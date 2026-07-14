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
незакоммиченные правки (modules.review.git), аннотации к
строкам, живой refresh и открытие файла в редакторе.

Подключение в ~/.config/kitty/kitty.conf:
    map cmd+shift+r kitten /Users/deno/Projects/kitty/plugins/review.py
"""

import os
import subprocess
import sys
from typing import Callable, ClassVar

from kittens.tui.handler import result_handler
from kittens.tui.loop import EventType as MouseEventType
from kittens.tui.loop import Loop, MouseButton
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
from modules.review.git import revert_paths, scan_changes, stage_paths
from modules.text import plural, short_path, truncate
from modules.update import start_check, update_hint
from modules.vcs.diff import DiffSource, group_key
from modules.vcs.git import git_blob, git_root, has_head, last_error, read_text
from modules.vcs.navdef import Target, resolve_definition, symbol_at, word_span
from modules.vcs.util import chord, ctrl_letter, to_latin
from modules.vcs.view import DiffTreeView


UNVERSIONED = 'Unversioned Files'

# бит Alt/Option в mouse-событии. kitty кодирует модификаторы мыши
# СВОЕЙ схемой (shift=1, alt=2, ctrl=4, super=8), а не xterm-SGR
# (где alt=8): проверено эмпирически — ⌥+click даёт mods=2. ⌘/Super
# мышью не приходит, поэтому go-to-definition — на ⌥+click.
_ALT_MOD = 0b10


class ReviewHandler(DiffTreeView):

    multiline_modes: ClassVar[tuple[str, ...]] = ('comment',)

    def __init__(self, args: list[str], cwd: str, root: 'str | None') -> None:
        super().__init__(root)
        self.collapsed.add(group_key(UNVERSIONED))
        self.cli_args = args
        self.cwd = cwd
        # что сделать после выхода (open in editor)
        self.action: 'dict | None' = None
        # (rel, line) → {'code', 'text'}
        self.annots: dict[tuple[str, int], dict[str, str]] = {}
        # (rel, line, code) редактируемой аннотации
        self.comment_target: 'tuple[str, int, str] | None' = None
        # (tracked, untracked), ждёт подтверждения
        self.pending_revert: 'tuple[list[str], list[str]] | None' = None
        self.filter_query = ''
        # go-to-definition: путь показанного read-only внешнего файла
        # (None — обычный diff из ревью), стек «назад», активный пикер
        self._external: 'str | None' = None
        self._navstack: list[dict] = []
        self._cand: 'list[Target] | None' = None
        self._cand_sym = ''

    # --- хуки DiffTreeView ---

    def _contents(self, it: dict) -> tuple[str, str]:
        path = it['path']
        absp = os.path.join(self.root, path)
        after = read_text(absp) if os.path.exists(absp) else ''
        if it['untracked'] or not has_head(self.root):
            return '', after
        return git_blob(self.root, 'HEAD', it.get('orig') or path), after

    def _tree_visible(self, it: dict) -> bool:
        q = self.filter_query.lower()
        return not q or q in os.path.basename(it['rel']).lower()

    def _empty_pane_msg(self) -> str:
        return 'no matches' if self.filter_query else 'no changes'

    def _focus_landing(self, start: int) -> int:
        return self._first_commentable(start)   # курсор встаёт на строку кода (для аннотаций)

    def _diff_annotated(self, di: int, cur_rel: 'str | None') -> bool:
        line = self.diff_lineno[di] if di < len(self.diff_lineno) else 0
        return (cur_rel is not None and (cur_rel, line) in self.annots
                and self._commentable(di))

    def _diff_line_clicked(self, di: int, double: bool, col: int) -> None:
        # клик по номеру строки (левее гуттера) → комментарий к ней
        if col < self._gutter_cols():
            if self._commentable(di):
                self.start_comment()
            else:
                self.draw_screen()
            return
        # двойной клик по коду → выделить слово под курсором
        if double and not self._external:
            span = word_span(self.diff_plain[di], col)
            if span:
                self.diff_char_sel = (di, *span)
                self.diff_sel = None
                self.flash = 'selected — ⌘c to copy'
        self.draw_screen()

    # --- жизненный цикл ---

    def initialize(self) -> None:
        self.cmd.set_cursor_visible(False)
        self.load_source()
        self.flash = update_hint() or ''
        start_check()
        self.draw_screen()

    def finalize(self) -> None:
        self.cmd.set_cursor_visible(True)
        self.reset_pointer()

    def _reload_items(self) -> None:
        if not self.root:
            self.items = []
            self.status = 'not a git repository'
            return
        self.items = scan_changes(self.root)
        for it in self.items:
            it['rel'] = it['path']
            if it.get('untracked'):
                it['group'] = UNVERSIONED
        # пустой список из-за ошибки git — показать её,
        # а не «no changes»
        self.status = '' if self.items else last_error()

    def load_source(self) -> None:
        self._reload_items()
        self.filter_query = ''
        self.rebuild_tree()
        self.tsel = self._first_file()
        self.left_offset = 0
        self.load_diff()

    def refresh(self) -> None:
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

    def _draw_frame(self) -> None:
        if self._cand is not None:
            self._draw_picker()
            return
        self.cmd.clear_screen()
        cols = self.screen_size.cols
        base = short_path(self.root or self.cwd)
        header = f' {base} ({self.n_files}'
        header += f'/{len(self.items)})' if self.filter_query else ')'
        cur = self.current_item()
        if self._external:
            header += f'   ▸ {self._external} (read-only)'
        elif cur:
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

    def _footer(self) -> str:
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
        back = ' · ⌃o back' if self._navstack else ''
        if self._external:
            return f' [read-only]  ↑↓ scroll · [ ] hunk · h/l scroll · ⌥/d def{back} · q'
        if self.focus == 'diff':
            if self.diff_sel is not None or self.diff_char_sel is not None:
                base = ' [diff]  drag selects (line/text) · ⌘c copy · d def · Esc clear'
            else:
                if self._gap_at(self.diff_cur) is not None:
                    act = 'Enter expand'
                else:
                    act = 'Enter/c comment'
                base = (f' [diff]  ↑↓ line · {act} · ⌥/d def · ⌘c copy · [ ] hunk'
                        f' · h/l scroll · {modes} · w export · ←/Tab tree · e edit{back}')
        else:
            u = 'u show-ignored' if not self.show_noise else 'u hide-ignored'
            stage = ' · + stage' if self._selected_paths() else ''
            revert = ' · - revert' if any(self._revert_targets()) else ''
            base = (f' [tree]  ↑↓ file · Enter fold · →/Tab diff · ⌘c @path · {modes}'
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
    def _stageable(it: dict) -> bool:
        """Есть ли что добавлять в индекс: вторая буква статуса git —
        рабочее дерево. Полностью staged файл ('M ', 'A ') повторный
        git add не изменит, untracked ('??') — изменит.
        """
        xy = it['xy']
        return len(xy) > 1 and xy[1] != ' '

    def _items_under_cursor(self, keep: Callable[[dict], bool]) -> list[dict]:
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

    def _selected_paths(self) -> list[str]:
        return [it['path'] for it in self._items_under_cursor(self._stageable)]

    def stage_selected(self) -> None:
        if self._ro_block() or not self.root:
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

    def _revert_targets(self) -> tuple[list[str], list[str]]:
        items = self._items_under_cursor(lambda it: True)
        tracked = [it['path'] for it in items if not it['untracked']]
        untracked = [it['path'] for it in items if it['untracked']]
        return tracked, untracked

    def start_revert(self) -> None:
        """Спросить подтверждение: откат необратим, а new-файлы ещё и
        не восстановить из git.
        """
        if self._ro_block() or not self.root:
            return
        tracked, untracked = self._revert_targets()
        if not tracked and not untracked:
            self.flash = 'nothing to revert here'
            self.draw_screen()
            return
        self.pending_revert = (tracked, untracked)
        self.draw_screen()

    def cancel_revert(self) -> None:
        self.pending_revert = None
        self.flash = 'revert cancelled'
        self.draw_screen()

    def confirm_revert(self) -> None:
        tracked, untracked = self.pending_revert
        self.pending_revert = None
        n = len(tracked) + len(untracked)
        if revert_paths(self.root, tracked, untracked):
            self.flash = f'reverted {plural(n, "file")}'
        else:
            self.flash = f'revert failed: {last_error()}'
        self.refresh()

    def _revert_prompt(self) -> str:
        tracked, untracked = self.pending_revert
        what = plural(len(tracked) + len(untracked), 'file')
        deleted = ''
        if untracked:
            deleted = f', {plural(len(untracked), "new file")} will be deleted for good'
        return f' revert {what}{deleted}?   y — yes   any other key — no'

    # --- аннотации (комментарии к строкам → markdown в буфер) ---

    def jump_annot(self, direction: int) -> None:
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

    def start_comment(self) -> None:
        if self._ro_block():
            return
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

    def _save_comment(self) -> None:
        rel, line, code = self.comment_target
        text = self.input_buffer.strip()
        key = (rel, line)
        if text:
            self.annots[key] = {'code': code, 'text': text}
        else:
            self.annots.pop(key, None)   # пустой комментарий = удалить
        self.comment_target = None

    def export_review(self) -> None:
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

    def clear_annotations(self) -> None:
        if not self.annots:
            self.flash = 'no comments'
        else:
            self.flash = f'cleared {plural(len(self.annots), "comment")}'
            self.annots = {}
        self.draw_screen()

    # --- открытие файла в редакторе ---

    def open_editor(self) -> None:
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

    # --- go-to-definition (⌥click / d по выделению) ---

    def _ro_block(self) -> bool:
        # правки (комментарий/stage/revert/export) в read-only внешнем
        # файле бессмысленны: tree-item под ним чужой
        if self._external:
            self.flash = 'read-only (external file)'
            self.draw_screen()
            return True
        return False

    def _word_at(self, ev) -> 'tuple[str, bool, bool, str | None] | None':
        di = self._diff_row_at(ev)
        if di is None or not (0 <= di < len(self.diff_plain)):
            return None
        return symbol_at(self.diff_plain[di], self._diff_col_at(ev))

    def goto_definition(self, symbol: 'str | None', is_attr: bool = False,
                        is_call: bool = False, qualifier: 'str | None' = None) -> None:
        if not symbol or not self.root:
            return
        cur_rel = self._external or (self.current_item() or {}).get('rel')
        targets = resolve_definition(
            self.root, cur_rel, self.diff_ext, symbol, is_attr=is_attr,
            is_call=is_call, qualifier=qualifier, cur_source=self.diff_after)
        if not targets:
            self.flash = f"no definition for '{symbol}'"
            self.draw_screen()
            return
        if len(targets) == 1:
            self._navigate(targets[0])
        else:
            self._cand, self._cand_sym = targets, symbol
            self.draw_screen()

    def _goto_from_selection(self) -> None:
        sel = self.diff_char_sel
        if not sel:
            self.flash = 'select a word (drag), then d'
            self.draw_screen()
            return
        row, cs, ce = sel
        if 0 <= row < len(self.diff_plain):
            ref = symbol_at(self.diff_plain[row], cs)
            if ref:
                self.goto_definition(*ref)

    def _snapshot(self) -> dict:
        return {'external': self._external, 'tsel': self.tsel,
                'diff_offset': self.diff_offset, 'diff_cur': self.diff_cur,
                'view_mode': self.view_mode, 'hscroll': self.hscroll,
                'left_offset': self.left_offset, 'focus': self.focus,
                'collapsed': set(self.collapsed)}

    def _reveal_file(self, rel: str) -> None:
        # раскрыть свёрнутых предков, чтобы файл появился строкой дерева
        it = next((x for x in self.filtered if x['rel'] == rel), None)
        if it is None:
            return
        prefix = group_key(it['group']) if it.get('group') else ''
        if prefix:
            self.collapsed.discard(prefix)
        key = prefix
        for part in rel.split('/')[:-1]:
            key = f'{key}/{part}' if key else part
            self.collapsed.discard(key)
        self.rebuild_tree()

    def _tree_row_for(self, rel: str) -> 'int | None':
        for i, r in enumerate(self.rows):
            if r['type'] == 'file' and self.filtered[r['idx']]['rel'] == rel:
                return i
        return None

    def _navigate(self, target: Target) -> None:
        self._navstack.append(self._snapshot())
        in_review = any(x['rel'] == target.path for x in self.filtered)
        row = None
        if in_review:
            self._reveal_file(target.path)
            row = self._tree_row_for(target.path)
        if row is not None:
            self._external = None
            self.set_tsel(row)
            self.load_diff()
            self.focus = 'diff'
            # определение часто на неизменённой строке — в unified она
            # скрыта (свёрнута), центрироваться не на что; финальный вид
            # показывает файл целиком. nav_back вернёт прежний режим.
            if target.line and target.line not in self.diff_lineno:
                self.view_mode = 'final'
                self.build_diff_rows()
            self._center_on_line(target.line)
        else:
            self._show_file(target.path, target.line)
        self.flash = f'{short_path(target.path)}:{target.line}'
        self.draw_screen()

    def _show_file(self, rel: str, line: int) -> None:
        text = read_text(os.path.join(self.root, rel))
        self._external = rel
        self.diff_before = self.diff_after = text
        self.diff_ext = os.path.splitext(rel)[1].lower()
        self.diff_src = DiffSource(text, text)
        self.view_mode = 'final'
        self.hscroll = 0
        self.diff_sel = self.diff_char_sel = None
        self.expanded = set()
        self.build_diff_rows()
        self.focus = 'diff'
        self._center_on_line(line)

    def nav_back(self) -> None:
        if not self._navstack:
            self.flash = 'nothing to go back to'
            self.draw_screen()
            return
        s = self._navstack.pop()
        self.collapsed = s['collapsed']
        self.rebuild_tree()
        self.view_mode = s['view_mode']
        if s['external']:
            self._show_file(s['external'], 0)
        else:
            self._external = None
            self.set_tsel(s['tsel'])
            self.load_diff()
        if s['hscroll'] and self.hscroll != s['hscroll']:
            self.hscroll = min(s['hscroll'], self.hscroll_max)
            self.build_diff_rows()
        rows = max(0, len(self.diff_rows) - 1)
        self.diff_cur = min(s['diff_cur'], rows)
        self.diff_offset = min(s['diff_offset'], max(0, len(self.diff_rows) - self.visible_rows()))
        self.left_offset = s['left_offset']
        self.focus = s['focus']
        self.flash = 'back'
        self.draw_screen()

    # --- пикер кандидатов (несколько определений) ---

    def _draw_picker(self) -> None:
        cols = self.screen_size.cols
        self.cmd.clear_screen()
        self.print(styled(truncate(f" definitions of ‘{self._cand_sym}’", cols),
                          fg='green', bold=True))
        self.print(styled('─' * cols, fg='gray'))
        for i, t in enumerate(self._cand[:9]):
            mark = '▎' if t.kind == 'def' else ' '
            loc = f'{short_path(t.path)}:{t.line}'
            self.print(truncate(f' {i + 1} {mark} {loc}   {t.preview}', cols))
        self.print('')
        self.print(styled(truncate(' 1-9 open · Esc cancel', cols), fg='gray'), end='')

    def _pick(self, n: int) -> None:
        targets = self._cand
        self._cand, self._cand_sym = None, ''
        if targets and 0 <= n < min(9, len(targets)):
            self._navigate(targets[n])
        else:
            self.draw_screen()

    def _close_picker(self) -> None:
        self._cand, self._cand_sym = None, ''
        self.draw_screen()

    # --- фильтр/поиск/комментарий (ввод в строке) ---

    def start_filter(self) -> None:
        self.start_input('filter', self.filter_query)

    def _input_live(self) -> None:
        if self.input_mode == 'filter':
            self._apply_filter()
        elif self.input_mode == 'search':
            self.apply_search_input()
        else:
            self.draw_screen()

    def _apply_filter(self) -> None:
        self.filter_query = self.input_buffer
        self.tsel = 0
        self.rebuild_tree()
        self.load_diff()
        self.draw_screen()

    def commit_input(self) -> None:
        if self.input_mode == 'comment' and self.comment_target:
            self._save_comment()
        super().commit_input()

    def _input_cancelled(self, mode: str) -> None:
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

    def _wanted_pointer(self, ev) -> 'str | None':
        di = self._diff_row_at(ev)
        if di is not None:
            col = self._diff_col_at(ev)
            # ⌥ над идентификатором → go-to-definition (кликабельно)
            if (getattr(ev, 'mods', 0) & _ALT_MOD) and col >= self._gutter_cols():
                if word_span(self.diff_plain[di], col):
                    return 'pointer'
            # над номером строки, где можно оставить комментарий
            if col < self._gutter_cols() and self._commentable(di):
                return 'pointer'
        return super()._wanted_pointer(ev)

    def on_mouse_event(self, ev) -> None:
        press = getattr(ev, 'type', None) == MouseEventType.PRESS
        left = bool(ev.buttons & MouseButton.LEFT)
        # пикер открыт — клик выбирает кандидата (строки списка с 2-й)
        if self._cand is not None:
            if press and left:
                self._pick(ev.cell_y - 2)
            return
        # ⌥+ЛКМ по слову — go-to-definition; press глотаем, иначе
        # базовый Handler синтезирует click и начнёт drag-select
        if press and left and (getattr(ev, 'mods', 0) & _ALT_MOD):
            ref = self._word_at(ev)
            if ref:
                self.goto_definition(*ref)
            return
        super().on_mouse_event(ev)

    def on_key(self, key_event) -> None:
        if key_event.type == EventType.RELEASE:
            return
        if self._cand is not None:
            if key_event.key == 'ESCAPE':
                self._close_picker()
            return   # пока пикер открыт — глотаем прочие клавиши
        if self.pending_revert:
            # печатаемое (в т.ч. сам «y») разбирает on_text; здесь
            # гасим только Enter/стрелки/Esc: необратимое не должно
            # подтверждаться ничем, кроме явного «y»
            if not getattr(key_event, 'text', ''):
                self.cancel_revert()
            return
        for letter in ('c', 'w', 'u', 'o', 'd'):
            if chord(key_event, 'ctrl', letter):
                if self._ctrl_key(letter):
                    return
                break
        k = key_event.key
        if self.input_key(k, shift=bool(getattr(key_event, 'shift', False))):
            return
        if chord(key_event, 'super+shift', 'c'):
            self.smart_copy_location()
            return
        if chord(key_event, 'super', 'c'):
            self.smart_copy()
            return
        if self.diff_common_key(k):
            return
        if k == 'HOME':
            self.set_tsel(0)
            self.load_diff()
            self.draw_screen()
        elif k == 'END':
            self.set_tsel(len(self.rows) - 1)
            self.load_diff()
            self.draw_screen()
        elif k == 'ENTER':
            self.start_comment()   # общий разбор оставил Enter на строке кода диффа
        elif k == 'ESCAPE' and self.filter_query:
            # дно каскада: Esc не закрывает оверлей (выход — q/⌃c)
            self._input_cancelled('filter')
            self.draw_screen()

    def _ctrl_key(self, letter: str) -> bool:
        """Ctrl-хоткеи — общая точка для on_key и on_text: на кириллице
        ctrl+буква приходит C0-байтом, а не key-событием (ctrl_letter).
        """
        if letter == 'c':
            self.quit_loop(0)
            return True
        if self.input_mode:
            # пока пишем в строку ввода, ⌃u/⌃w правят текст, а не
            # скроллят дифф: скроллить всё равно незачем
            if letter == 'w':
                self.input_kill_word()
                return True
            if letter == 'u':
                self.input_kill_all()
                return True
            return False
        if letter == 'o':
            self.nav_back()
            return True
        if letter == 'd':
            self.diff_scroll(self.visible_rows() // 2)
            return True
        if letter == 'u':
            self.diff_scroll(-(self.visible_rows() // 2))
            return True
        return False

    def on_text(self, text: str, in_bracketed_paste: bool = False) -> None:
        if self.pending_revert:
            if to_latin(text[:1]) in ('y', 'Y'):
                self.confirm_revert()
            else:
                self.cancel_revert()
            return
        if self._cand is not None:
            ch = text[:1]
            if ch.isdigit() and ch != '0':
                self._pick(int(ch) - 1)
            return
        ctrl = ctrl_letter(text, in_bracketed_paste)
        if ctrl is not None and self._ctrl_key(ctrl):
            return
        if self.input_text(text):
            return
        for ch in text:
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
            if self.diff_common_text(ch):
                continue
            if c in ('f', 'F'):
                self.start_filter()
            elif c in ('r', 'R'):
                self.refresh()
            elif c in ('e', 'E'):
                self.open_editor()
                return
            elif c in ('c', 'C'):
                self.start_comment()
            elif c in ('d', 'D'):
                self._goto_from_selection()
            elif c in ('w', 'W'):
                self.export_review()
            elif c in ('x', 'X'):
                self.clear_annotations()
            elif ch == '+':
                self.stage_selected()
            elif ch == '-':
                self.start_revert()

    def on_resize(self, new_size) -> None:
        self.build_diff_rows()
        self.draw_screen()

    def on_interrupt(self) -> None:
        self.quit_loop(0)

    def on_eot(self) -> None:
        # Ctrl+D — скролл диффа на полстраницы вниз, а НЕ
        # закрытие оверлея.
        self.diff_scroll(self.visible_rows() // 2)


def main(args: list[str]) -> 'dict | None':
    mark_overlay('review')
    cwd = os.getcwd()
    root = git_root(cwd)
    handler = ReviewHandler(args, cwd, root)
    loop = Loop()
    loop.loop(handler)
    return handler.action


@result_handler()
def handle_result(args: list[str], answer: 'dict | None',
                  target_window_id: int, boss) -> None:
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
