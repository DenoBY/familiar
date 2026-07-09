"""Выделение мышью, общее для diff-панели (review/log) и превью
session: drag внутри одной строки выделяет символы, через строки —
строки целиком.

Миксин к Handler: владеет состоянием drag'а и классификацией жеста; куда
положить результат и как показать координаты — задаёт подкласс хуками.
"""

from kittens.tui.loop import EventType, MouseButton


class DragSelect:

    _drag_anchor: 'int | None' = None
    _drag_anchor_col: int = 0
    _drag_moved: bool = False

    def _sel_row_at(self, ev) -> 'int | None':
        raise NotImplementedError

    def _sel_col_at(self, ev) -> int:
        return max(0, ev.cell_x)

    def _apply_char_sel(self, row: int, cs: int, ce: int) -> None:
        raise NotImplementedError

    def _apply_line_sel(self, lo: int, hi: int, row: int) -> None:
        raise NotImplementedError

    def _sel_done(self) -> None:
        pass

    def drag_select(self, ev) -> bool:
        """Обработать событие мыши как drag-выделение; False —
        событие не про выделение, его нужно отдать дальше (клик).
        """
        left = bool(ev.buttons & MouseButton.LEFT)
        if ev.type == EventType.PRESS and left:
            self._drag_anchor = self._sel_row_at(ev)
            self._drag_anchor_col = self._sel_col_at(ev)
            self._drag_moved = False
            return False
        if ev.type == EventType.MOVE and left and self._drag_anchor is not None:
            row = self._sel_row_at(ev)
            if row is None:
                return True
            if row == self._drag_anchor:
                lo, hi = sorted((self._drag_anchor_col, self._sel_col_at(ev)))
                if hi <= lo:
                    return True
                # правая граница включительно: символ под
                # курсором тоже выделен
                self._apply_char_sel(row, lo, hi + 1)
            else:
                lo, hi = sorted((self._drag_anchor, row))
                self._apply_line_sel(lo, hi, row)
            self._drag_moved = True
            self.draw_screen()
            return True
        if ev.type == EventType.RELEASE:
            moved, self._drag_anchor, self._drag_moved = self._drag_moved, None, False
            if moved:
                self._sel_done()
                self.draw_screen()
                return True
        return False
