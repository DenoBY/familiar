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
    _draw_pending = False
    # (строка, колонка) каретки строки ввода, 0-based; выставляет
    # отрисовщик строки ввода через set_caret на каждом кадре
    _caret: 'tuple[int, int] | None' = None

    def set_caret(self, row: int, col: int) -> None:
        self._caret = (row, col)

    def schedule_draw(self) -> None:
        """Слить подряд идущие перерисовки в один кадр.

        Колесо мыши и автоповтор клавиши приходят пачкой: kitty
        разбирает весь прочитанный буфер и зовёт колбэк на каждое
        событие. Кадр — это весь экран (у diff-панели ~5 КБ), и
        рисовать его на каждый щелчок значит слать сотни килобайт в
        тот же терминал; на заполненном буфере stdout запись
        блокируется, кит перестаёт читать ввод — со стороны это
        выглядит как зависание. Обработку событий не откладывает:
        кадр рисуется, как только разобрана очередь готовых.
        """
        if self._draw_pending:
            return
        loop = getattr(self, 'asyncio_loop', None)
        if loop is None:
            self.draw_screen()
            return
        self._draw_pending = True
        loop.call_soon(self._draw_coalesced)

    def _draw_coalesced(self) -> None:
        self._draw_pending = False
        self.draw_screen()

    def draw_screen(self) -> None:
        had_flash = bool(getattr(self, 'flash', ''))
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
        # только если кадр действительно погасил flash: при раннем
        # выходе _draw_frame (диалог, пикер) сообщение ещё на экране,
        # и таймер, взведённый вхолостую, перевзводился бы вечно
        self._arm_flash_timer(had_flash and not getattr(self, 'flash', ''))

    def _arm_flash_timer(self, consumed: bool) -> None:
        if self._flash_timer is not None:
            self._flash_timer.cancel()   # новый кадр — старый отсчёт неактуален
            self._flash_timer = None
        loop = getattr(self, 'asyncio_loop', None)
        if not consumed or loop is None:
            return
        self._flash_timer = loop.call_later(self.FLASH_TTL, self._flash_expired)

    def _flash_expired(self) -> None:
        self._flash_timer = None
        self.draw_screen()   # flash уже снят прошлым кадром — вернётся футер
