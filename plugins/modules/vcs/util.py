"""Утилиты vcs-китов: сборка цветной строки, статусы, IDE-шум.

Модуль без состояния и без зависимостей от git — только преобразования
строк и таблицы констант для отрисовки. Раскладку и текстовые примитивы
потребители берут напрямую из modules.keylayout / modules.text.
"""

from kittens.tui.operations import styled

from ..text import truncate


# Статус изменения → цвет имени в дереве файлов, в стиле IDE
# (букву статуса не печатаем — её несёт цвет). None — цвет
# терминала по умолчанию: результат поиска — обычный файл.
STATUS_STYLE = {
    'modified':  'blue',
    'added':     'green',
    'deleted':   'gray',
    'renamed':   'cyan',
    'untracked': 'red',
    'match':     None,
}

# Папки/файлы, скрытые по умолчанию (как «ignored» в IDE).
# Переключаются клавишей u.
NOISE_DIRS = {
    '.idea', '.vscode', '.git', '.DS_Store', 'node_modules', '__pycache__',
    '.venv', 'venv', '.next', '.nuxt', '.pytest_cache', '.mypy_cache',
    '.gradle', '.cache',
}

# Артефакты с именами, которые встречаются и у исходников: Laravel
# держит опубликованные шаблоны пакетов в resources/views/vendor, и
# скрывать их нельзя. Такие имена — шум только в корне репозитория.
NOISE_ROOTS = {'vendor', 'dist', 'build', 'target', 'coverage'}


def is_noise(rel: str) -> bool:
    root, sep, _ = rel.partition('/')
    if sep and root in NOISE_ROOTS:
        return True
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
