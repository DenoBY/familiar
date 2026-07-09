"""Форматирование строк по ширине: общий модуль всех китов.

Лежит в корне пакета modules (как keylayout): нужен и vcs-китам
(review/log), и session — иначе одинаковые функции жили бы в двух
util по копии.
"""

import os
from typing import Callable


HOME = os.path.expanduser('~')


def short_path(path: str) -> str:
    if path.startswith(HOME):
        return '~' + path[len(HOME):]
    return path


def plural(n: int, noun: str) -> str:
    """«1 line» / «2 lines» — счётчик с англ. множественным числом."""
    return f'{n} {noun}' if n == 1 else f'{n} {noun}s'


def truncate(s: str, width: int) -> str:
    if width <= 0:
        return ''
    if len(s) <= width:
        return s
    if width == 1:
        return '…'
    return s[:width - 1] + '…'


def pad(s: str, width: int) -> str:
    s = truncate(s, width)
    return s + ' ' * (width - len(s))


def wrap_words(
    words: 'list[list]',
    width: int,
    space: Callable[[object, 'object | None'], object],
) -> 'list[list]':
    """Перенос по словам над произвольными токенами; длинные слова
    режем жёстко.

    words — слова как списки элементов (символы, пары (символ, стиль)
    и т.п.); space(prev, nxt) — элемент-пробел между соседними словами
    (nxt может быть None для пустого слова). Общий движок wrap_text
    и markdown._wrap_chars.
    """
    if width < 1:
        width = 1
    lines: list[list] = []
    line: list = []
    for word in words:
        while len(word) > width:
            if line:
                lines.append(line)
                line = []
            lines.append(list(word[:width]))
            word = word[width:]
        if not line:
            line = list(word)
        elif len(line) + 1 + len(word) <= width:
            line.append(space(line[-1], word[0] if word else None))
            line += word
        else:
            lines.append(line)
            line = list(word)
    lines.append(line)
    return lines


def wrap_text(text: str, width: int) -> list[str]:
    """Простой перенос по словам; длинные токены режем жёстко."""
    words = [list(w) for w in text.split(' ')]
    return [''.join(line) for line in wrap_words(words, width, lambda a, b: ' ')]
