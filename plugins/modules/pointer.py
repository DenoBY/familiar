"""Форма указателя мыши (OSC 22).

Пока включён mouse-tracking, kitty подменяет курсор стрелкой —
сигнал «мышь у приложения». В просмотрщике это скрывает подсказки:
над текстом уместен текстовый курсор (drag-select), над кликабельным
элементом — рука. Форму кладём на стек push/pop, чтобы на выходе из
зоны вернуть прежнюю, а не навязывать default. Имена форм — из набора
CSS-курсоров (text, pointer, …).
"""


def push_pointer(shape: str) -> str:
    return f'\x1b]22;>{shape}\x1b\\'


def pop_pointer() -> str:
    return '\x1b]22;<\x1b\\'


class PointerCursor:
    """Миксин к Handler: форма указателя по зоне под мышью.

    Подкласс реализует `_wanted_pointer(ev)` — имя CSS-курсора либо
    None (стрелка). `update_pointer` зовут из `on_mouse_event`,
    `reset_pointer` — из `finalize`. Форма живёт на стеке kitty, на
    смене зоны прежняя снимается, поэтому escape уходит только при
    переходе между зонами, а не на каждое движение.
    """

    _pointer_shape: 'str | None' = None

    def _wanted_pointer(self, ev) -> 'str | None':
        raise NotImplementedError

    def update_pointer(self, ev) -> None:
        want = self._wanted_pointer(ev)
        if want == self._pointer_shape:
            return
        if self._pointer_shape is not None:
            self.print(pop_pointer(), end='')
        if want is not None:
            self.print(push_pointer(want), end='')
        self._pointer_shape = want

    def reset_pointer(self) -> None:
        if self._pointer_shape is not None:
            self.print(pop_pointer(), end='')
            self._pointer_shape = None
