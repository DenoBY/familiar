"""Утилиты session-кита: возраст сессии, обрезка и перенос строк.

Без состояния и без обращения к диску (кроме вычисления HOME на импорте).
Раскладка клавиш — общая, из modules.keylayout.
"""

import os

from modules.keylayout import LAYOUT, to_latin  # noqa: F401  (ре-экспорт для session)

HOME = os.path.expanduser('~')


def human_age(seconds: float) -> str:
    m = seconds / 60
    if m < 1:
        return 'just now'
    if m < 60:
        return f'{int(m)}m ago'
    h = m / 60
    if h < 24:
        return f'{int(h)}h ago'
    d = h / 24
    if d < 30:
        return f'{int(d)}d ago'
    return f'{int(d / 30)}mo ago'


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
