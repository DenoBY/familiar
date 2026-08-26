#!/usr/bin/env python3
"""
log — kitten для kitty.

Оверлей просмотра истории git: экран списка коммитов (текущая
ветка или все ветки) и по выбранному коммиту — полноценное ревью
его изменений.

Экран ревью — общий класс modules.vcs.screen.ReviewScreen, тот же,
что показывает незакоммиченные правки в review: дерево файлов и
дифф, комментарии к строкам, go-to-definition, Find in Files, метки,
редактор. Отличие только в источнике данных (modules.vcs.source:
снимок коммита вместо рабочего дерева), поэтому поиск и прыжки к
определениям идут по состоянию на момент коммита. Здесь — сам
список коммитов, граф веток и работа с удалёнками.

Подключение в ~/.config/kitty/kitty.conf:
    map cmd+shift+l kitten /Users/deno/Projects/kitty/plugins/log.py
"""

import os
import sys

from kittens.tui.handler import Handler, result_handler
from kittens.tui.loop import MouseButton
from kittens.tui.operations import styled
from kitty.key_encoding import EventType


if '__file__' in globals():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.keylayout import chord, to_latin
from modules.log.git import (
    commit_detail,
    display_refs,
    fetch,
    load_commits,
    push,
    push_target,
    unpushed_shas,
)
from modules.log.graph import NODE, build_graph
from modules.overlay import mark_overlay, restore_layout
from modules.text import pad, plural, short_path, truncate, wrap_text
from modules.vcs.git import git_root, last_error
from modules.vcs.screen import ReviewScreen, apply_result, run_screen
from modules.vcs.source import CommitSource, EmptySource
from modules.vcs.util import compose


BATCH = 300   # сколько коммитов тянем за раз (докрутка подгружает следующую пачку)
# Пауза в прокрутке, после которой подтягиваются подробности коммита.
# Тот же порядок, что у отложенной загрузки диффа в дереве файлов.
DETAIL_DELAY = 0.08

# Цвет ref-меток в списке коммитов по типу
# (см. modules.log.git.parse_refs).
_REF_STYLE = {'head': {'fg': 'cyan', 'bold': True}, 'branch': {'fg': 'green'},
              'remote': {'fg': 'blue'}, 'tag': {'fg': 'yellow'}}

# Палитра лейнов графа веток (лейн 0 — золотой основной ствол,
# как в IDE).
_GRAPH_COLORS = ['yellow', 'magenta', 'blue', 'green', 'cyan', 'red']

# Узел незапушенного коммита. 256-цвет, а не имя 'green':
# ANSI-green темы бывает оливковым (#b5bd68) и сливается с
# лейнами — берём настоящий зелёный.
_UNPUSHED_COLOR = 77

_AUTHOR_W = 12   # фикс-колонка автора (справа) — чтобы строки выравнивались
_DATE_W = 15     # фикс-колонка даты (справа)


class CommitLogHandler(ReviewScreen):

    QUIT_CONFIRM_MSG = 'Are you sure you want to close log?'

    def __init__(self, args: list[str], root: 'str | None') -> None:
        super().__init__(root, EmptySource(root))
        self.cli_args = args
        self.screen = 'commits'          # 'commits' → список; 'diff' → ревью коммита
        self.all_branches = False        # режим: HEAD (текущая ветка) ↔ все ветки (--all)
        self.all_commits: list[dict] = []
        self.commits: list[dict] = []
        self.graph: list[dict] = []      # раскладка лейнов графа для self.commits
        self.unpushed: set[str] = set()
        self.show_graph = True           # рисовать граф веток слева (тумблер g)
        self.show_detail = True          # панель подробностей коммита справа (тумблер i)
        self._detail_cache: dict[str, dict] = {}   # ленивая подгрузка commit_detail
        self._detail_later = None        # таймер отложенной подгрузки
        self._fetching = False
        self._pushing = False
        # (ветка, upstream|None, коммитов), ждёт «y»
        self.pending_push: 'tuple[str, str | None, int] | None' = None
        self.exhausted = False
        self.sel = 0
        self.offset = 0
        self.commit: 'dict | None' = None
        self.commit_filter = ''          # фильтр списка коммитов (не дерева файлов)
        self._annots_sha = ''            # к какому коммиту относятся комментарии

    # --- жизненный цикл ---

    def load_state(self) -> None:
        self.reload_commits()

    # --- хуки экрана ревью ---

    def _header(self) -> str:
        c = self.commit
        badge = '⑂ ' if c.get('merge') else ''
        refs = ''.join(f'{name} ' for name, _ in display_refs(c.get('refs') or []))
        header = f' {badge}{c["short"]} · {refs}{truncate(c["subject"], 60)}'
        cur = self.current_item()
        if self._external:
            header += f'   ▸ {self._external} (read-only)'
        elif cur:
            header += f'   ▸ {cur["path"]}'
        return header

    def _annot_title(self) -> str:
        c = self.commit
        return f'# Review comments — {c["short"]} {c["subject"]}' if c else super()._annot_title()

    def _escape_bottom(self) -> None:
        self.back_to_commits()   # дно каскада ревью — назад к списку

    def _back_hint(self) -> str:
        return ' · Esc commits'

    def _host_key(self, k: str) -> bool:
        if k == 'LEFT':
            self.back_to_commits()   # каскад общего разбора исчерпан
            return True
        return False

    def _pending_active(self) -> bool:
        return self.pending_push is not None

    def _pending_prompt(self) -> str:
        branch, up, n = self.pending_push
        dest = up or f'origin/{branch} (new branch)'
        return f' push {plural(n, "commit")} to {dest}?   y — yes   any other key — no'

    def _confirm_pending(self) -> None:
        branch, up, _ = self.pending_push
        self.pending_push = None
        self._pushing = True
        self.draw_screen()
        # сеть — как и fetch, уводим в фоновый поток, иначе UI замёрзнет
        self.run_background(lambda: push(self.root, branch, up is not None), self._push_done)

    def _cancel_pending(self) -> None:
        if self.pending_push is None:
            return
        self.pending_push = None
        self.flash = 'push cancelled'
        self.draw_screen()

    # --- список коммитов ---

    def reload_commits(self) -> None:
        if not self.root:
            self.status = 'not a git repository'
            self.all_commits = self.commits = []
            self.unpushed = set()
            return
        self.unpushed = unpushed_shas(self.root)
        self.all_commits = load_commits(self.root, self.all_branches, BATCH)
        self.exhausted = len(self.all_commits) < BATCH
        # пустая история из-за ошибки git — показать её, а не
        # «no commits»
        self.status = '' if self.all_commits else (last_error() or 'no commits')
        self.rebuild_commits()

    def load_more(self) -> None:
        if self.exhausted or self.commit_filter:
            return
        more = load_commits(self.root, self.all_branches, BATCH, len(self.all_commits))
        if len(more) < BATCH:
            self.exhausted = True
        if more:
            self.all_commits.extend(more)
            self.rebuild_commits()

    def rebuild_commits(self) -> None:
        q = self.commit_filter.lower()
        if q:
            self.commits = [c for c in self.all_commits
                            if q in c['subject'].lower() or q in c['short'].lower()
                            or q in c['author'].lower()]
        else:
            self.commits = list(self.all_commits)
        self.graph = build_graph(self.commits)
        self.sel = min(self.sel, max(0, len(self.commits) - 1))
        self._schedule_detail()

    def toggle_graph(self) -> None:
        self.show_graph = not self.show_graph
        self.draw_screen()

    def _graph_gutter(self, i: int, gw: int) -> str:
        """Цветной граф-гаттер строки коммита i, добитый
        пробелами до ширины gw.
        """
        cells = self.graph[i]['cells'] if i < len(self.graph) else []
        unpushed = (i < len(self.commits)
                    and self.commits[i]['sha'] in self.unpushed)
        out = ''
        for glyph, color in cells:
            if glyph == ' ':
                out += ' '
            elif glyph == NODE and unpushed:
                out += styled(glyph, fg=_UNPUSHED_COLOR, bold=True)
            else:
                out += styled(glyph, fg=_GRAPH_COLORS[color % len(_GRAPH_COLORS)])
        return out + ' ' * max(0, gw - len(cells))

    def toggle_mode(self) -> None:
        self.all_branches = not self.all_branches
        self.sel = 0
        self.offset = 0
        self.reload_commits()
        self.draw_screen()

    def do_fetch(self) -> None:
        """Подтянуть изменения с удалёнок и перечитать список.

        git fetch — сеть до минуты; в колбэке ждать нельзя (UI и
        Ctrl+C замёрзнут), поэтому работа уходит в фоновый поток, а
        результат возвращается в event loop.
        """
        if not self.root or self._fetching:
            return
        self._fetching = True
        self.draw_screen()
        self.run_background(lambda: fetch(self.root), self._fetch_done)

    def _fetch_done(self, err: 'str | None') -> None:
        self._fetching = False
        self._detail_cache = {}          # ветки/содержимое могли измениться
        self.sel = 0
        self.offset = 0
        self.reload_commits()
        self.flash = 'fetched' if err is None else 'fetch failed'
        self.draw_screen()

    def start_push(self) -> None:
        """Спросить подтверждение: push публикует коммиты в удалёнку,
        промах по клавише не должен этого делать.
        """
        if not self.root or self._pushing or self._fetching:
            return
        target = push_target(self.root)
        if target is None:
            self.flash = 'nothing to push'
            self.draw_screen()
            return
        self.pending_push = target
        self.draw_screen()

    def _push_done(self, err: 'str | None') -> None:
        self._pushing = False
        self.flash = 'pushed' if err is None else f'push failed: {err}'
        if err is None:
            self.reload_commits()   # ref-метки уехали, узлы графа больше не «свои»
        self.draw_screen()

    def move(self, delta: int) -> None:
        if not self.commits:
            return
        self.sel = max(0, min(len(self.commits) - 1, self.sel + delta))
        if self.sel >= len(self.commits) - 1:
            self.load_more()
        self._schedule_detail()
        self.schedule_draw()

    def ensure_commit_visible(self) -> None:
        vis = self.visible_rows()
        if self.sel < self.offset:
            self.offset = self.sel
        elif self.sel >= self.offset + vis:
            self.offset = self.sel - vis + 1

    def open_commit(self) -> None:
        if not self.commits or not (0 <= self.sel < len(self.commits)):
            return
        c = self.commits[self.sel]
        # комментарии принадлежат разобранному коммиту: перенести их на
        # чужие строки было бы хуже, чем честно сказать, что их нет
        if self.annots and self._annots_sha != c['sha']:
            self.annots = {}
            self.flash = 'comments cleared (another commit)'
        self._annots_sha = c['sha']
        self.commit = c
        self.screen = 'diff'
        self.set_source(CommitSource(self.root, c['sha']))
        self.draw_screen()

    def back_to_commits(self) -> None:
        self.screen = 'commits'
        self.commit = None
        self.draw_screen()

    # --- отрисовка ---

    def _draw_frame(self) -> None:
        if self.screen != 'commits':
            super()._draw_frame()
            return
        if self.draw_quit_confirm():
            return
        self.cmd.clear_screen()
        self._draw_commits()
        self._draw_input_line()
        danger = self.pending_push is not None
        foot_fg = 'red' if danger else ('green' if self.flash else 'gray')
        self.print(styled(truncate(self._commits_footer(), self.screen_size.cols),
                          fg=foot_fg, bold=danger), end='')
        self.flash = ''

    def _draw_commits(self) -> None:
        cols = self.screen_size.cols
        mode = 'all branches' if self.all_branches else 'current branch'
        # «+» — загружена лишь пачка (BATCH), докрутка подтянет ещё:
        # иначе счётчик читается как «в ветке всего столько коммитов»
        more = '' if self.exhausted else '+'
        header = f' {short_path(self.root or os.getcwd())} · {mode} ({len(self.commits)}'
        header += (f'/{len(self.all_commits)}{more})' if self.commit_filter
                   else f'{more})')
        self.print(styled(truncate(header, cols), fg='green', bold=True))
        self.print(styled('─' * cols, fg='gray'))
        vis = self.visible_rows()
        self.ensure_commit_visible()
        if not self.commits:
            self.print(styled('  ' + (self.status or 'no matches'), fg='gray'))
            for _ in range(vis - 1):
                self.print()
            return
        panel = self.show_detail and cols >= 90
        panel_w = min(52, cols // 3) if panel else 0
        list_w = cols - (panel_w + 3 if panel else 0)
        end = min(self.offset + vis, len(self.commits))
        # ширина графа — максимум по ВИДИМЫМ строкам, а не
        # глобальный: на линейных экранах граф прижат к тексту,
        # на мержах — расширяется ровно сколько нужно
        if self.show_graph:
            gw = max((len(self.graph[i]['cells']) for i in range(self.offset, end)
                      if i < len(self.graph)), default=0)
        else:
            gw = 0
        detail = self._detail_lines(panel_w) if panel else []
        sep = styled(' │ ', fg='gray')
        for r in range(vis):
            i = self.offset + r
            if i < len(self.commits):
                row = self._commit_row(self.commits[i], list_w - (gw + 1 if gw else 0),
                                       i == self.sel)
                left = (self._graph_gutter(i, gw) + ' ' + row) if gw else row
            else:
                left = ' ' * list_w
            if panel:
                self.print(left + sep + (detail[r] if r < len(detail) else ''))
            else:
                self.print(left)

    def _schedule_detail(self) -> None:
        """Подтянуть подробности выбранного коммита, когда прокрутка
        утихнет.

        commit_detail() — два git-вызова, и `branch --contains` среди
        них обходит все ветки репозитория. На каждый шаг курсора это
        заметно: при зажатой стрелке список отвечает рывками. Зовётся
        при смене выделения, а не из отрисовки — рендер не должен
        ходить в git.
        """
        if self._detail_later is not None:
            self._detail_later.cancel()
            self._detail_later = None
        if not self.commits or not (0 <= self.sel < len(self.commits)):
            return
        sha = self.commits[self.sel]['sha']
        if sha not in self._detail_cache:
            self._detail_later = self.asyncio_loop.call_later(
                DETAIL_DELAY, self._load_detail, sha)

    def _load_detail(self, sha: str) -> None:
        self._detail_later = None
        self._detail_cache[sha] = commit_detail(self.root, sha)
        self.draw_screen()

    def _detail_lines_brief(self, c: dict, width: int) -> list[str]:
        """Панель на время прокрутки: только то, что уже есть в списке
        коммитов, без обращений к git. Показывает то же, что и полная
        панель, минус тело сообщения, email и ветки, — чтобы при
        подгрузке она не прыгала.
        """
        out = [styled(truncate(w, width), bold=True) for w in wrap_text(c['subject'], width)]
        out.append('')
        out.append(styled(truncate(f'{c["short"]}  {c["author"]}', width), fg='cyan'))
        out.append(styled(truncate(c['date'], width), fg='gray'))
        return out

    def _detail_lines(self, width: int) -> list[str]:
        """Строки правой панели: подробности выбранного коммита
        (как в IDE).
        """
        if not self.commits or not (0 <= self.sel < len(self.commits)):
            return []
        c = self.commits[self.sel]
        d = self._detail_cache.get(c['sha'])
        if d is None:
            return self._detail_lines_brief(c, width)
        out = []
        msg = (d['body'] or c['subject']).split('\n')
        for i, ml in enumerate(msg):
            for w in wrap_text(ml, width):
                out.append(styled(truncate(w, width), bold=(i == 0)))
        out.append('')
        out.append(styled(truncate(f'{c["short"]}  {c["author"]}', width), fg='cyan'))
        if d['author_email']:
            out.append(styled(truncate(f'<{d["author_email"]}>', width), fg='gray'))
        out.append(styled(truncate(c['date'], width), fg='gray'))
        if d['committer'] and (d['committer'] != c['author']
                               or d['committer_email'] != d['author_email']):
            out.append(styled(truncate(f'committed by {d["committer"]}', width), fg='gray'))
        if d['branches']:
            out.append('')
            out.append(styled(truncate(f'In {len(d["branches"])} branches:', width),
                              fg='green'))
            for b in d['branches']:
                out.append(styled(truncate('  ' + b, width), fg='gray'))
        return out

    def _commit_row(self, c: dict, width: int, selected: bool) -> str:
        badge = '⑂ ' if c.get('merge') else ''
        refs = display_refs(c.get('refs') or [])
        refs_plain = '  '.join(name for name, _ in refs)
        author = truncate(c['author'], _AUTHOR_W)
        date = truncate(c['date'], _DATE_W)
        # автор и дата — фикс-колонки у правого края (строки
        # выравниваются); ветки/теги — правее subject, вплотную
        # к колонке автора
        tail_plain = f'{author:<{_AUTHOR_W}}  {date:<{_DATE_W}}'
        left_w = max(1, width - len(tail_plain) - 1)
        head = f'{badge}{c["short"]}  '
        subj_max = left_w - len(head) - len(refs_plain) - 2   # ≥2 пробела перед refs
        subject = truncate(c['subject'], max(1, subj_max))
        gap = max(1, left_w - len(head) - len(subject) - len(refs_plain))
        if selected:
            plain = head + subject + ' ' * gap + refs_plain + ' ' + tail_plain
            return styled(pad(plain, width), reverse=True)
        segs = [(badge, {'fg': 'magenta'}), (f'{c["short"]}  ', {'fg': 'cyan'}),
                (subject, {}), (' ' * gap, None)]
        for i, (name, kind) in enumerate(refs):
            segs.append((name + ('  ' if i < len(refs) - 1 else ''),
                         _REF_STYLE.get(kind, {'fg': 'green'})))
        segs += [(' ', None), (f'{author:<{_AUTHOR_W}}', {'bold': True}),
                 ('  ', None), (f'{date:<{_DATE_W}}', {'fg': 'gray'})]
        return compose(segs, width)

    def _commits_footer(self) -> str:
        if self.pending_push:
            return self._pending_prompt()
        if self.input_mode:
            return ' Enter — keep   Esc — clear'
        if self.flash:
            return ' ' + self.flash
        if self._fetching:
            return ' fetching…'
        if self._pushing:
            return ' pushing…'
        # в футере — действие по клавише, а не текущий режим
        # (он и так виден в шапке)
        mode = 'a current branch' if self.all_branches else 'a all branches'
        graph = 'g graph off' if self.show_graph else 'g graph on'
        info = 'i info off' if self.show_detail else 'i info on'
        push_hint = ' · p push' if self.unpushed else ''
        return (f' [log]  ↑↓ commit · Enter/→ open · ⌘c hash · f fetch{push_hint} · {mode}'
                f' · {graph} · {info} · / filter · q quit')

    # --- фильтр списка коммитов (у дерева файлов свой) ---

    def start_commit_filter(self) -> None:
        self.start_input('commits', self.commit_filter)

    def _input_live(self) -> None:
        if self.input_mode == 'commits':
            self.commit_filter = self.input_buffer
            self.sel = 0
            self.rebuild_commits()
            self.draw_screen()
            return
        super()._input_live()

    def _input_cancelled(self, mode: str) -> None:
        if mode == 'commits':
            self.commit_filter = ''
            self.sel = 0
            self.rebuild_commits()
            return
        super()._input_cancelled(mode)

    # --- клавиатура ---

    def on_key(self, key_event) -> None:
        if key_event.type == EventType.RELEASE:
            return
        if self.screen != 'commits':
            super().on_key(key_event)
            return
        if self.confirm_key(key_event):
            return
        if chord(key_event, 'ctrl', 'c'):
            self.quit_loop(0)
            return
        if self.pending_push:
            # печатаемое (в т.ч. сам «y») разбирает on_text; здесь
            # гасим Enter/стрелки/Esc: публикация не должна
            # подтверждаться ничем, кроме явного «y»
            if not getattr(key_event, 'text', ''):
                self._cancel_pending()
            return
        k = key_event.key
        if self.input_key(k):
            return
        if chord(key_event, 'super', 'f'):
            self.start_commit_filter()
            return
        if chord(key_event, 'super', 'c'):
            self.copy_commit()
            return
        self._commits_key(k)

    def copy_commit(self) -> None:
        if not self.commits or not (0 <= self.sel < len(self.commits)):
            return
        c = self.commits[self.sel]
        self._copy_clipboard(c['sha'])
        self.flash = f'copied {c["short"]}'
        self.draw_screen()

    def _commits_key(self, k: str) -> None:
        if k == 'UP':
            self.move(-1)
        elif k == 'DOWN':
            self.move(1)
        elif k == 'PAGE_UP':
            self.move(-self.visible_rows())
        elif k == 'PAGE_DOWN':
            self.move(self.visible_rows())
        elif k == 'HOME':
            self.sel = 0
            self._schedule_detail()
            self.draw_screen()
        elif k == 'END':
            self.sel = max(0, len(self.commits) - 1)
            self._schedule_detail()
            self.draw_screen()
        elif k in ('ENTER', 'RIGHT'):
            self.open_commit()
        elif k == 'ESCAPE':
            if self.commit_filter:
                self._input_cancelled('commits')
                self.draw_screen()
            else:
                # дно каскада: вместо тихого выхода — подтверждение
                self.start_quit_confirm()

    def on_text(self, text: str, in_bracketed_paste: bool = False) -> None:
        if self.screen != 'commits':
            super().on_text(text, in_bracketed_paste)
            return
        if self.confirm_text(text, in_bracketed_paste):
            return
        if self.pending_push:
            if to_latin(text[:1]) in ('y', 'Y'):
                self._confirm_pending()
            else:
                self._cancel_pending()
            return
        if self.input_text(text):
            return
        for ch in text:
            c = to_latin(ch)
            if c in ('q', 'Q'):
                self.quit_loop(0)
                return
            if c == '/':
                self.start_commit_filter()
            elif c in ('a', 'A'):
                self.toggle_mode()
            elif c in ('g', 'G'):
                self.toggle_graph()
            elif c in ('i', 'I'):
                self.show_detail = not self.show_detail
                self.draw_screen()
            elif c in ('f', 'F'):
                self.do_fetch()
            elif c in ('p', 'P'):
                self.start_push()

    # --- мышь: список коммитов сам, ревью — общий экран ---

    def _pointer_for(self, ev) -> 'str | None':
        # На списке коммитов зон диффа нет — база искала бы их по
        # координатам дерева и врала бы формой курсора.
        if self.screen == 'commits':
            return None
        return super()._pointer_for(ev)

    def _on_mouse(self, ev) -> None:
        if self.screen != 'commits':
            super()._on_mouse(ev)
            return
        self.update_pointer(ev)
        if ev.buttons in (MouseButton.WHEEL_UP, MouseButton.WHEEL_DOWN):
            self.move(-1 if ev.buttons == MouseButton.WHEEL_UP else 1)
            return
        Handler.on_mouse_event(self, ev)   # обычный клик → on_click

    def on_click(self, ev) -> None:
        if self.input_mode:
            return
        if self.screen != 'commits':
            super().on_click(ev)
            return
        r = ev.cell_y - 2
        if not (0 <= r < self.visible_rows()):
            return
        i = self.offset + r
        if i >= len(self.commits):
            return
        if i == self.sel:
            self.open_commit()
        else:
            self.sel = i
            self._schedule_detail()
            self.draw_screen()

    def on_resize(self, new_size) -> None:
        if self.screen == 'diff':
            self.build_diff_rows()
        self.draw_screen()

    def on_eot(self) -> None:
        super().on_eot()   # ⌃d не закрывает кит ни на одном экране


def main(args: list[str]) -> dict:
    mark_overlay('log')
    return run_screen(CommitLogHandler(args, git_root(os.getcwd())))


@result_handler()
def handle_result(args: list[str], answer: 'dict | None',
                  target_window_id: int, boss) -> None:
    restore_layout(boss, target_window_id)
    apply_result(answer, target_window_id, boss)


if __name__ == '__main__':
    main(sys.argv)
