"""Каркас кита-оверлея: общий стек миксинов и жизненный цикл.

Все три кита собирают один и тот же набор поведений (диалог выхода,
атомарный кадр, строка ввода, выделение мышью, форма указателя) и
один и тот же жизненный цикл: открыться без курсора, прочитать своё
состояние, показать подсказку об обновлении, на выходе вернуть курсор
и указатель. Забытый в новом ките элемент не ломает импорт и не виден
тестам — он просто молча не работает, поэтому набор собран здесь.

Диалог подтверждения обязан получать ввод раньше кита, а логика кита
— раньше базового Handler; чтобы этот порядок не приходилось
воспроизводить в каждом ките, мышь и указатель разведены на пару
«шаблон + хук»: подкласс переопределяет `_on_mouse`/`_pointer_for`,
а пролог диалога остаётся здесь.

Хуки подкласса: `load_state` (старт), `_draw_frame` (кадр, AtomicDraw),
`_on_mouse` и `_pointer_for` (мышь вне диалога).
"""

import threading
from typing import Callable

from kittens.tui.handler import Handler
from kittens.tui.operations import MouseTracking

from .confirm import ConfirmQuit
from .dragselect import DragSelect
from .draw import AtomicDraw
from .inputline import InputLine
from .pointer import PointerCursor
from .update import start_check, update_hint


class OverlayHandler(ConfirmQuit, AtomicDraw, InputLine, DragSelect,
                     PointerCursor, Handler):

    # full (не buttons_and_drag): нужны события движения без нажатой
    # кнопки — иначе не поймать наведение для смены формы указателя.
    mouse_tracking = MouseTracking.full

    def load_state(self) -> None:
        raise NotImplementedError

    def initialize(self) -> None:
        self.cmd.set_cursor_visible(False)
        self.load_state()
        self.flash = update_hint() or ''
        start_check()
        self.draw_screen()

    def finalize(self) -> None:
        self.cmd.set_cursor_visible(True)
        self.reset_pointer()

    def run_background(self, work: Callable[[], object],
                       done: Callable[[object], None]) -> None:
        """Долгую работу — в демон-поток, результат — обратно в loop.

        Не run_in_executor: его дефолтный пул держит обычные потоки,
        и atexit join'ит их на выходе — закрытие кита посреди работы
        ждало бы её конца (у сетевых команд это до минуты чёрного
        оверлея). Демон-поток на выходе просто бросается.
        """
        def runner() -> None:
            result = work()
            loop = getattr(self, 'asyncio_loop', None)
            if loop is None:
                return     # цикла уже нет — показывать результат некому
            try:
                loop.call_soon_threadsafe(done, result)
            except RuntimeError:
                pass       # кит уже закрыт

        threading.Thread(target=runner, daemon=True).start()

    def _wanted_pointer(self, ev) -> 'str | None':
        if self.confirm_active:
            return self.confirm_pointer(ev)
        return self._pointer_for(ev)

    def _pointer_for(self, ev) -> 'str | None':
        return None

    def on_mouse_event(self, ev) -> None:
        if self.confirm_click(ev):
            return
        self._on_mouse(ev)

    def _on_mouse(self, ev) -> None:
        super().on_mouse_event(ev)
