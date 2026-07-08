"""Атомарная перерисовка кадра TUI (DEC mode 2026, synchronized update).

Кадр у китов — clear_screen + построчная печать; без синхронизации терминал
успевает показать уже очищенный экран до прихода новых строк, и панели мигают
при каждом скролле. Режим 2026 просит kitty применить весь кадр целиком.

Подкласс реализует _draw_frame() — прежнее тело draw_screen.
"""

from kittens.tui.operations import Mode


class AtomicDraw:

    def draw_screen(self) -> None:
        self.cmd.set_mode(Mode.PENDING_UPDATE)
        try:
            self._draw_frame()
        finally:
            self.cmd.reset_mode(Mode.PENDING_UPDATE)
