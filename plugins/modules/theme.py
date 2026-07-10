"""Палитры подсветки синтаксиса: цвет для каждой роли токена.

Тему выбирает `familiar enable --theme`: она пишет в kitty.conf
`env FAMILIAR_THEME=<имя>`, и kitty передаёт переменную процессу
kitten. Неизвестное или пустое имя молча даёт DEFAULT — китен не
должен падать из-за опечатки в конфиге.

Значение роли — либо номер в 256-цветной палитре (int), либо
'#rrggbb'; hex превращается в kitty.fast_data_types.Color, который
styled() выводит как truecolor. Точные оттенки IDE иначе не
передать: 256-цветный куб их огрубляет.
"""

import os


DEFAULT = 'default'

# One Dark: оттенки, которыми kitty-киты жили до появления тем.
# 256-цвет — исторически: truecolor не у всех тем терминала ложится
# ровно, а 256 kitty рисует одинаково.
_DEFAULT_PALETTE = {
    'comment': 244,     # серый
    'doc': 114,         # докстринг — как строка
    'string': 114,      # зелёный
    'number': 173,      # оранжевый
    'const': 173,       # UPPER_CASE
    'kwconst': 173,     # True/False/None — та же роль, что у числа
    'keyword': 176,     # фиолетовый: if/for/def/return, and/or/not
    'func': 75,         # синий: имя функции — в объявлении и в вызове
    'cls': 180,         # песочный: классы, типы, исключения
    'decorator': 180,   # @property — та же роль, что у класса
    'self': 168,        # красноватый: self/this/cls, $var в php
    'builtin': 75,      # синий: len/print — это тоже функции
    'operator': 73,     # бирюзовый: = + - > и прочие знаки
    'punct': 145,       # тусклый: скобки, запятые, точки с запятой
    'error': 203,       # красный: то, что лексер не смог разобрать
}

# Darcula. Значения — из первоисточника JetBrains: схема "Darcula" в
# platform/platform-resources/src/DefaultColorSchemesManager.xml
# (intellij-community). Роли, которых в схеме нет (класс, оператор,
# скобки), наследуют цвет текста — так их и рисует IDE.
_DARCULA_PALETTE = {
    'comment': '#808080',    # DEFAULT_LINE_COMMENT
    'doc': '#629755',        # DEFAULT_DOC_COMMENT — зеленее строки
    'string': '#6a8759',     # DEFAULT_STRING
    'number': '#6897bb',     # DEFAULT_NUMBER
    'const': '#9876aa',      # DEFAULT_CONSTANT
    'kwconst': '#cc7832',    # None/True/False IDE красит как keyword
    'keyword': '#cc7832',    # DEFAULT_KEYWORD
    'func': '#ffc66d',       # DEFAULT_FUNCTION_DECLARATION
    'cls': '#a9b7c6',        # TEXT: имя класса в Darcula не выделено
    'decorator': '#bbb529',  # DEFAULT_METADATA
    'self': '#9876aa',       # DEFAULT_INSTANCE_FIELD
    'builtin': '#ffc66d',    # print/len — рисуем как функции
    'operator': '#a9b7c6',   # TEXT
    'punct': '#a9b7c6',      # TEXT
    'error': '#f0524f',      # CONSOLE_RED_OUTPUT
}

_PALETTES = {
    DEFAULT: _DEFAULT_PALETTE,
    'darcula': _DARCULA_PALETTE,
}

NAMES = tuple(_PALETTES)


def theme_name() -> str:
    return os.environ.get('FAMILIAR_THEME', '').strip().lower() or DEFAULT


def _rgb(spec: str):
    from kitty.fast_data_types import Color
    return Color(int(spec[1:3], 16), int(spec[3:5], 16), int(spec[5:7], 16))


def palette(name: 'str | None' = None) -> dict:
    """Роль токена → цвет для styled(): int (256-цвет) или Color."""
    raw = _PALETTES.get(name or theme_name(), _DEFAULT_PALETTE)
    return {role: _rgb(v) if isinstance(v, str) else v for role, v in raw.items()}
