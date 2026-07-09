"""Утилиты vcs-китов: форматирование строк по ширине, статусы, IDE-шум.

Модуль без состояния и без зависимостей от git — только преобразования
строк и таблицы констант для отрисовки. Раскладка и текстовые примитивы
— общие, из modules.keylayout / modules.text.
"""

from kittens.tui.operations import styled

from modules.keylayout import (  # noqa: F401  (ре-экспорт для review/log)
    LAYOUT,
    chord,
    to_latin,
)
from modules.text import HOME, pad, short_path, truncate  # noqa: F401


# Статус изменения → (буква, цвет) для дерева файлов, в стиле IDE.
STATUS_STYLE = {
    'modified':  ('M', 'blue'),
    'added':     ('A', 'green'),
    'deleted':   ('D', 'gray'),
    'renamed':   ('R', 'cyan'),
    'untracked': ('?', 'red'),
}

# Папки/файлы, скрытые по умолчанию (как «ignored» в IDE).
# Переключаются клавишей u.
NOISE_DIRS = {
    '.idea', '.vscode', '.git', '.DS_Store', 'node_modules', '__pycache__',
    '.venv', 'venv', 'dist', 'build', 'target', 'vendor', '.next', '.nuxt',
    '.pytest_cache', '.mypy_cache', '.gradle', '.cache', 'coverage',
}


def is_noise(rel: str) -> bool:
    return any(part in NOISE_DIRS for part in rel.split('/'))


def compose(segments: list[tuple[str, dict]], width: int) -> str:
    """Собрать строку из цветных сегментов ровно шириной width."""
    out, used = '', 0
    for text, style in segments:
        if used >= width:
            break
        t = truncate(text, width - used)
        out += styled(t, **style) if style else t
        used += len(t)
    if used < width:
        out += ' ' * (width - used)
    return out
