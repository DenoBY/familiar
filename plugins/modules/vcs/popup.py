"""Плашки поверх кода в режиме правки: список автодополнения, панель
документации и строка сигнатуры.

Чистая раскладка и рендер: на вход — что показать и где на экране
свободно, на выход — прямоугольник и готовые строки со стилями.
Печатает их редактор, абсолютным позиционированием поверх кадра.
"""

import re
from typing import NamedTuple

from kittens.tui.operations import styled

from ..highlight import (
    C_CLASS,
    C_CONST,
    C_FUNC,
    C_KEYWORD,
    C_SELF,
    C_STRING,
    MENU_BG,
    MENU_DIM,
    MENU_SEL_BG,
)
from ..text import truncate, wrap_text


MAX_ROWS = 10
LABEL_MAX = 60
DESC_MAX = 30
# пояснение уже этого не читается — лучше отдать место метке
DESC_MIN = 6
DOC_WIDTH = 60
DOC_HEIGHT = 12
# уже этого панель документации — сплошные переносы
DOC_MIN = 30

# ' kk ' слева от метки и ' ▐' справа (отступ и полоса прокрутки)
_LEFT = 4
_RIGHT = 2

_KIND_FG = {
    'm': C_FUNC, 'ƒ': C_FUNC, 'C': C_CLASS, 'I': C_CLASS, 'S': C_CLASS,
    'E': C_CLASS, 'T': C_CLASS, 'M': C_CLASS, 'v': C_SELF, 'p': C_SELF,
    'fd': C_SELF, 'c': C_CONST, 'e': C_CONST, 'kw': C_KEYWORD, 'sn': C_KEYWORD,
}

# SGR зачёркивания: у styled такого параметра нет во всех версиях kitty
_STRIKE, _NO_STRIKE = '\x1b[9m', '\x1b[29m'


class Pane(NamedTuple):
    top: int        # первая строка экрана, где можно рисовать
    bottom: int     # за последней
    left: int
    right: int      # за последней колонкой


class Box(NamedTuple):
    row: int
    col: int
    width: int
    height: int

    def contains(self, x: int, y: int) -> bool:
        return self.col <= x < self.col + self.width and self.row <= y < self.row + self.height


class MenuRow(NamedTuple):
    kind: str
    label: str
    label_detail: str
    description: str
    positions: 'tuple[int, ...]'
    deprecated: bool


def menu_widths(rows: 'list[MenuRow]') -> 'tuple[int, int]':
    label = max((len(r.label) + len(r.label_detail) for r in rows), default=0)
    desc = max((len(r.description) for r in rows), default=0)
    return min(LABEL_MAX, label), min(DESC_MAX, desc)


def place_menu(anchor_row: int, anchor_x: int, count: int, widths: 'tuple[int, int]',
               pane: Pane, above: 'bool | None') -> 'tuple[Box, bool] | None':
    """Прямоугольник списка у слова на строке `anchor_row`, метки —
    ровно под началом слова.

    `above` — сторона, выбранная при открытии: сузившийся по мере
    набора список не должен перепрыгивать с одной стороны на другую.
    """
    label_w, desc_w = widths
    height = min(MAX_ROWS, count)
    below_room = pane.bottom - anchor_row - 1
    above_room = anchor_row - pane.top
    if above is None:
        above = below_room < height and above_room > below_room
    height = min(height, above_room if above else below_room)
    width = min(_LEFT + label_w + (2 + desc_w if desc_w else 0) + _RIGHT,
                pane.right - pane.left)
    if height <= 0 or width <= _LEFT + _RIGHT:
        return None
    col = max(pane.left, min(anchor_x - _LEFT, pane.right - width))
    row = anchor_row - height if above else anchor_row + 1
    return Box(row, col, width, height), above


def menu_lines(rows: 'list[MenuRow]', box: Box, widths: 'tuple[int, int]',
               selected: int, top: int, total: int) -> 'list[str]':
    """Видимые строки списка: `rows` — окно с `top`, `selected` — номер
    выбранного во всём списке.
    """
    label_w, desc_w = _fit(box.width, widths)
    thumb = _thumb(box.height, top, total)
    out = []
    for i, row in enumerate(rows[:box.height]):
        bg = MENU_SEL_BG if top + i == selected else MENU_BG
        kind = styled(f' {row.kind:<2} ', fg=_KIND_FG.get(row.kind, MENU_DIM), bg=bg)
        label = _label(row, label_w, bg)
        desc = ''
        if desc_w:
            desc = styled('  ' + truncate(row.description, desc_w).rjust(desc_w),
                          fg=MENU_DIM, bg=bg)
        mark = '▐' if i in thumb else ' '
        out.append(kind + label + desc + styled(' ' + mark, fg=MENU_DIM, bg=bg))
    return out


def _fit(width: int, widths: 'tuple[int, int]') -> 'tuple[int, int]':
    label_w, desc_w = widths
    room = width - _LEFT - _RIGHT
    if desc_w and label_w + 2 + desc_w > room:
        desc_w = room - label_w - 2
        if desc_w < DESC_MIN:
            desc_w = 0
    return min(label_w, room - (2 + desc_w if desc_w else 0)), desc_w


def _label(row: MenuRow, width: int, bg: object) -> str:
    text = truncate(row.label + row.label_detail, width).ljust(width)
    hits = set(row.positions)
    parts = []
    for i, ch in enumerate(text):
        if i < len(row.label) and not (i == width - 1 and text.endswith('…')):
            parts.append(styled(ch, bold=_on(i in hits), bg=bg))
        else:
            parts.append(styled(ch, fg=MENU_DIM, bg=bg))
    label = ''.join(parts)
    return _STRIKE + label + _NO_STRIKE if row.deprecated else label


def _on(flag: bool) -> 'bool | None':
    """Флаг стиля для styled: False у kitty — не «без жирного», а
    `22` в начале и `1` в конце, то есть жирный включается следом.
    """
    return True if flag else None


def _thumb(height: int, top: int, total: int) -> 'range':
    if total <= height:
        return range(0)
    size = max(1, height * height // total)
    start = (height - size) * top // max(1, total - height)
    return range(start, start + size)


# --- документация ---

_FENCE = re.compile(r'^\s*```')
_MD_ESCAPE = re.compile(r'\\([\\`*_{}\[\]()#+\-.!|<>$])')
_MD_EMPHASIS = re.compile(r'(\*\*|__|\*|_)(\S(?:.*?\S)?)\1')


def doc_content(detail: str, documentation: str) -> 'list[tuple[str, bool]]':
    """Строки документации: (текст, это ли код). Markdown упрощённо —
    ограды кода и разметка выделения сняты: в терминальной плашке они
    только шум.
    """
    out: 'list[tuple[str, bool]]' = []
    if detail:
        out.append((detail, True))
    code = False
    for line in documentation.split('\n'):
        if _FENCE.match(line):
            code = not code
            continue
        if code:
            if line.strip() != '<?php':
                out.append((line.replace('\t', '    '), True))
            continue
        text = _MD_EMPHASIS.sub(r'\2', _MD_ESCAPE.sub(r'\1', line)).replace('`', '')
        if line.strip() in ('---', '***') and out:
            out.append(('', False))
            continue
        out.append((text, False))
    while out and not out[-1][0].strip():
        out.pop()
    return _squeeze_blank(out)


def _squeeze_blank(lines: 'list[tuple[str, bool]]') -> 'list[tuple[str, bool]]':
    out: 'list[tuple[str, bool]]' = []
    for text, code in lines:
        if not text.strip() and (not out or not out[-1][0].strip()):
            continue
        out.append((text, code))
    return out


def place_doc(menu: Box, pane: Pane, content: 'list[tuple[str, bool]]') -> 'Box | None':
    """Сбоку от списка: справа, если там хватает места, иначе слева."""
    if not content:
        return None
    want = min(DOC_WIDTH, max(len(t) for t, _ in content) + 2)
    right_room = pane.right - (menu.col + menu.width)
    left_room = menu.col - pane.left
    if right_room >= min(want, DOC_MIN):
        width = min(want, right_room)
        col = menu.col + menu.width
    elif left_room >= min(want, DOC_MIN):
        width = min(want, left_room)
        col = menu.col - width
    else:
        return None
    height = min(DOC_HEIGHT, pane.bottom - menu.row)
    if height <= 0:
        return None
    return Box(menu.row, col, width, height)


def doc_lines(content: 'list[tuple[str, bool]]', box: Box) -> 'list[str]':
    inner = box.width - 2
    wrapped: 'list[tuple[str, bool]]' = []
    for text, code in content:
        if code:
            wrapped.append((truncate(text, inner), True))
        else:
            wrapped.extend((part, False) for part in wrap_text(text, inner))
    out = []
    for i, (text, code) in enumerate(wrapped[:box.height]):
        fg = C_STRING if code else None
        out.append(styled(' ' + text.ljust(inner) + ' ', fg=fg, bold=_on(code and i == 0),
                          bg=MENU_BG))
    return out


# --- сигнатура ---

def signature_line(label: str, active: 'tuple[int, int] | None', width: int) -> str:
    """Сигнатура в ширину `width`, текущий параметр — жирным. Длинную
    режем с того края, где нет параметра, чтобы он оставался видим.
    """
    inner = max(1, width - 2)
    start = 0
    if len(label) > inner and active is not None and active[1] > inner - 1:
        start = min(active[0], len(label) - inner + 1)
    text = label[start:]
    prefix = '…' if start else ''
    text = truncate(prefix + text, inner)
    parts = [styled(' ', bg=MENU_BG)]
    shift = start - len(prefix)
    for i, ch in enumerate(text):
        pos = i + shift
        hot = active is not None and active[0] <= pos < active[1]
        parts.append(styled(ch, bold=_on(hot), underline='straight' if hot else None,
                            fg=None if hot else MENU_DIM, bg=MENU_BG))
    parts.append(styled(' ' * (inner - len(text) + 1), bg=MENU_BG))
    return ''.join(parts)
