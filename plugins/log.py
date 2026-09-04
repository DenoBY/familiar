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
import time

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
    push,
    push_target,
    unpushed_shas,
)
from modules.log.graph import NODE, build_graph
from modules.log.multi import extend_feeds, load_feeds, merge_feeds, page_size, relative_age
from modules.overlay import mark_overlay, restore_layout
from modules.text import pad, plural, short_path, truncate, wrap_text
from modules.vcs.screen import ReviewScreen, apply_result, run_screen
from modules.vcs.source import CommitSource, EmptySource
from modules.vcs.util import compose
from modules.vcs.workspace import Workspace, map_repos, open_workspace


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
_REPO_W = 14     # потолок колонки имени репозитория в общей ленте
_AGE_W = 4       # возраст коммита там же (2h, 4d, 3mo)
_MERGE_W = 2     # колонка значка мержа — своя, иначе съезжает хеш


def _fetch_report(results: list) -> str:
    """Итог веера fetch. Неудачные репозитории называем поимённо:
    молчаливое «fetched» на упавшей удалёнке хуже списка того, что не
    вышло.
    """
    failed = [repo.name or 'repository' for repo, err, exc in results if err or exc]
    done = len(results) - len(failed)
    if failed and not done:
        return 'fetch failed'
    if failed:
        return f'fetched {done}, failed: {", ".join(failed)}'
    return 'fetched' if len(results) == 1 else f'fetched {done} repositories'


def _commit_root(c: dict, ws: Workspace) -> str:
    return c.get('repo') or ws.single_root or ws.base


def _commit_key(c: dict) -> tuple:
    """Коммит как ключ: в мультирепо один sha ничего не значит без
    репозитория."""
    return c.get('repo'), c['sha']


class CommitLogHandler(ReviewScreen):

    QUIT_CONFIRM_MSG = 'Are you sure you want to close log?'

    def __init__(self, args: list[str], ws: Workspace) -> None:
        super().__init__(ws, EmptySource(ws))
        self.cli_args = args
        self.screen = 'commits'          # 'commits' → список; 'diff' → ревью коммита
        self.all_branches = False        # режим: HEAD (текущая ветка) ↔ все ветки (--all)
        self.all_commits: list[dict] = []
        self.commits: list[dict] = []
        self.graph: list[dict] = []      # раскладка лейнов графа для self.commits
        self.unpushed: dict[str, set[str]] = {}   # незапушенные sha по репозиторию
        self.feeds: list = []                     # ленты коммитов по репозиториям
        self._cols_cache: 'tuple[int, int] | None' = None
        self.show_graph = True           # рисовать граф веток слева (тумблер g)
        self.show_detail = True          # панель подробностей коммита справа (тумблер i)
        # ленивая подгрузка commit_detail, ключ (репозиторий, sha)
        self._detail_cache: dict[tuple, dict] = {}
        self._detail_later = None        # таймер отложенной подгрузки
        self._fetching = False
        self._pushing = False
        # (репозиторий, ветка, upstream|None, коммитов), ждёт «y»
        self.pending_push: 'tuple[str, str, str | None, int] | None' = None
        self.exhausted = False
        self.sel = 0
        self.offset = 0
        self.commit: 'dict | None' = None
        self.commit_filter = ''          # фильтр списка коммитов (не дерева файлов)
        self._annots_sha = ()            # к какому коммиту относятся комментарии

    # --- жизненный цикл ---

    def load_state(self) -> None:
        self.reload_commits()
        self.note_truncation()

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
            header += f'   ▸ {self._copy_rel(cur["path"], cur.get("repo"))}'
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
        root, branch, up, n = self.pending_push
        dest = up or f'origin/{branch} (new branch)'
        whose = f' from {self.ws.name_of(root)}' if self.ws.multi else ''
        return (f' push {plural(n, "commit")}{whose} to {dest}?'
                f'   y — yes   any other key — no')

    def _confirm_pending(self) -> None:
        root, branch, up, _ = self.pending_push
        self.pending_push = None
        self._pushing = True
        self.draw_screen()
        # сеть — как и fetch, уводим в фоновый поток, иначе UI замёрзнет
        self.run_background(lambda: push(root, branch, up is not None), self._push_done)

    def _cancel_pending(self) -> None:
        if self.pending_push is None:
            return
        self.pending_push = None
        self.flash = 'push cancelled'
        self.draw_screen()

    # --- список коммитов ---

    def reload_commits(self) -> None:
        repos = self.active_repos()
        if not repos:
            self.status = 'not a git repository'
            self.all_commits = self.commits = []
            self.unpushed = {}
            return
        # счётчики соседей не трогаем: в фокусе загружен один
        # репозиторий, а меню показывает ↑N по всем
        for r, shas, _err in map_repos(repos, lambda r: unpushed_shas(r.root)):
            self.unpushed[r.root] = shas or set()
        self.feeds, error = load_feeds(repos, self.all_branches, self._page())
        self._merge_feeds()
        # пустая история из-за ошибки git — показать её, а не
        # «no commits»
        self.status = '' if self.all_commits else (error or 'no commits')
        self.rebuild_commits()

    def _page(self) -> int:
        return page_size(len(self.active_repos()), BATCH)

    def _merge_feeds(self) -> None:
        self.all_commits, self.exhausted = merge_feeds(self.feeds)
        self._cols_cache = None

    def load_more(self) -> None:
        if self.exhausted or self.commit_filter:
            return
        before = len(self.all_commits)
        self.feeds = extend_feeds(self.feeds, self.all_branches, self._page())
        self._merge_feeds()
        if len(self.all_commits) != before:
            self.rebuild_commits()

    def rebuild_commits(self) -> None:
        q = self.commit_filter.lower()
        if q:
            self.commits = [c for c in self.all_commits if self._commit_matches(c, q)]
        else:
            self.commits = list(self.all_commits)
        # граф лейнов строится по одной истории: поверх нескольких
        # репозиториев общего DAG нет
        self.graph = build_graph(self.commits) if self._graph_ready() else []
        self.sel = min(self.sel, max(0, len(self.commits) - 1))
        self._schedule_detail()

    def _commit_matches(self, c: dict, q: str) -> bool:
        return (q in c['subject'].lower() or q in c['short'].lower()
                or q in c['author'].lower() or q in c.get('repo_name', '').lower())

    def _is_unpushed(self, c: dict) -> bool:
        return c['sha'] in self.unpushed.get(_commit_root(c, self.ws), ())

    @property
    def merged_feed(self) -> bool:
        """Показана ли общая лента нескольких репозиториев: у неё своя
        строка (имя репозитория и возраст) и нет графа — общего DAG
        поверх независимых историй не существует.

        Фильтр списка сюда не входит: он оставляет от истории
        произвольный кусок, и лейны по нему легли бы мимо.
        """
        return self.ws.multi and not self.repo_focus

    def _graph_ready(self) -> bool:
        return not self.merged_feed

    def toggle_graph(self) -> None:
        if not self._graph_ready():
            self.flash = 'graph needs a single repo (R)'
            self.draw_screen()
            return
        self.show_graph = not self.show_graph
        self.draw_screen()

    def _graph_gutter(self, i: int, gw: int) -> str:
        """Цветной граф-гаттер строки коммита i, добитый
        пробелами до ширины gw.
        """
        cells = self.graph[i]['cells'] if i < len(self.graph) else []
        unpushed = i < len(self.commits) and self._is_unpushed(self.commits[i])
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
        repos = self.active_repos()
        if not repos or self._fetching:
            return
        self._fetching = True
        self.draw_screen()
        self.run_background(lambda: map_repos(repos, lambda r: fetch(r.root)),
                            self._fetch_done)

    def _fetch_done(self, results: list) -> None:
        self._fetching = False
        self._detail_cache = {}          # ветки/содержимое могли измениться
        self.sel = 0
        self.offset = 0
        self.reload_commits()
        self.flash = _fetch_report(results)
        self.draw_screen()

    def start_push(self) -> None:
        """Спросить подтверждение: push публикует коммиты в удалёнку,
        промах по клавише не должен этого делать.
        """
        root = self._selected_root()
        if not root or self._pushing or self._fetching:
            return
        target = push_target(root)
        if target is None:
            self.flash = 'nothing to push'
            self.draw_screen()
            return
        self.pending_push = (root, *target)
        self.draw_screen()

    def _selected_root(self) -> 'str | None':
        """Репозиторий выбранного коммита: push публикует историю, и
        гадать, чью именно, нельзя.
        """
        if self.commits and 0 <= self.sel < len(self.commits):
            return _commit_root(self.commits[self.sel], self.ws)
        return self.root

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
        if self.annots and self._annots_sha != _commit_key(c):
            self.annots = {}
            self.flash = 'comments cleared (another commit)'
        self._annots_sha = _commit_key(c)
        self.commit = c
        self.screen = 'diff'
        # у коммита всегда один репозиторий — экран ревью про
        # мультирепо ничего не знает; база рабочей области прежняя,
        # иначе @-ссылки потеряли бы имя репозитория в пути
        self.set_source(CommitSource(self.ws.sub(_commit_root(c, self.ws)), c['sha']))
        self.draw_screen()

    def _repo_focus_changed(self) -> None:
        if self.screen != 'commits':
            super()._repo_focus_changed()
            return
        self.sel = 0
        self.offset = 0
        self.reload_commits()
        self.draw_screen()

    def _repo_summary(self, repo) -> str:
        unpushed = len(self.unpushed.get(repo.root, ()))
        mark = f'↑{unpushed}' if unpushed else ''
        if self.repo_focus and repo.root != self.repo_focus:
            # история соседей сейчас не загружена: «0 commits» сказало
            # бы не то, что есть на самом деле
            return mark
        n = sum(1 for c in self.all_commits if c.get('repo') == repo.root)
        return f'{plural(n, "commit")}' + (f'   {mark}' if mark else '')

    def back_to_commits(self) -> None:
        self.screen = 'commits'
        self.commit = None
        # источник коммита держал один репозиторий: оставь его — и
        # список коммитов считал бы себя однорепозиторным (R, футер)
        self.set_source(EmptySource(self.ws))
        self.draw_screen()

    # --- отрисовка ---

    def _draw_frame(self) -> None:
        if self.screen != 'commits':
            super()._draw_frame()
            return
        if self.draw_quit_confirm():
            return
        if self._repo_menu is not None:
            self.draw_repo_menu()
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
        mode = self._commits_mode()
        # «+» — загружена лишь пачка (BATCH), докрутка подтянет ещё:
        # иначе счётчик читается как «в ветке всего столько коммитов»
        more = '' if self.exhausted else '+'
        header = f' {self._commits_scope()} · {mode} ({len(self.commits)}'
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

    def _commits_mode(self) -> str:
        if self.all_branches:
            return 'all branches'
        return 'current branches' if len(self.active_repos()) > 1 else 'current branch'

    def _commits_scope(self) -> str:
        base = short_path(self.ws.base)
        if self.repo_focus:
            return f'{base} › {self.focus_name()}'
        if self.ws.multi:
            return f'{base} · {plural(len(self.ws.repos), "repo")}'
        return base

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
        c = self.commits[self.sel]
        if _commit_key(c) not in self._detail_cache:
            self._detail_later = self.asyncio_loop.call_later(
                DETAIL_DELAY, self._load_detail, c)

    def _load_detail(self, c: dict) -> None:
        self._detail_later = None
        self._detail_cache[_commit_key(c)] = commit_detail(_commit_root(c, self.ws),
                                                           c['sha'])
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
        d = self._detail_cache.get(_commit_key(c))
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

    def _multi_commit_row(self, c: dict, width: int, selected: bool) -> str:
        """Строка ленты нескольких репозиториев: слева имя репозитория
        и возраст — абсолютная дата в фикс-колонке съела бы место, а
        сравнивают здесь именно свежесть.

        Все колонки фиксированной ширины, значок мержа — своя: припиши
        его к хешу, и на строках мержа съехала бы вся таблица.
        """
        repo_w, sha_w = self._columns()
        name = f'{truncate(c.get("repo_name", ""), repo_w):<{repo_w}}'
        badge = f'{"⑂" if c.get("merge") else "":<{_MERGE_W}}'
        sha = f'{c["short"]:<{sha_w}}'
        age = f'{truncate(relative_age(c["ts"], time.time()), _AGE_W):>{_AGE_W}}'
        head = f'{name}  {badge}{sha}  {age}  '
        subject = truncate(c['subject'], max(1, width - len(head)))
        if selected:
            return styled(pad(head + subject, width), reverse=True)
        segs = [(f'{name}  ', self._repo_style(c)), (badge, {'fg': 'magenta'}),
                (f'{sha}  ', {'fg': 'cyan'}), (f'{age}  ', {'fg': 'gray'}),
                (subject, {})]
        return compose(segs, width)

    def _columns(self) -> tuple[int, int]:
        """Ширина колонок имени репозитория и хеша: git отдаёт хеш
        минимальной уникальной длины, и она гуляет от репозитория к
        репозиторию.
        """
        if self._cols_cache is None:
            names = [r.name for r in self.ws.repos] or ['']
            shas = [c['short'] for c in self.all_commits] or ['']
            self._cols_cache = (min(_REPO_W, max(len(n) for n in names)),
                                max(len(x) for x in shas))
        return self._cols_cache

    def _repo_style(self, c: dict) -> dict:
        """Цвет имени репозитория — стабильный по его месту в списке:
        лейнов графа в этом режиме нет, палитра свободна.
        """
        names = [r.name for r in self.ws.repos]
        i = names.index(c['repo_name']) if c.get('repo_name') in names else 0
        return {'fg': _GRAPH_COLORS[i % len(_GRAPH_COLORS)]}

    def _commit_row(self, c: dict, width: int, selected: bool) -> str:
        if self.merged_feed:
            return self._multi_commit_row(c, width, selected)
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
        push_hint = ' · p push' if any(self.unpushed.values()) else ''
        fetch_hint = 'f fetch all' if len(self.active_repos()) > 1 else 'f fetch'
        repo_hint = self._repo_hint()
        graph = '' if not self._graph_ready() else f' · {graph}'
        return (f' [log]  ↑↓ commit · Enter/→ open · ⌘c hash{repo_hint}'
                f' · {fetch_hint}{push_hint} · {mode}{graph} · {info}'
                f' · / filter · q quit')

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
        if self._repo_menu is not None:
            if key_event.key == 'ESCAPE':
                self.close_repo_menu()
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
            # каскад: фильтр → фокус репозитория → дно
            if self.commit_filter:
                self._input_cancelled('commits')
                self.draw_screen()
            elif self.clear_repo_focus():
                pass
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
        if self.repo_menu_text(text[:1]):
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
            elif c == 'R' and self.ws.multi:
                self.open_repo_menu()
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
    return run_screen(CommitLogHandler(args, open_workspace(os.getcwd())))


@result_handler()
def handle_result(args: list[str], answer: 'dict | None',
                  target_window_id: int, boss) -> None:
    restore_layout(boss, target_window_id)
    apply_result(answer, target_window_id, boss)


if __name__ == '__main__':
    main(sys.argv)
