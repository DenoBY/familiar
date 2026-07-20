"""Строковый ввод в TUI (фильтр/поиск/комментарий/…), общий китам.

Миксин к Handler: владеет input_mode/input_buffer/input_pos и
разбирает Enter/Esc/Backspace/Delete, стрелки ←/→ и Home/End
(каретка) и печатаемый текст. Каретку рисует ТЕРМИНАЛЬНЫЙ курсор
(см. AtomicDraw.set_caret): глиф-вставка в текст сдвигала бы хвост
строки на ячейку при каждом движении. Раньше эта машинерия жила
копиями в review/session/log — теперь состояние принадлежит миксину.

Хуки подкласса:
- _input_live() — применить ввод вживую (после каждого
  символа/Backspace);
  по умолчанию просто перерисовка;
- commit_input() — Enter; по умолчанию выйти из режима;
- _input_cancelled(mode) — откат состояния после Esc
  (режим/буфер уже сброшены);
- input_prefix() — подпись поля (session подписывает поля не
  именем режима);
- multiline_modes — режимы, где Shift+Enter даёт перенос строки,
  а длинный текст переносится по словам.
"""

from typing import ClassVar

from .text import wrap_words


def _space_pair(prev: tuple, nxt: 'tuple | None') -> tuple:
    # индекс пробела-разделителя восстанавливается по соседям:
    # сам пробел потерян при split
    return (' ', nxt[1] - 1 if nxt is not None else prev[1] + 1)


class InputLine:

    input_mode: 'str | None' = None
    input_buffer: str = ''
    input_pos: int = 0
    multiline_modes: ClassVar[tuple[str, ...]] = ()

    def start_input(self, mode: str, initial: str = '') -> None:
        self.input_mode = mode
        self.input_buffer = initial
        self.input_pos = len(initial)
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
        self.input_pos = 0
        if mode:
            self._input_cancelled(mode)
        self.draw_screen()

    def input_prefix(self) -> str:
        return f' {self.input_mode}: '

    def input_layout(self, width: int) -> 'tuple[list[str], int, int]':
        """Визуальные строки ввода (жёсткие переносы по \\n плюс
        мягкий перенос по словам) и (строка, колонка) каретки в них.

        Вёрстка и позиция считаются одним проходом wrap_words: при
        переносе пробел-разделитель исчезает из вёрстки, и позицию
        каретки по длинам готовых строк было бы не восстановить.
        """
        prefix = self.input_prefix()
        indent = ' ' * len(prefix)
        # минус ячейка: каретке за последним символом полной строки
        w = max(1, width - len(prefix) - 1)
        lines: list[str] = []
        caret: 'tuple[int, int] | None' = None
        base = 0
        for logical in self.input_buffer.split('\n'):
            words, idx = [], base
            for word in logical.split(' '):
                words.append([(ch, idx + k) for k, ch in enumerate(word)])
                idx += len(word) + 1
            here = caret is None and base <= self.input_pos <= base + len(logical)
            for pairs in wrap_words(words, w, _space_pair):
                head = prefix if not lines else indent
                if here and caret is None:
                    for col, (_ch, i) in enumerate(pairs):
                        if i >= self.input_pos:
                            caret = (len(lines), len(head) + col)
                            break
                lines.append(head + ''.join(ch for ch, _ in pairs))
            if here and caret is None:
                # за последним символом (или на \n в конце строки)
                caret = (len(lines) - 1, len(lines[-1]))
            base += len(logical) + 1
        if caret is None:
            caret = (len(lines) - 1, len(lines[-1]))
        return lines, caret[0], caret[1]

    def input_lines(self, width: int) -> 'list[str]':
        return self.input_layout(width)[0]

    def input_kill_word(self) -> None:
        """Граница слова — пробел или перенос строки: на \\n стирание
        останавливается, чтобы не съесть предыдущую строку
        комментария целиком. Стирается слово слева от каретки,
        хвост за ней остаётся.
        """
        head = self.input_buffer[:self.input_pos].rstrip(' ')
        cut = max(head.rfind(' '), head.rfind('\n')) + 1
        self.input_buffer = head[:cut] + self.input_buffer[self.input_pos:]
        self.input_pos = cut
        self._input_live()

    def input_kill_all(self) -> None:
        """Ctrl+U — стереть весь буфер. В readline это «до начала
        строки», но у многострочного комментария полезнее снести
        сразу всё: последнюю строку и так добирают Ctrl+W.
        """
        self.input_buffer = ''
        self.input_pos = 0
        self._input_live()

    def input_newline(self) -> bool:
        if self.input_mode not in self.multiline_modes:
            return False
        self._insert('\n')
        return True

    def _insert(self, text: str) -> None:
        self.input_buffer = (self.input_buffer[:self.input_pos] + text
                             + self.input_buffer[self.input_pos:])
        self.input_pos += len(text)
        self._input_live()

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
            if self.input_pos:
                self.input_buffer = (self.input_buffer[:self.input_pos - 1]
                                     + self.input_buffer[self.input_pos:])
                self.input_pos -= 1
            self._input_live()
        elif key == 'DELETE':
            self.input_buffer = (self.input_buffer[:self.input_pos]
                                 + self.input_buffer[self.input_pos + 1:])
            self._input_live()
        elif key == 'LEFT':
            self.input_pos = max(0, self.input_pos - 1)
            self.draw_screen()
        elif key == 'RIGHT':
            self.input_pos = min(len(self.input_buffer), self.input_pos + 1)
            self.draw_screen()
        elif key == 'HOME':
            self.input_pos = 0
            self.draw_screen()
        elif key == 'END':
            self.input_pos = len(self.input_buffer)
            self.draw_screen()
        return True

    def input_text(self, text: str) -> bool:
        """Вписать печатаемый текст в позицию каретки.

        False — режим не активен.
        """
        if not self.input_mode:
            return False
        multi = self.input_mode in self.multiline_modes
        self._insert(''.join(
            ch for ch in text if ch.isprintable() or (multi and ch == '\n')))
        return True
