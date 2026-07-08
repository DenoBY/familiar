"""Форматирование строк по ширине: общий модуль всех китов.

Лежит в корне пакета modules (как keylayout): нужен и vcs-китам (review/log),
и session — иначе одинаковые функции жили бы в двух util по копии.
"""

import os


HOME = os.path.expanduser('~')


def short_path(path: str) -> str:
    if path.startswith(HOME):
        return '~' + path[len(HOME):]
    return path


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


def wrap_text(text: str, width: int) -> list[str]:
    """Простой перенос по словам; длинные токены режем жёстко."""
    if width < 1:
        width = 1
    out = []
    cur = ''
    for w in text.split(' '):
        while len(w) > width:
            if cur:
                out.append(cur)
                cur = ''
            out.append(w[:width])
            w = w[width:]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += ' ' + w
        else:
            out.append(cur)
            cur = w
    out.append(cur)
    return out
