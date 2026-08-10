"""Go-to-definition во вьюере: символ под курсором → файл:строка,
пикер при нескольких кандидатах, стек возврата (⌃o) и read-only показ
файла, которого нет среди изменений.

Миксин к DiffTreeView. Где искать определение, решает источник:
в рабочем дереве — по диску, в коммите — по его снимку (`source.rev`),
поэтому прыжок ведёт к тому же коду, что показан в диффе.
"""

import os

from kittens.tui.operations import styled

from ..text import short_path, truncate
from .diff import DiffSource, group_key
from .git import last_error
from .navdef import Target, resolve_definition, symbol_at, word_span


# бит Alt/Option в mouse-событии. kitty кодирует модификаторы мыши
# СВОЕЙ схемой (shift=1, alt=2, ctrl=4, super=8), а не xterm-SGR
# (где alt=8): проверено эмпирически — ⌥+click даёт mods=2. ⌘/Super
# мышью не приходит, поэтому go-to-definition — на ⌥+click.
ALT_MOD = 0b10


class GotoDefinitionMixin:

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # путь показанного read-only внешнего файла (None — обычный
        # дифф), стек «назад», активный пикер кандидатов
        self._external: 'str | None' = None
        self._navstack: list[dict] = []
        self._cand: 'list[Target] | None' = None
        self._cand_sym = ''
        self._goto_busy = False

    # --- поиск определения ---

    def _word_at(self, ev) -> 'tuple[str, bool, bool, str | None] | None':
        di = self._diff_row_at(ev)
        if di is None or not (0 <= di < len(self.diff_plain)):
            return None
        return symbol_at(self.diff_plain[di], self._diff_col_at(ev))

    def goto_definition(self, symbol: 'str | None', is_attr: bool = False,
                        is_call: bool = False, qualifier: 'str | None' = None) -> None:
        """Найти определение символа и перейти к нему.

        Поиск — repo-wide git grep, на монорепозитории это секунды:
        в колбэке ждать нельзя (замёрзли бы кадр и ⌃c), поэтому работа
        уходит в фоновый поток.
        """
        if not symbol or not self.root:
            return
        if self._goto_busy:
            return                       # предыдущий поиск ещё идёт
        cur_rel = self._external or (self.current_item() or {}).get('path')
        ext, source = self.diff_ext, self.diff_after
        rev = self.source.rev
        self._goto_busy = True
        self.flash = f"searching for '{symbol}'…"
        self.draw_screen()

        def work():
            targets = resolve_definition(
                self.root, cur_rel, ext, symbol, is_attr=is_attr,
                is_call=is_call, qualifier=qualifier, cur_source=source, rev=rev)
            return targets, last_error()

        self.run_background(work, lambda res: self._goto_done(symbol, *res))

    def _goto_done(self, symbol: str, targets: list, err: str) -> None:
        self._goto_busy = False
        if not targets:
            # таймаут/сбой git иначе выглядел бы уверенным «нет
            # определения» — молча неверный ответ
            self.flash = err or f"no definition for '{symbol}'"
            self.draw_screen()
            return
        if len(targets) == 1:
            self._navigate(targets[0])
        else:
            self._cand, self._cand_sym = targets, symbol
            self.draw_screen()

    def goto_from_selection(self) -> None:
        sel = self.diff_char_sel
        if not sel:
            self.flash = 'select a word (drag), then d'
            self.draw_screen()
            return
        row, cs, _ = sel
        if 0 <= row < len(self.diff_plain):
            ref = symbol_at(self.diff_plain[row], cs)
            if ref:
                self.goto_definition(*ref)

    def goto_click(self, ev) -> None:
        ref = self._word_at(ev)
        if ref:
            self.goto_definition(*ref)

    def goto_hover(self, di: int, col: int, mods: int) -> bool:
        """⌥ над идентификатором — строка кликабельна."""
        return bool(mods & ALT_MOD and col >= self._gutter_cols()
                    and word_span(self.diff_plain[di], col))

    # --- снимок позиции во вьюере ---

    def _capture_view(self) -> dict:
        """Куда вернуться после прыжка к определению (⌃o) или выхода
        из поиска по проекту.
        """
        return {'external': self._external, 'tsel': self.tsel,
                'diff_offset': self.diff_offset, 'diff_cur': self.diff_cur,
                'view_mode': self.view_mode, 'hscroll': self.hscroll,
                'left_offset': self.left_offset, 'focus': self.focus,
                'collapsed': set(self.collapsed)}

    def _restore_view(self, s: dict) -> None:
        """Обратная к _capture_view; дерево перестраивает сама.

        Порядок обязателен: view_mode — до load_diff (тот строит строки
        через build_diff_rows, который его читает), hscroll — после неё
        (раньше не известен hscroll_max), left_offset — последним:
        set_tsel по пути правит его под курсор.
        """
        self.collapsed = s.get('collapsed', self.collapsed)
        self.view_mode = s.get('view_mode', 'diff')
        self.rebuild_tree()
        if s.get('external'):
            self._show_file(s['external'], 0)
        else:
            self._external = None
            self.set_tsel(s.get('tsel', 0))
            self.load_diff()
        if s.get('hscroll') and self.hscroll != s['hscroll']:
            self.hscroll = min(s['hscroll'], self.hscroll_max)
            self.build_diff_rows()
        self.diff_cur = min(s.get('diff_cur', 0), max(0, len(self.diff_rows) - 1))
        self.diff_offset = min(s.get('diff_offset', 0), self.diff_offset_max())
        self.left_offset = s.get('left_offset', 0)
        self.focus = s.get('focus', 'tree')

    # --- переход ---

    def _reveal_file(self, rel: str) -> None:
        # раскрыть свёрнутых предков, чтобы файл появился строкой дерева
        it = next((x for x in self.filtered if x['path'] == rel), None)
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
            if r['type'] == 'file' and self.filtered[r['idx']]['path'] == rel:
                return i
        return None

    def _navigate(self, target: Target) -> None:
        self._navstack.append(self._capture_view())
        in_view = any(x['path'] == target.path for x in self.filtered)
        row = None
        if in_view:
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
        text = self.source.read(rel)
        self._external = rel
        self.diff_before = self.diff_after = text
        self.diff_ext = os.path.splitext(rel)[1].lower()
        self.diff_src = DiffSource(text, text)
        self.view_mode = 'final'
        self.hscroll = 0
        self.diff_sel = self.diff_char_sel = None
        self.expanded = {}
        self.build_diff_rows()
        self.focus = 'diff'
        self._center_on_line(line)

    def nav_back(self) -> None:
        if not self._navstack:
            self.flash = 'nothing to go back to'
            self.draw_screen()
            return
        self._restore_view(self._navstack.pop())
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
