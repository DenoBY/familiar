"""Строковый ввод в TUI (фильтр/поиск/комментарий/…), общий китам.

Миксин к Handler: владеет input_mode/input_buffer и разбирает
Enter/Esc/Backspace и печатаемый текст. Раньше эта машинерия
жила копиями в review/session/log, а базовый DiffTreeView
лез в неё через getattr — теперь состояние принадлежит миксину.

Хуки подкласса:
- _input_live() — применить ввод вживую (после каждого
  символа/Backspace);
  по умолчанию просто перерисовка;
- commit_input() — Enter; по умолчанию выйти из режима;
- _input_cancelled(mode) — откат состояния после Esc
  (режим/буфер уже сброшены);
- multiline_modes — режимы, где Shift+Enter даёт перенос строки,
  а длинный текст переносится по словам.
"""

from modules.text import wrap_text


CURSOR = '▏'


class InputLine:

    input_mode: 'str | None' = None
    input_buffer: str = ''
    multiline_modes: tuple = ()

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

    def input_lines(self, width: int) -> 'list[str]':
        """Буфер как визуальные строки: жёсткие переносы по \\n плюс
        мягкий перенос по словам. Последняя несёт курсор.
        """
        prefix = f' {self.input_mode}: '
        indent = ' ' * len(prefix)
        w = max(1, width - len(prefix) - len(CURSOR))
        out = []
        for i, logical in enumerate(self.input_buffer.split('\n')):
            for j, vis in enumerate(wrap_text(logical, w)):
                out.append((prefix if i == 0 and j == 0 else indent) + vis)
        out[-1] += CURSOR
        return out

    def input_kill_word(self) -> None:
        """Граница слова — пробел или перенос строки: на \\n стирание
        останавливается, чтобы не съесть предыдущую строку
        комментария целиком.
        """
        buf = self.input_buffer.rstrip(' ')
        cut = max(buf.rfind(' '), buf.rfind('\n'))
        self.input_buffer = buf[:cut + 1]
        self._input_live()

    def input_kill_all(self) -> None:
        """Ctrl+U — стереть весь буфер. В readline это «до начала
        строки», но у многострочного комментария полезнее снести
        сразу всё: последнюю строку и так добирают Ctrl+W.
        """
        self.input_buffer = ''
        self._input_live()

    def input_newline(self) -> bool:
        if self.input_mode not in self.multiline_modes:
            return False
        self.input_buffer += '\n'
        self._input_live()
        return True

    def input_key(self, key: str, shift: bool = False) -> bool:
        """Обработать клавишу в режиме ввода.

        False — режим не активен.
        """
        if not self.input_mode:
            return False
        if key == 'ENTER':
            if not (shift and self.input_newline()):
                self.commit_input()
        elif key == 'ESCAPE':
            self.cancel_input()
        elif key == 'BACKSPACE':
            self.input_buffer = self.input_buffer[:-1]
            self._input_live()
        return True

    def input_text(self, text: str) -> bool:
        """Дописать печатаемый текст в буфер.

        False — режим не активен.
        """
        if not self.input_mode:
            return False
        multi = self.input_mode in self.multiline_modes
        self.input_buffer += ''.join(
            ch for ch in text if ch.isprintable() or (multi and ch == '\n'))
        self._input_live()
        return True
