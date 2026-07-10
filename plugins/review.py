#!/usr/bin/env python3
"""
review — kitten для kitty.

Двухпанельный оверлей для ревью незакоммиченных правок git:
слева дерево изменённых файлов (в стиле IDE, со сворачиванием
папок), справа — unified diff выделенного файла с подсветкой
синтаксиса, word-diff, поиском и прыжками по изменениям,
вживую.

Двухпанельная diff-механика — общий базовый класс
modules.vcs.view.DiffTreeView; здесь только review-специфика:
скоупы working/staged/branch (modules.review.git), аннотации к
строкам, живой refresh и открытие файла в редакторе.

Подключение в ~/.config/kitty/kitty.conf:
    map cmd+shift+r kitten /Users/deno/Projects/kitty/plugins/review.py
"""

import os
import subprocess
import sys

from kittens.tui.handler import result_handler
from kittens.tui.loop import Loop
from kittens.tui.operations import styled
from kitty.key_encoding import EventType


# Пакет modules лежит рядом с этим файлом. При запуске через
# `kitten path.py` (CLI/автодополнение) kitty не добавляет его
# папку в sys.path; при штатном launch папка и так в sys.path
# на время загрузки, но __file__ там отсутствует.
if '__file__' in globals():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.overlay import mark_overlay
from modules.review.editor import editor_command
from modules.review.git import detect_base, revert_paths, scan_changes, stage_paths
from modules.text import plural
from modules.vcs.diff import group_key
from modules.vcs.git import git_blob, git_root, has_head, last_error, read_text
from modules.vcs.util import chord, short_path, to_latin, truncate
from modules.vcs.view import DiffTreeView


UNVERSIONED = 'Unversioned Files'


class ReviewHandler(DiffTreeView):

    multiline_modes = ('comment',)

    def __init__(self, args: list, cwd: str, root: 'str | None',
                 base: str = 'main') -> None:
        super().__init__(root)
        self.collapsed.add(group_key(UNVERSIONED))
        self.cli_args = args
        self.cwd = cwd
        self.base = base             # базовая ветка для scope 'branch'
        self.scope = 'working'       # working → staged → branch (клавиша s)
        self.action = None           # что сделать после выхода (open in editor)
        self.annots = {}             # (file_rel, line) -> {'code': str, 'text': str}
        self.comment_target = None   # (rel, line, code) редактируемой аннотации
        self.pending_revert = None   # (tracked, untracked), ждёт подтверждения
        self.filter_query = ''

    # --- хуки DiffTreeView ---

    def _contents(self, it):
        path = it['path']
        src = it.get('orig') or path
        absp = os.path.join(self.root, path)
        disk = read_text(absp) if os.path.exists(absp) else ''
        if self.scope == 'working':
            if it['untracked'] or not has_head(self.root):
                before = ''
            else:
                before = git_blob(self.root, 'HEAD', src)
            after = disk
        elif self.scope == 'staged':                      # индекс vs HEAD
            before = '' if it['kind'] == 'added' else git_blob(self.root, 'HEAD', src)
            after = '' if it['kind'] == 'deleted' else git_blob(self.root, '', path)
        else:                                             # branch: рабочее дерево vs base
            before = '' if it['kind'] == 'added' else git_blob(self.root, self.base, src)
            after = disk
        return before, after

    def _tree_visible(self, it):
        q = self.filter_query.lower()
        return not q or q in os.path.basename(it['rel']).lower()

    def _empty_pane_msg(self):
        return 'no matches' if self.filter_query else 'no changes'

    def _focus_landing(self, start):
        return self._first_commentable(start)   # курсор встаёт на строку кода (для аннотаций)

    def _diff_annotated(self, di, cur_rel):
        line = self.diff_lineno[di] if di < len(self.diff_lineno) else 0
        return (cur_rel is not None and (cur_rel, line) in self.annots
                and self._commentable(di))

    def _diff_line_clicked(self, di, double):
        if double and self._commentable(di):
            self.start_comment()
        else:
            self.draw_screen()

    # --- жизненный цикл ---

    def initialize(self):
        self.cmd.set_cursor_visible(False)
        self.load_source()
        self.draw_screen()

    def finalize(self):
        self.cmd.set_cursor_visible(True)

    def _reload_items(self):
        if not self.root:
            self.items = []
            self.status = 'not a git repository'
            return
        self.items = scan_changes(self.root, self.scope, self.base)
        for it in self.items:
            it['rel'] = it['path']
            if it.get('untracked'):
                it['group'] = UNVERSIONED
        # пустой список из-за ошибки git — показать её,
        # а не «no changes»
        self.status = '' if self.items else last_error()

    def cycle_scope(self):
        order = ('working', 'staged', 'branch')
        self.scope = order[(order.index(self.scope) + 1) % len(order)]
        self.tsel = 0
        self.load_source()
        self.draw_screen()

    def load_source(self):
        self._reload_items()
        self.filter_query = ''
        self.rebuild_tree()
        self.tsel = self._first_file()
        self.left_offset = 0
        self.load_diff()

    def refresh(self):
        """Пересканировать изменения, сохранив фильтр,
        сворачивание, выделение и позицию скролла диффа (не
        прыгать на начало) — удобно пока агент дописывает код.
        """
        off, hs = self.diff_offset, self.hscroll
        self._reload_items()
        self.rebuild_tree()          # сохраняет выделение по ключу/idx
        self.load_diff()             # сбрасывает diff_offset/hscroll в 0
        if hs:
            self.hscroll = hs
            self.build_diff_rows()
        limit = max(0, len(self.diff_rows) - self.visible_rows())
        self.diff_offset = min(off, limit)
        self.draw_screen()

    # --- отрисовка ---

    def _draw_frame(self):
        self.cmd.clear_screen()
        cols = self.screen_size.cols
        base = short_path(self.root or self.cwd)
        scope = {'working': 'working', 'staged': 'staged',
                 'branch': f'vs {self.base}'}[self.scope]
        header = f' {base} · {scope} ({self.n_files}'
        header += f'/{len(self.items)})' if self.filter_query else ')'
        cur = self.current_item()
        if cur:
            header += f'   ▸ {cur["rel"]}'
        self.print(styled(truncate(header, cols), fg='green', bold=True))
        self.print(styled('─' * cols, fg='gray'))
        self._draw_pane_body()
        self._draw_input_line()
        if self.pending_revert:
            foot_fg = 'red'
        else:
            foot_fg = 'green' if self.flash else 'gray'
        self.print(styled(truncate(self._footer(), cols), fg=foot_fg, bold=bool(
            self.pending_revert)), end='')
        self.flash = ''

    def _footer(self):
        if self.pending_revert:
            return self._revert_prompt()
        if self.input_mode == 'comment':
            return (' Enter — save   Shift+Enter — new line   ⌃w erase word'
                    '   ⌃u erase all   Esc — cancel   (empty = delete)')
        if self.input_mode:
            return ' Enter — keep   ⌃w erase word   ⌃u erase all   Esc — clear'
        if self.flash:
            return ' ' + self.flash
        modes = self._mode_hints()
        if self.focus == 'diff':
            if self.diff_sel is not None or self.diff_char_sel is not None:
                base = ' [diff]  drag selects (line/text) · ⌘c copy · Esc clear'
            else:
                act = ('Enter expand' if self._gap_at(self.diff_cur) is not None
                       else 'Enter/c comment')
                base = (f' [diff]  ↑↓ line · {act} · ⌘c copy'
                        f' · [ ] hunk · h/l scroll · {modes} · w export · ←/Tab tree · e edit')
        else:
            u = 'u show-ignored' if not self.show_noise else 'u hide-ignored'
            stage = ' · + stage' if self._selected_paths() else ''
            revert = ' · - revert' if any(self._revert_targets()) else ''
            base = (f' [tree]  ↑↓ file · Enter fold · →/Tab diff · ⌘c @path · {modes} · s scope'
                    f'{stage}{revert} · e edit · r refresh · / search · f filter · {u} · q')
        if self.annots:
            base += f'   ·   ✎ {len(self.annots)} ({{}} nav · w copy+clear · x clear)'
        if self.hscroll:
            base += f'   ·   ↔ {self.hscroll}'
        if self.search_matches:
            base += f'   ·   n/N {self.search_idx + 1}/{len(self.search_matches)}'
        return base

    # --- git add ---

    @staticmethod
    def _stageable(it):
        """Есть ли что добавлять в индекс: вторая буква статуса git —
        рабочее дерево. Полностью staged файл ('M ', 'A ') повторный
        git add не изменит, untracked ('??') — изменит.
        """
        xy = it.get('xy') or ''
        return len(xy) > 1 and xy[1] != ' '

    def _items_under_cursor(self, keep):
        """Элементы под курсором дерева: у файла — он сам, у папки (и
        у узла Unversioned Files) — все её файлы, прошедшие keep.
        Скрытые фильтром и noise-каталоги не попадают: трогаем ровно
        то, что видно.
        """
        if not self.rows or not (0 <= self.tsel < len(self.rows)):
            return []
        row = self.rows[self.tsel]
        if row['type'] == 'file':
            it = self.filtered[row['idx']]
            return [it] if keep(it) else []
        prefix = row['path'] + '/' if row.get('path') else ''
        return [it for it in self.filtered
                if it.get('group') == row.get('group') and it['rel'].startswith(prefix)
                and keep(it)]

    def _selected_paths(self):
        return [it['path'] for it in self._items_under_cursor(self._stageable)]

    def stage_selected(self):
        if not self.root:
            return
        paths = self._selected_paths()
        if not paths:
            self.flash = 'nothing to stage here'
            self.draw_screen()
            return
        if stage_paths(self.root, paths):
            self.flash = f'staged {plural(len(paths), "file")}'
        else:
            self.flash = f'git add failed: {last_error()}'
        self.refresh()

    # --- откат изменений (git restore / удаление новых файлов) ---

    @staticmethod
    def _revertable(it):
        # 'xy' есть только в скоупе working: в staged/branch откатывать
        # нечего — там показан уже закоммиченный/проиндексированный дифф
        return bool(it.get('xy'))

    def _revert_targets(self):
        items = self._items_under_cursor(self._revertable)
        tracked = [it['path'] for it in items if not it['untracked']]
        untracked = [it['path'] for it in items if it['untracked']]
        return tracked, untracked

    def start_revert(self):
        """Спросить подтверждение: откат необратим, а new-файлы ещё и
        не восстановить из git.
        """
        if not self.root:
            return
        tracked, untracked = self._revert_targets()
        if not tracked and not untracked:
            self.flash = 'nothing to revert here'
            self.draw_screen()
            return
        self.pending_revert = (tracked, untracked)
        self.draw_screen()

    def cancel_revert(self):
        self.pending_revert = None
        self.flash = 'revert cancelled'
        self.draw_screen()

    def confirm_revert(self):
        tracked, untracked = self.pending_revert
        self.pending_revert = None
        n = len(tracked) + len(untracked)
        if revert_paths(self.root, tracked, untracked):
            self.flash = f'reverted {plural(n, "file")}'
        else:
            self.flash = f'revert failed: {last_error()}'
        self.refresh()

    def _revert_prompt(self):
        tracked, untracked = self.pending_revert
        what = plural(len(tracked) + len(untracked), 'file')
        deleted = (f', {plural(len(untracked), "new file")} will be deleted for good'
                   if untracked else '')
        return f' revert {what}{deleted}?   y — yes   any other key — no'

    # --- аннотации (комментарии к строкам → markdown в буфер) ---

    def jump_annot(self, direction):
        """Прыжок курсора между строками с аннотациями (●) в
        текущем файле, по кругу.
        """
        cur = self.current_item()
        if not cur or not self.annots:
            return
        rel = cur['rel']
        marked = [di for di in range(len(self.diff_lineno))
                  if self._commentable(di) and (rel, self.diff_lineno[di]) in self.annots]
        if not marked:
            return
        self.focus = 'diff'
        if direction > 0:
            nxt = next((d for d in marked if d > self.diff_cur), marked[0])
        else:
            nxt = next((d for d in reversed(marked) if d < self.diff_cur), marked[-1])
        self.diff_cur = nxt
        self._ensure_cursor_visible()
        self.draw_screen()

    def start_comment(self):
        if self.focus != 'diff' or not self._commentable(self.diff_cur):
            self.flash = 'Tab → diff, hover a line, then c'
            self.draw_screen()
            return
        cur = self.current_item()
        if not cur:
            return
        line = self.diff_lineno[self.diff_cur]
        after_lines = self.diff_after.splitlines()
        code = after_lines[line - 1] if 0 < line <= len(after_lines) else ''
        self.comment_target = (cur['rel'], line, code)
        existing = self.annots.get((cur['rel'], line))
        self.start_input('comment', existing['text'] if existing else '')

    def _save_comment(self):
        rel, line, code = self.comment_target
        text = self.input_buffer.strip()
        key = (rel, line)
        if text:
            self.annots[key] = {'code': code, 'text': text}
        else:
            self.annots.pop(key, None)   # пустой комментарий = удалить
        self.comment_target = None

    def export_review(self):
        if not self.annots:
            self.flash = 'no comments — Tab→diff, hover a line, c'
            self.draw_screen()
            return
        by_file = {}
        for (rel, line), v in self.annots.items():
            by_file.setdefault(rel, []).append((line, v))
        out = ['# Review comments', '']
        for rel in sorted(by_file):
            out.append(f'## {rel}')
            for line, v in sorted(by_file[rel]):
                code = v['code'].strip()
                out.append(f'- **L{line}** `{code}`' if code else f'- **L{line}**')
                out += [f'  {ln}' if ln else '' for ln in v['text'].split('\n')]
            out.append('')
        self._copy_clipboard('\n'.join(out))
        n = len(self.annots)
        # выгруженное ревью живёт дальше в буфере обмена; держать
        # его ещё и на строках диффа незачем — маркеры ● только
        # мешают следующему проходу
        self.annots = {}
        self.flash = f'copied {plural(n, "comment")} to clipboard — cleared'
        self.draw_screen()

    def clear_annotations(self):
        if not self.annots:
            self.flash = 'no comments'
        else:
            self.flash = f'cleared {len(self.annots)} comments'
            self.annots = {}
        self.draw_screen()

    # --- открытие файла в редакторе ---

    def open_editor(self):
        """Открыть текущий файл на видимой сверху строке.
        GUI-редактор (IDE) запускаем тут же, не закрывая
        оверлей; терминальный ($EDITOR=vim) — выходим и
        открываем в табе.
        """
        it = self.current_item()
        if not it:
            return
        path = os.path.join(self.root, it['path'])
        line = 1
        if 0 <= self.diff_offset < len(self.diff_lineno):
            line = max(1, self.diff_lineno[self.diff_offset])
        project = self.root or os.path.dirname(path)
        cmd, gui = editor_command(project, path, line)
        if gui:
            # start_new_session: жизнь редактора не должна
            # зависеть от процесса оверлея
            try:
                subprocess.Popen(cmd, cwd=project, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                self.flash = f'opened  {os.path.basename(path)}:{line}'
            except OSError as e:
                self.flash = f'editor failed: {e}'
            self.draw_screen()
            return
        # терминальный редактор — открываем в новом табе kitty
        # (через handle_result)
        self.action = {'action': 'edit', 'path': path, 'line': line, 'cwd': project}
        self.quit_loop(0)

    # --- фильтр/поиск/комментарий (ввод в строке) ---

    def start_filter(self):
        self.start_input('filter', self.filter_query)

    def start_search(self):
        self.start_input('search', self.search_query)

    def _input_live(self):
        if self.input_mode == 'filter':
            self._apply_filter()
        elif self.input_mode == 'search':
            self._apply_search()
        else:
            self.draw_screen()

    def _apply_filter(self):
        self.filter_query = self.input_buffer
        self.tsel = 0
        self.rebuild_tree()
        self.load_diff()
        self.draw_screen()

    def _apply_search(self):
        self.search_query = self.input_buffer
        self._recompute_matches()
        if self.search_matches:
            self.search_idx = next((n for n, r in enumerate(self.search_matches)
                                    if r >= self.diff_offset), 0)
            self._scroll_to_match()
        self.draw_screen()

    def commit_input(self):
        if self.input_mode == 'comment' and self.comment_target:
            self._save_comment()
        super().commit_input()

    def _input_cancelled(self, mode):
        if mode == 'filter':
            self.filter_query = ''
            self.tsel = 0
            self.rebuild_tree()
            self.load_diff()
        elif mode == 'search':
            self.search_query = ''
            self.search_matches = []
        elif mode == 'comment':
            self.comment_target = None

    # --- ввод ---

    def on_key(self, key_event):
        if key_event.type == EventType.RELEASE:
            return
        if self.pending_revert:
            # печатаемое (в т.ч. сам «y») разбирает on_text; здесь
            # гасим только Enter/стрелки/Esc: необратимое не должно
            # подтверждаться ничем, кроме явного «y»
            if not getattr(key_event, 'text', ''):
                self.cancel_revert()
            return
        if chord(key_event, 'ctrl', 'c'):
            self.quit_loop(0)
            return
        # пока пишем в строку ввода, ⌃u/⌃w правят текст, а не
        # скроллят дифф: скроллить всё равно незачем
        if self.input_mode:
            if chord(key_event, 'ctrl', 'w'):
                self.input_kill_word()
                return
            if chord(key_event, 'ctrl', 'u'):
                self.input_kill_all()
                return
        elif chord(key_event, 'ctrl', 'd'):
            self.diff_scroll(self.visible_rows() // 2)
            return
        elif chord(key_event, 'ctrl', 'u'):
            self.diff_scroll(-self.visible_rows() // 2)
            return
        k = key_event.key
        if self.input_key(k, shift=bool(getattr(key_event, 'shift', False))):
            return
        if chord(key_event, 'super+shift', 'c'):
            self.smart_copy_location()
            return
        if chord(key_event, 'super', 'c'):
            self.smart_copy()
            return
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
        elif k == 'HOME':
            self.set_tsel(0)
            self.load_diff()
            self.draw_screen()
        elif k == 'END':
            self.set_tsel(len(self.rows) - 1)
            self.load_diff()
            self.draw_screen()
        elif k == 'ENTER':
            if self.focus == 'diff':
                if self._gap_at(self.diff_cur) is not None:
                    self.expand_gap(self.diff_cur)
                else:
                    self.start_comment()
            else:
                self.toggle_fold()
        elif k == 'RIGHT':
            self.set_focus('diff')
        elif k == 'LEFT':
            self.set_focus('tree')
        elif k == 'ESCAPE':
            if self.diff_sel is not None or self.diff_char_sel is not None:
                self.diff_sel = self.diff_char_sel = None   # сначала снимаем выделение
                self.draw_screen()
            elif self.search_query:
                self.clear_search()
            elif self.focus == 'diff':
                self.focus = 'tree'
                self.draw_screen()
            else:
                self.quit_loop(0)

    def on_text(self, text, in_bracketed_paste=False):
        if self.pending_revert:
            if to_latin(text[:1]) in ('y', 'Y'):
                self.confirm_revert()
            else:
                self.cancel_revert()
            return
        if self.input_text(text):
            return
        for ch in text:
            if ch == '\x15':   # Ctrl+U — скролл диффа на полстраницы вверх
                self.diff_scroll(-(self.visible_rows() // 2))
                continue
            if ch == '\x04':   # Ctrl+D — на полстраницы вниз (дубль к on_eot)
                self.diff_scroll(self.visible_rows() // 2)
                continue
            if ch in ('{', 'Х'):    # прыжок к пред. аннотации (Shift+[ ; на ru — Shift+х)
                self.jump_annot(-1)
                continue
            if ch in ('}', 'Ъ'):    # прыжок к след. аннотации
                self.jump_annot(1)
                continue
            c = to_latin(ch)
            if c in ('q', 'Q'):
                self.quit_loop(0)
                return
            elif c == '/':
                self.start_search()
            elif c in ('f', 'F'):
                self.start_filter()
            elif c == 'n':
                self.search_next(1)
            elif c == 'N':
                self.search_next(-1)
            elif c == '[':
                self.jump_hunk(-1)
            elif c == ']':
                self.jump_hunk(1)
            elif ch == '\t':
                self.toggle_focus()
            elif c in ('l', 'L'):
                self.hscroll_by(8)
            elif c in ('h', 'H'):
                self.hscroll_by(-8)
            elif c == 'g':
                self.jump_edge(False)
            elif c == 'G':
                self.jump_edge(True)
            elif c in ('a', 'A'):
                self.toggle_expand()
            elif c in ('v', 'V'):
                self.toggle_view_mode()
            elif c in ('s', 'S'):
                self.cycle_scope()
            elif c in ('r', 'R'):
                self.refresh()
            elif c in ('e', 'E'):
                self.open_editor()
                return
            elif c in ('c', 'C'):
                self.start_comment()
            elif c in ('w', 'W'):
                self.export_review()
            elif c in ('x', 'X'):
                self.clear_annotations()
            elif c in ('u', 'U'):
                self.toggle_noise()
            elif ch == '+':
                self.stage_selected()
            elif ch == '-':
                self.start_revert()
            elif ch == ' ':
                self.toggle_fold()

    def on_resize(self, new_size):
        self.build_diff_rows()
        self.draw_screen()

    def on_interrupt(self):
        self.quit_loop(0)

    def on_eot(self):
        # Ctrl+D — скролл диффа на полстраницы вниз, а НЕ
        # закрытие оверлея.
        self.diff_scroll(self.visible_rows() // 2)


def main(args: list) -> 'dict | None':
    mark_overlay('review')
    cwd = os.getcwd()
    root = git_root(cwd)
    base = detect_base(root) if root else 'main'
    handler = ReviewHandler(args, cwd, root, base)
    loop = Loop()
    loop.loop(handler)
    return handler.action


@result_handler()
def handle_result(args, answer, target_window_id, boss):
    if not answer or answer.get('action') != 'edit':
        return
    project, path, line = answer['cwd'], answer['path'], answer['line']
    cmd, gui = editor_command(project, path, line)
    w = boss.window_id_map.get(target_window_id)
    if w is None:
        return   # исходное окно уже закрыто — не запускать «куда попало»
    kind = '--type=background' if gui else '--type=tab'
    boss.call_remote_control(w, ('launch', kind, '--cwd', project, *cmd))


if __name__ == '__main__':
    main(sys.argv)
