"""Утилиты session-кита: возраст сессии; обрезка и перенос
строк — из modules.text.

Без состояния и без обращения к диску. Раскладка клавиш —
общая, из modules.keylayout.
"""

from modules.keylayout import LAYOUT, to_latin  # noqa: F401  (ре-экспорт для session)
from modules.text import (  # noqa: F401  (ре-экспорт для session)
    HOME,
    pad,
    plural,
    short_path,
    truncate,
    wrap_text,
)


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
