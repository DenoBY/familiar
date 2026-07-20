"""Атомарная перерисовка кадра TUI (DEC mode 2026, synchronized update).

Кадр у китов — clear_screen + построчная печать; без синхронизации
терминал успевает показать уже очищенный экран до прихода новых
строк, и панели мигают при каждом скролле. Режим 2026 просит kitty
применить весь кадр целиком.

Подкласс реализует _draw_frame() — прежнее тело draw_screen. Если у
подкласса есть атрибут `flash` (сообщение поверх футера), кадр сам
заводит таймер на его снятие: `_draw_frame` гасит flash после
печати, но без нового кадра подсказки футера не вернулись бы до
следующего нажатия клавиши.
"""

from kittens.tui.operations import Mode


class AtomicDraw:

    FLASH_TTL = 2.5   # сек: успеть прочитать сообщение, но не мозолить глаза

    _flash_timer = None
    # (строка, колонка) каретки строки ввода, 0-based; выставляет
    # отрисовщик строки ввода через set_caret на каждом кадре
    _caret: 'tuple[int, int] | None' = None

    def set_caret(self, row: int, col: int) -> None:
        self._caret = (row, col)

    def draw_screen(self) -> None:
        shown = bool(getattr(self, 'flash', ''))
        self._caret = None
        self.cmd.set_mode(Mode.PENDING_UPDATE)
        try:
            self._draw_frame()
            # каретку рисует курсор терминала: глиф в тексте сдвигал
            # бы хвост строки на ячейку при каждом движении
            if self._caret is not None:
                row, col = self._caret
                self.print(f'\x1b[{row + 1};{col + 1}H', end='')
            self.cmd.set_cursor_visible(self._caret is not None)
        finally:
            self.cmd.reset_mode(Mode.PENDING_UPDATE)
        self._arm_flash_timer(shown)

    def _arm_flash_timer(self, shown: bool) -> None:
        if self._flash_timer is not None:
            self._flash_timer.cancel()   # новый кадр — старый отсчёт неактуален
            self._flash_timer = None
        loop = getattr(self, 'asyncio_loop', None)
        if not shown or loop is None:
            return
        self._flash_timer = loop.call_later(self.FLASH_TTL, self._flash_expired)

    def _flash_expired(self) -> None:
        self._flash_timer = None
        self.draw_screen()   # flash уже снят прошлым кадром — вернётся футер
