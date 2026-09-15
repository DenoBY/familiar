"""Сниппеты LSP: текст для вставки и позиции плейсхолдеров.

Грамматика — из спеки LSP 3.17 (раздел Snippet Syntax). Сессию
переходов по плейсхолдерам ведёт редактор; здесь только разбор.
"""

import re
from typing import NamedTuple


class Stop(NamedTuple):
    index: int      # номер табстопа; 0 — финальная позиция каретки
    start: int      # смещения в развёрнутом тексте
    end: int


_INT = re.compile(r'\d+')
_VAR = re.compile(r'[_a-zA-Z][_a-zA-Z0-9]*')


class _SnippetError(Exception):
    pass


class _Parser:
    def __init__(self, text: str, variables: 'dict[str, str]') -> None:
        self.s = text
        self.i = 0
        self.out: 'list[str]' = []
        self.size = 0
        self.vars = variables
        self.stops: 'dict[int, Stop]' = {}

    def emit(self, text: str) -> None:
        self.out.append(text)
        self.size += len(text)

    def parse(self, stop_at: str = '') -> None:
        """Разобрать до `stop_at` (не съедая его) или до конца."""
        s = self.s
        while self.i < len(s):
            ch = s[self.i]
            if stop_at and ch in stop_at:
                return
            if ch == '\\' and self.i + 1 < len(s) and s[self.i + 1] in '$}\\':
                self.emit(s[self.i + 1])
                self.i += 2
            elif ch == '$':
                self.dollar()
            else:
                self.emit(ch)
                self.i += 1
        if stop_at:
            raise _SnippetError

    def dollar(self) -> None:
        s = self.s
        self.i += 1
        num = _INT.match(s, self.i)
        if num:
            self.i = num.end()
            self.stop(int(num.group()), self.size, self.size)
            return
        var = _VAR.match(s, self.i)
        if var:
            self.i = var.end()
            name = var.group()
            # неизвестная переменная — чаще всего неэкранированный
            # PHP-код ($this), а не опечатка: оставляем как есть
            self.emit(self.vars.get(name, '$' + name))
            return
        if self.i < len(s) and s[self.i] == '{':
            self.i += 1
            self.braced()
            return
        self.emit('$')

    def braced(self) -> None:
        s = self.s
        num = _INT.match(s, self.i)
        if num:
            self.i = num.end()
            index = int(num.group())
            start = self.size
            if s.startswith('}', self.i):
                self.i += 1
            elif s.startswith(':', self.i):
                self.i += 1
                self.parse('}')
                self.i += 1
            elif s.startswith('|', self.i):
                self.i += 1
                self.emit(self.choice())
            else:
                raise _SnippetError
            self.stop(index, start, self.size)
            return
        var = _VAR.match(s, self.i)
        if not var:
            raise _SnippetError
        self.i = var.end()
        value = self.vars.get(var.group())
        if s.startswith('}', self.i):
            self.i += 1
            self.emit(value or '')
        elif s.startswith(':', self.i):
            self.i += 1
            mark = len(self.out), self.size
            self.parse('}')
            self.i += 1
            if value:
                del self.out[mark[0]:]
                self.size = mark[1]
                self.emit(value)
        elif s.startswith('/', self.i):
            # трансформация по регэкспу: без переменной ей не над чем
            # работать, а переменных мы почти не знаем
            self.i = self.closing_brace() + 1
            self.emit(value or '')
        else:
            raise _SnippetError

    def closing_brace(self) -> int:
        """Индекс `}`, закрывающей текущий `${…`, с учётом
        вложенных `${…}` в формате трансформации.
        """
        s, i, depth = self.s, self.i, 1
        while i < len(s):
            if s[i] == '\\':
                i += 2
                continue
            if s.startswith('${', i):
                depth += 1
                i += 2
                continue
            if s[i] == '}':
                depth -= 1
                if not depth:
                    return i
            i += 1
        raise _SnippetError

    def choice(self) -> str:
        s = self.s
        options, cur = [], []
        while self.i < len(s):
            ch = s[self.i]
            if ch == '\\' and self.i + 1 < len(s) and s[self.i + 1] in '$}\\,|':
                cur.append(s[self.i + 1])
                self.i += 2
            elif ch == ',':
                options.append(''.join(cur))
                cur = []
                self.i += 1
            elif s.startswith('|}', self.i):
                options.append(''.join(cur))
                self.i += 2
                return options[0]
            else:
                cur.append(ch)
                self.i += 1
        raise _SnippetError

    def stop(self, index: int, start: int, end: int) -> None:
        # зеркала (тот же номер ещё раз) — только первое вхождение:
        # правка одного места не повторяется в остальных
        if index not in self.stops:
            self.stops[index] = Stop(index, start, end)


def expand(text: str, variables: 'dict[str, str] | None' = None) -> 'tuple[str, list[Stop]]':
    """(текст, плейсхолдеры по порядку обхода; `$0` — последним).

    Сломанный сниппет не роняет вставку: возвращается текст со снятым
    экранированием и без плейсхолдеров.
    """
    parser = _Parser(text, variables or {})
    try:
        parser.parse()
    except _SnippetError:
        return re.sub(r'\\([$}\\])', r'\1', text), []
    stops = sorted(parser.stops.values(), key=lambda st: (st.index == 0, st.index))
    return ''.join(parser.out), stops
