#!/usr/bin/env python3
"""
log — kitten для kitty.

Оверлей просмотра истории git: экран списка коммитов (текущая ветка или все ветки) и
по выбранному коммиту — двухпанельный просмотр его изменений (дерево файлов + unified
diff), как в cc-review, с подсветкой, поиском и копированием в буфер для вставки в промт.

Двухпанельная diff-механика — общий базовый класс modules.vcs.view.DiffTreeView; здесь
только экран списка коммитов и подключение git-слоя коммитов (modules.log.git).

Подключение в ~/.config/kitty/kitty.conf:
    map cmd+shift+l kitten /Users/deno/Projects/kitty/plugins/log.py
"""

import os
import sys

from kittens.tui.handler import Handler
from kittens.tui.loop import Loop, MouseButton
from kittens.tui.operations import styled
from kitty.key_encoding import EventType


if '__file__' in globals():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.log.git import (
    commit_contents,
    commit_detail,
    commit_files,
    display_refs,
    fetch,
    first_parent,
    load_commits,
    unpushed_shas,
)
from modules.log.graph import NODE, build_graph
from modules.overlay import mark_overlay
from modules.text import wrap_text
from modules.vcs.git import git_root, last_error
from modules.vcs.util import compose, pad, short_path, to_latin, truncate
from modules.vcs.view import DiffTreeView


BATCH = 300   # сколько коммитов тянем за раз (докрутка подгружает следующую пачку)

# Цвет ref-меток в списке коммитов по типу (см. modules.log.git.parse_refs).
_REF_STYLE = {'head': {'fg': 'cyan', 'bold': True}, 'branch': {'fg': 'green'},
              'remote': {'fg': 'blue'}, 'tag': {'fg': 'yellow'}}

# Палитра лейнов графа веток (лейн 0 — золотой основной ствол, как в IDE).
_GRAPH_COLORS = ['yellow', 'magenta', 'blue', 'green', 'cyan', 'red']

# Узел незапушенного коммита. 256-цвет, а не имя 'green': ANSI-green темы бывает
# оливковым (#b5bd68) и сливается с лейнами — берём настоящий зелёный.
_UNPUSHED_COLOR = 77

_AUTHOR_W = 12   # фикс-колонка автора (справа) — чтобы строки выравнивались
_DATE_W = 15     # фикс-колонка даты (справа)


class CommitLogHandler(DiffTreeView):

    def __init__(self, args: list, root: 'str | None') -> None:
        super().__init__(root)
        self.cli_args = args
        self.screen = 'commits'          # 'commits' → список; 'diff' → изменения коммита
        self.all_branches = False        # режим: HEAD (текущая ветка) ↔ все ветки (--all)
        self.all_commits = []
        self.commits = []
        self.graph = []                  # раскладка лейнов графа для self.commits
        self.unpushed = set()
        self.show_graph = True           # рисовать граф веток слева (тумблер g)
        self.show_detail = True          # панель подробностей коммита справа (тумблер i)
        self._detail_cache = {}          # sha -> commit_detail (ленивая подгрузка)
        self._fetching = False
        self.exhausted = False
        self.sel = 0                     # выбранный коммит
        self.offset = 0                  # скролл списка коммитов
        self.commit = None               # открытый коммит (на экране diff)
        self._parent = None              # первый родитель открытого коммита (кэш)
        self.filter_query = ''

    # --- хуки DiffTreeView ---

    def _contents(self, it):
        return commit_contents(self.root, self.commit['sha'], it, self._parent)

    def _empty_pane_msg(self):
        return 'no file changes'

    # --- жизненный цикл ---

    def initialize(self):
        self.cmd.set_cursor_visible(False)
        self.reload_commits()
        self.draw_screen()

    def finalize(self):
        self.cmd.set_cursor_visible(True)

    # --- список коммитов ---

    def reload_commits(self):
        if not self.root:
            self.status = 'not a git repository'
            self.all_commits = self.commits = []
            self.unpushed = set()
            return
        self.unpushed = unpushed_shas(self.root)
        self.all_commits = load_commits(self.root, self.all_branches, BATCH)
        self.exhausted = len(self.all_commits) < BATCH
        # пустая история из-за ошибки git — показать её, а не «no commits»
        self.status = '' if self.all_commits else (last_error() or 'no commits')
        self.rebuild_commits()

    def load_more(self):
        if self.exhausted or self.filter_query:
            return
        more = load_commits(self.root, self.all_branches, BATCH, len(self.all_commits))
        if len(more) < BATCH:
            self.exhausted = True
        if more:
            self.all_commits.extend(more)
            self.rebuild_commits()

    def rebuild_commits(self):
        q = self.filter_query.lower()
        if q:
            self.commits = [c for c in self.all_commits
                            if q in c['subject'].lower() or q in c['short'].lower()
                            or q in c['author'].lower()]
        else:
            self.commits = list(self.all_commits)
        self.graph = build_graph(self.commits)
        self.sel = min(self.sel, max(0, len(self.commits) - 1))

    def toggle_graph(self):
        self.show_graph = not self.show_graph
        self.draw_screen()

    def _graph_gutter(self, i, gw):
        """Цветной граф-гаттер строки коммита i, добитый пробелами до ширины gw."""
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

    def toggle_mode(self):
        self.all_branches = not self.all_branches
        self.sel = 0
        self.offset = 0
        self.reload_commits()
        self.draw_screen()

    def do_fetch(self):
        """Подтянуть изменения с удалёнок и перечитать список.

        git fetch — сеть до минуты; в колбэке ждать нельзя (UI и Ctrl+C замёрзнут),
        поэтому работа уходит в executor, а результат возвращается в event loop.
        """
        if not self.root or self._fetching:
            return
        self._fetching = True
        self.draw_screen()
        fut = self.asyncio_loop.run_in_executor(None, fetch, self.root)
        fut.add_done_callback(self._fetch_done)

    def _fetch_done(self, fut):
        self._fetching = False
        ok = not fut.cancelled() and fut.exception() is None and fut.result()
        self._detail_cache = {}          # ветки/содержимое могли измениться
        self.sel = 0
        self.offset = 0
        self.reload_commits()
        self.flash = 'fetched' if ok else 'fetch failed'
        self.draw_screen()

    def move(self, delta):
        if not self.commits:
            return
        self.sel = max(0, min(len(self.commits) - 1, self.sel + delta))
        if self.sel >= len(self.commits) - 1:
            self.load_more()
        self.draw_screen()

    def ensure_commit_visible(self):
        vis = self.visible_rows()
        if self.sel < self.offset:
            self.offset = self.sel
        elif self.sel >= self.offset + vis:
            self.offset = self.sel - vis + 1

    def open_commit(self):
        if not self.commits or not (0 <= self.sel < len(self.commits)):
            return
        self.commit = self.commits[self.sel]
        self._parent = first_parent(self.root, self.commit['sha'])
        self.items = commit_files(self.root, self.commit['sha'], self._parent)
        self.screen = 'diff'
        self.collapsed = set()
        self.show_noise = False
        self.focus = 'tree'
        self.search_query = ''
        self.search_matches = []
        self.rebuild_tree()
        self.tsel = self._first_file()
        self.left_offset = 0
        self.load_diff()
        self.draw_screen()

    def back_to_commits(self):
        self.screen = 'commits'
        self.commit = None
        self.draw_screen()

    # --- отрисовка ---

    def _draw_frame(self):
        self.cmd.clear_screen()
        if self.screen == 'commits':
            self._draw_commits()
        else:
            self._draw_diff_header()
            self._draw_pane_body()
        self._draw_input_line()
        foot_fg = 'green' if self.flash else 'gray'
        self.print(styled(truncate(self._footer(), self.screen_size.cols), fg=foot_fg), end='')
        self.flash = ''

    def _draw_commits(self):
        cols = self.screen_size.cols
        mode = 'all branches' if self.all_branches else 'current branch'
        header = f' {short_path(self.root or os.getcwd())} · {mode} ({len(self.commits)}'
        header += f'/{len(self.all_commits)})' if self.filter_query else ')'
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
        # ширина графа — максимум по ВИДИМЫМ строкам, а не глобальный: на линейных
        # экранах граф прижат к тексту, на мержах — расширяется ровно сколько нужно
        gw = max((len(self.graph[i]['cells']) for i in range(self.offset, end)
                  if i < len(self.graph)), default=0) if self.show_graph else 0
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

    def _commit_detail(self, sha):
        if sha not in self._detail_cache:
            self._detail_cache[sha] = commit_detail(self.root, sha)
        return self._detail_cache[sha]

    def _detail_lines(self, width):
        """Строки правой панели: подробности выбранного коммита (как в IDE)."""
        if not self.commits or not (0 <= self.sel < len(self.commits)):
            return []
        c = self.commits[self.sel]
        d = self._commit_detail(c['sha'])
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

    def _commit_row(self, c, width, selected):
        badge = '⑂ ' if c.get('merge') else ''
        refs = display_refs(c.get('refs') or [])
        refs_plain = '  '.join(name for name, _ in refs)
        author = truncate(c['author'], _AUTHOR_W)
        date = truncate(c['date'], _DATE_W)
        # автор и дата — фикс-колонки у правого края (строки выравниваются);
        # ветки/теги — правее subject, вплотную к колонке автора
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

    def _draw_diff_header(self):
        cols = self.screen_size.cols
        c = self.commit
        badge = '⑂ ' if c.get('merge') else ''
        refs = ''.join(f'{name} ' for name, _ in display_refs(c.get('refs') or []))
        header = f' {badge}{c["short"]} · {refs}{truncate(c["subject"], 60)}'
        cur = self.current_item()
        if cur:
            header += f'   ▸ {cur["rel"]}'
        self.print(styled(truncate(header, cols), fg='green', bold=True))
        self.print(styled('─' * cols, fg='gray'))

    def _footer(self):
        if self.input_mode:
            return ' Enter — keep   Esc — clear'
        if self.flash:
            return ' ' + self.flash
        if self._fetching:
            return ' fetching…'
        if self.screen == 'commits':
            mode = 'all branches' if self.all_branches else 'current branch'
            graph = 'g graph off' if self.show_graph else 'g graph on'
            info = 'i info off' if self.show_detail else 'i info on'
            return (f' [log]  ↑↓ commit · Enter/→ open · ⌘c hash · f fetch · a {mode}'
                    f' · {graph} · {info} · / filter · q quit')
        exp = 'a full-file' if not self.expand else 'a hunks'
        if self.focus == 'diff':
            if self.diff_sel is not None or self.diff_char_sel is not None:
                base = ' [diff]  drag selects (line/text) · ⌘c copy · Esc clear'
            else:
                act = 'Enter expand' if self._gap_at(self.diff_cur) is not None else '—'
                base = (f' [diff]  ↑↓ line · {act} · ⌘c copy'
                        f' · [ ] hunk · h/l scroll · {exp} · ←/Tab tree · Esc back')
        else:
            u = 'u show-ignored' if not self.show_noise else 'u hide-ignored'
            base = (f' [tree]  ↑↓ file · Enter fold · →/Tab diff · ⌘c path · {exp}'
                    f' · / search · {u} · Esc back')
        if self.hscroll:
            base += f'   ·   ↔ {self.hscroll}'
        if self.search_matches:
            base += f'   ·   n/N {self.search_idx + 1}/{len(self.search_matches)}'
        return base

    # --- ввод (фильтр коммитов / поиск по диффу) ---

    def start_filter(self):
        self.start_input('filter', self.filter_query)

    def start_search(self):
        self.start_input('search', self.search_query)

    def _input_live(self):
        if self.input_mode == 'filter':
            self.filter_query = self.input_buffer
            self.sel = 0
            self.rebuild_commits()
        else:
            self.search_query = self.input_buffer
            self._recompute_matches()
            if self.search_matches:
                self.search_idx = next((n for n, r in enumerate(self.search_matches)
                                        if r >= self.diff_offset), 0)
                self._scroll_to_match()
        self.draw_screen()

    def _input_cancelled(self, mode):
        if mode == 'filter':
            self.filter_query = ''
            self.sel = 0
            self.rebuild_commits()
        elif mode == 'search':
            self.search_query = ''
            self.search_matches = []

    # --- клавиатура ---

    def on_key(self, key_event):
        if key_event.type == EventType.RELEASE:
            return
        if key_event.matches('ctrl+c'):
            self.quit_loop(0)
            return
        k = key_event.key
        if self.input_key(k):
            return
        if self.screen == 'diff':
            if key_event.matches('cmd+c'):
                self.smart_copy()
                return
            if key_event.matches('cmd+shift+c'):
                self.smart_copy_location()
                return
        elif key_event.matches('cmd+c'):
            self.copy_commit()                 # список коммитов — скопировать hash
            return
        if self.screen == 'commits':
            self._commits_key(k)
        else:
            self._diff_key(k)

    def copy_commit(self):
        if not self.commits or not (0 <= self.sel < len(self.commits)):
            return
        c = self.commits[self.sel]
        self._copy_clipboard(c['sha'])
        self.flash = f'copied {c["short"]}'
        self.draw_screen()

    def _commits_key(self, k):
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
            self.draw_screen()
        elif k == 'END':
            self.sel = max(0, len(self.commits) - 1)
            self.draw_screen()
        elif k in ('ENTER', 'RIGHT'):
            self.open_commit()
        elif k == 'ESCAPE':
            self.quit_loop(0)

    def _diff_key(self, k):
        if k == 'TAB':
            self.toggle_focus()
        elif k == 'UP':
            self.nav(-1)
        elif k == 'DOWN':
            self.nav(1)
        elif k == 'PAGE_UP':
            self.diff_scroll(-self.visible_rows())
        elif k == 'PAGE_DOWN':
            self.diff_scroll(self.visible_rows())
        elif k == 'ENTER':
            if self.focus == 'diff' and self._gap_at(self.diff_cur) is not None:
                self.expand_gap(self.diff_cur)
            elif self.focus == 'tree':
                self.toggle_fold()
        elif k == 'RIGHT':
            self.set_focus('diff')
        elif k == 'LEFT':
            if self.focus == 'diff':
                self.set_focus('tree')
            else:
                self.back_to_commits()
        elif k == 'ESCAPE':
            if self.diff_sel is not None or self.diff_char_sel is not None:
                self.diff_sel = self.diff_char_sel = None
                self.draw_screen()
            elif self.search_query:
                self.clear_search()
            elif self.focus == 'diff':
                self.set_focus('tree')
            else:
                self.back_to_commits()

    def on_text(self, text, in_bracketed_paste=False):
        if self.input_text(text):
            return
        for ch in text:
            c = to_latin(ch)
            if c in ('q', 'Q'):
                self.quit_loop(0)
                return
            if self.screen == 'commits':
                if c == '/':
                    self.start_filter()
                elif c in ('a', 'A'):
                    self.toggle_mode()
                elif c in ('g', 'G'):
                    self.toggle_graph()
                elif c in ('i', 'I'):
                    self.show_detail = not self.show_detail
                    self.draw_screen()
                elif c in ('f', 'F'):
                    self.do_fetch()
                continue
            if c == '/':
                self.start_search()
            elif c == 'g':
                self.jump_edge(False)
            elif c == 'G':
                self.jump_edge(True)
            elif c in ('a', 'A'):
                self.toggle_expand()
            elif ch == '\t':
                self.toggle_focus()
            elif c == 'n':
                self.search_next(1)
            elif c == 'N':
                self.search_next(-1)
            elif c == '[':
                self.jump_hunk(-1)
            elif c == ']':
                self.jump_hunk(1)
            elif c in ('l', 'L'):
                self.hscroll_by(8)
            elif c in ('h', 'H'):
                self.hscroll_by(-8)
            elif c in ('u', 'U'):
                self.toggle_noise()
            elif ch == ' ':
                self.toggle_fold()

    # --- мышь: список коммитов сам, дифф — базовый класс ---

    def on_mouse_event(self, ev):
        if self.screen == 'commits':
            if ev.buttons in (MouseButton.WHEEL_UP, MouseButton.WHEEL_DOWN):
                self.move(-1 if ev.buttons == MouseButton.WHEEL_UP else 1)
                return
            Handler.on_mouse_event(self, ev)   # обычный клик → on_click
            return
        super().on_mouse_event(ev)

    def on_click(self, ev):
        if self.input_mode:
            return
        if self.screen == 'commits':
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
                self.draw_screen()
            return
        super().on_click(ev)

    def on_resize(self, new_size):
        if self.screen == 'diff':
            self.build_diff_rows()
        self.draw_screen()

    def on_interrupt(self):
        self.quit_loop(0)

    def on_eot(self):
        self.quit_loop(0)


def main(args: list) -> None:
    mark_overlay('log')
    root = git_root(os.getcwd())
    handler = CommitLogHandler(args, root)
    Loop().loop(handler)
    return None


if __name__ == '__main__':
    main(sys.argv)
