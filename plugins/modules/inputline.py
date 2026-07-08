"""Строковый ввод в TUI (фильтр/поиск/комментарий/…), общий для всех китов.

Миксин к Handler: владеет input_mode/input_buffer и разбирает Enter/Esc/Backspace
и печатаемый текст. Раньше эта машинерия жила копиями в review/session/log, а
базовый DiffTreeView лез в неё через getattr — теперь состояние принадлежит миксину.

Хуки подкласса:
- _input_live() — применить ввод вживую (после каждого символа/Backspace);
  по умолчанию просто перерисовка;
- commit_input() — Enter; по умолчанию выйти из режима;
- _input_cancelled(mode) — откат состояния после Esc (режим/буфер уже сброшены).
"""


class InputLine:

    input_mode: 'str | None' = None
    input_buffer: str = ''

    def start_input(self, mode: str, initial: str = '') -> None:
        self.input_mode = mode
        self.input_buffer = initial
        self.draw_screen()

    def _input_live(self) -> None:
        self.draw_screen()

    def commit_input(self) -> None:
        self.input_mode = None
        self.draw_screen()

    def _input_cancelled(self, mode: str) -> None:
        pass

    def cancel_input(self) -> None:
        mode = self.input_mode
        self.input_mode = None
        self.input_buffer = ''
        if mode:
            self._input_cancelled(mode)
        self.draw_screen()

    def input_key(self, key: str) -> bool:
        """Обработать клавишу в режиме ввода; False — режим не активен."""
        if not self.input_mode:
            return False
        if key == 'ENTER':
            self.commit_input()
        elif key == 'ESCAPE':
            self.cancel_input()
        elif key == 'BACKSPACE':
            self.input_buffer = self.input_buffer[:-1]
            self._input_live()
        return True

    def input_text(self, text: str) -> bool:
        """Дописать печатаемый текст в буфер; False — режим не активен."""
        if not self.input_mode:
            return False
        self.input_buffer += ''.join(ch for ch in text if ch.isprintable())
        self._input_live()
        return True
