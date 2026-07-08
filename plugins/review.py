#!/usr/bin/env python3
"""
review — kitten для kitty.

Двухпанельный оверлей для ревью незакоммиченных правок git: слева дерево изменённых
файлов (в стиле IDE, со сворачиванием папок), справа — unified diff выделенного файла
с подсветкой синтаксиса, word-diff, поиском и прыжками по изменениям, вживую.

Двухпанельная diff-механика — общий базовый класс modules.vcs.view.DiffTreeView; здесь
только review-специфика: скоупы working/staged/branch (modules.review.git), аннотации к
строкам, живой refresh и открытие файла в редакторе.

Подключение в ~/.config/kitty/kitty.conf:
    map cmd+shift+r kitten /Users/deno/Projects/kitty/plugins/review.py
"""

import os
import sys
import shlex
import shutil
import subprocess

from kittens.tui.handler import result_handler
from kittens.tui.loop import Loop
from kittens.tui.operations import styled
from kitty.key_encoding import EventType

# Пакет modules лежит рядом с этим файлом. При запуске через `kitten path.py`
# (CLI/автодополнение) kitty не добавляет его папку в sys.path; при штатном launch
# папка и так в sys.path на время загрузки, но __file__ там отсутствует.
if '__file__' in globals():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.overlay import mark_overlay
from modules.vcs.util import short_path, to_latin, truncate
from modules.vcs.git import git_blob, git_root, has_head, read_text
from modules.vcs.view import DiffTreeView
from modules.review.git import detect_base, scan_changes


class ReviewHandler(DiffTreeView):

    def __init__(self, args, cwd, root, base='main'):
        super().__init__(root)
        self.cli_args = args
        self.cwd = cwd
        self.base = base             # базовая ветка для scope 'branch'
        self.scope = 'working'       # working → staged → branch (клавиша s)
        self.action = None           # что сделать после выхода (open in editor)
        self.annots = {}             # (file_rel, line) -> {'code': str, 'text': str}
        self.comment_target = None   # (rel, line, code) редактируемой аннотации
        self.filter_query = ''
        self.input_mode = None
        self.input_buffer = ''

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
        self.items = scan_changes(self.root, self.scope, self.base) if self.root else []
        self.status = '' if self.root else 'not a git repository'
        for it in self.items:
            it['rel'] = it['path']

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
        """Пересканировать изменения, сохранив фильтр, сворачивание, выделение и позицию
        скролла диффа (не прыгать на начало) — удобно пока агент дописывает код.
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

    def draw_screen(self):
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
        foot_fg = 'green' if self.flash else 'gray'
        self.print(styled(truncate(self._footer(), cols), fg=foot_fg), end='')
        self.flash = ''

    def _footer(self):
        if self.input_mode == 'comment':
            return ' Enter — save   Esc — cancel   (пустой = удалить)'
        if self.input_mode:
            return ' Enter — keep   Esc — clear'
        if self.flash:
            return ' ' + self.flash
        exp = 'a full-file' if not self.expand else 'a hunks'
        if self.focus == 'diff':
            if self.diff_sel is not None or self.diff_char_sel is not None:
                base = ' [diff]  drag selects (line/text) · ⌘c copy · Esc clear'
            else:
                act = ('Enter expand' if self._gap_at(self.diff_cur) is not None
                       else 'Enter/c comment')
                base = (f' [diff]  ↑↓ line · {act} · ⌘c copy'
                        f' · [ ] hunk · h/l scroll · {exp} · w export · ←/Tab tree · e edit')
        else:
            u = 'u show-ignored' if not self.show_noise else 'u hide-ignored'
            base = (f' [tree]  ↑↓ file · Enter fold · →/Tab diff · ⌘c path · {exp} · s scope'
                    f' · e edit · r refresh · / search · f filter · {u} · q')
        if self.annots:
            base += f'   ·   ✎ {len(self.annots)} ({{}} nav · w copy · x clear)'
        if self.hscroll:
            base += f'   ·   ↔ {self.hscroll}'
        if self.search_matches:
            base += f'   ·   n/N {self.search_idx + 1}/{len(self.search_matches)}'
        return base

    # --- аннотации (комментарии к строкам → markdown в буфер) ---

    def jump_annot(self, direction):
        """Прыжок курсора между строками с аннотациями (●) в текущем файле, по кругу."""
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
        self.input_mode = 'comment'
        self.input_buffer = existing['text'] if existing else ''
        self.draw_screen()

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
                out.append(f'  {v["text"]}')
            out.append('')
        self._copy_clipboard('\n'.join(out))
        self.flash = f'copied {len(self.annots)} comments to clipboard'
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
        """Открыть текущий файл на видимой сверху строке. GUI-редактор (IDE) запускаем
        тут же, не закрывая оверлей; терминальный ($EDITOR=vim) — выходим и открываем в табе.
        """
        it = self.current_item()
        if not it:
            return
        path = os.path.join(self.root, it['path'])
        line = 1
        if 0 <= self.diff_offset < len(self.diff_lineno):
            line = max(1, self.diff_lineno[self.diff_offset])
        project = self.root or os.path.dirname(path)
        cmd, gui = _editor_command(project, path, line)
        if gui:
            try:
                subprocess.Popen(cmd, cwd=project, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                self.flash = f'opened  {os.path.basename(path)}:{line}'
            except OSError as e:
                self.flash = f'editor failed: {e}'
            self.draw_screen()
            return
        # терминальный редактор — открываем в новом табе kitty (через handle_result)
        self.action = {'action': 'edit', 'path': path, 'line': line, 'cwd': project}
        self.quit_loop(0)

    # --- фильтр/поиск/комментарий (ввод в строке) ---

    def start_filter(self):
        self.input_mode = 'filter'
        self.input_buffer = self.filter_query
        self.draw_screen()

    def start_search(self):
        self.input_mode = 'search'
        self.input_buffer = self.search_query
        self.draw_screen()

    def _apply_input(self):
        if self.input_mode == 'filter':
            self._apply_filter()
        else:
            self._apply_search()

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
        self.input_mode = None
        self.draw_screen()

    def cancel_input(self):
        mode = self.input_mode
        self.input_mode = None
        self.input_buffer = ''
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
        self.draw_screen()

    # --- ввод ---

    def on_key(self, key_event):
        if key_event.type == EventType.RELEASE:
            return
        if key_event.matches('ctrl+c'):
            self.quit_loop(0)
            return
        if key_event.matches('ctrl+d'):
            self.diff_scroll(self.visible_rows() // 2)
            return
        if key_event.matches('ctrl+u'):
            self.diff_scroll(-self.visible_rows() // 2)
            return
        k = key_event.key
        if self.input_mode:
            if k == 'ENTER':
                self.commit_input()
            elif k == 'ESCAPE':
                self.cancel_input()
            elif k == 'BACKSPACE':
                self.input_buffer = self.input_buffer[:-1]
                if self.input_mode in ('filter', 'search'):
                    self._apply_input()
                else:
                    self.draw_screen()
            return
        if key_event.matches('cmd+c'):
            self.smart_copy()
            return
        if key_event.matches('cmd+shift+c'):
            self.smart_copy_location()
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
            self.tsel = 0
            self.load_diff()
            self.draw_screen()
        elif k == 'END':
            self.tsel = max(0, len(self.rows) - 1)
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
        if self.input_mode:
            self.input_buffer += ''.join(ch for ch in text if ch.isprintable())
            if self.input_mode in ('filter', 'search'):
                self._apply_input()
            else:
                self.draw_screen()
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
            elif ch == ' ':
                self.toggle_fold()

    def on_resize(self, new_size):
        self.build_diff_rows()
        self.draw_screen()

    def on_interrupt(self):
        self.quit_loop(0)

    def on_eot(self):
        # Ctrl+D — скролл диффа на полстраницы вниз, а НЕ закрытие оверлея.
        self.diff_scroll(self.visible_rows() // 2)


def main(args):
    mark_overlay('review')
    cwd = os.getcwd()
    root = git_root(cwd)
    base = detect_base(root) if root else 'main'
    handler = ReviewHandler(args, cwd, root, base)
    loop = Loop()
    loop.loop(handler)
    return handler.action


# GUI-редакторы открываем без окна kitty (у них своя оконная система),
# терминальные — в новом табе kitty.
_GUI_EDITORS = {'code', 'code-insiders', 'codium', 'cursor', 'windsurf', 'subl', 'zed'}

# JetBrains: shell-лаунчеры (если стоит command-line launcher) и .app в /Applications.
_JETBRAINS_CLI = ('phpstorm', 'idea', 'pycharm', 'webstorm', 'goland', 'rubymine',
                  'clion', 'rider', 'datagrip', 'idea-ce', 'pycharm-ce')
_JETBRAINS_APPS = ('PhpStorm', 'IntelliJ IDEA', 'IntelliJ IDEA CE', 'PyCharm',
                   'PyCharm CE', 'WebStorm', 'GoLand', 'RubyMine', 'CLion', 'Rider',
                   'DataGrip')


def _editor_command(project, path, line):
    """(argv, gui) — чем открыть файл на строке. Приоритет: IDE по конфигам проекта
    (открываем со всем проектом), иначе $VISUAL/$EDITOR, иначе vim.
    """
    j = os.path.join
    # 1) JetBrains — по .idea/ (открывает файл в проекте, к которому он принадлежит)
    if os.path.isdir(j(project, '.idea')):
        for launcher in _JETBRAINS_CLI:
            if shutil.which(launcher):
                return ([launcher, '--line', str(line), path], True)
        for app in _JETBRAINS_APPS:
            ap = f'/Applications/{app}.app'
            if os.path.isdir(ap):
                return (['open', '-na', ap, '--args', '--line', str(line), path], True)
    # 2) VS Code / Cursor — по .vscode/ или .cursor/ (открываем папку проекта + файл:строка)
    if os.path.isdir(j(project, '.vscode')) or os.path.isdir(j(project, '.cursor')):
        cursor = os.path.isdir(j(project, '.cursor'))
        clis = ('cursor',) if cursor else ('code', 'codium', 'code-insiders')
        for launcher in clis:
            if shutil.which(launcher):
                return ([launcher, project, '-g', f'{path}:{line}'], True)
        app = '/Applications/Cursor.app' if cursor else '/Applications/Visual Studio Code.app'
        if os.path.isdir(app):
            return (['open', '-na', app, '--args', project, '-g', f'{path}:{line}'], True)
    # 3) Zed — по .zed/
    if os.path.isdir(j(project, '.zed')) and shutil.which('zed'):
        return (['zed', f'{path}:{line}'], True)
    # 4) $VISUAL / $EDITOR (или vim)
    parts = shlex.split(os.environ.get('VISUAL') or os.environ.get('EDITOR') or 'vim')
    prog = os.path.basename(parts[0]) if parts else 'vim'
    if prog in _GUI_EDITORS:
        target = f'{path}:{line}'
        tail = [target] if prog in ('subl', 'zed') else ['-g', target]
        return (parts + tail, True)
    return (parts + [f'+{line}', path], False)


@result_handler()
def handle_result(args, answer, target_window_id, boss):
    if not answer or answer.get('action') != 'edit':
        return
    project, path, line = answer['cwd'], answer['path'], answer['line']
    cmd, gui = _editor_command(project, path, line)
    w = boss.window_id_map.get(target_window_id)
    kind = '--type=background' if gui else '--type=tab'
    boss.call_remote_control(w, ('launch', kind, '--cwd', project, *cmd))


if __name__ == '__main__':
    main(sys.argv)
