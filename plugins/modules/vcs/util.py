"""Утилиты vcs-китов: форматирование строк по ширине, статусы, IDE-шум.

Модуль без состояния и без зависимостей от git — только преобразования
строк и таблицы констант для отрисовки. Раскладка и текстовые примитивы
— общие, из modules.keylayout / modules.text.
"""

from kittens.tui.operations import styled

from ..keylayout import chord, to_latin  # noqa: F401  (ре-экспорт для review/log)
from ..text import pad, short_path, truncate  # noqa: F401


# Статус изменения → цвет имени в дереве файлов, в стиле IDE
# (букву статуса не печатаем — её несёт цвет).
STATUS_STYLE = {
    'modified':  'blue',
    'added':     'green',
    'deleted':   'gray',
    'renamed':   'cyan',
    'untracked': 'red',
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
