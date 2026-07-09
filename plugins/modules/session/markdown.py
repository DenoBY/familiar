"""Markdown ответов Claude → строки терминала: жирный, курсив,
код, списки, таблицы.

Разметка разбирается построчно и намеренно грубо: без вложенности и
без ссылок — столько, сколько видно в ответах ассистента. Каждая
строка отдаётся парой (plain, render): plain нужен поиску и подсветке
совпадений, render — готовый ANSI.

Fenced-блоки подсвечиваются общим лексером из modules.highlight.
"""

import re

from kittens.tui.operations import styled

from ..highlight import LANG_EXT, render_code
from ..text import truncate, wrap_words


# Инлайн-разметка. Вложенность не поддерживается: первый совпавший
# вариант побеждает.
_INLINE_RE = re.compile(
    r'`([^`]+)`'                    # `код`
    r'|\*\*([^*]+)\*\*'             # **жирный**
    r'|__([^_]+)__'                 # __жирный__
    r'|(?<![\w*])\*([^*\s][^*]*)\*(?![\w*])'   # *курсив*
)
_STYLE_BY_GROUP = {1: 'code', 2: 'bold', 3: 'bold', 4: 'italic'}
_STYLE_KWARGS = {
    'code': {'fg': 'cyan'},
    'bold': {'bold': True},
    'italic': {'italic': True},
    'head': {'bold': True},
}

_FENCE_RE = re.compile(r'^\s*```+\s*([\w+#.-]*)\s*$')
_HEAD_RE = re.compile(r'^(#{1,6})\s+(.*)$')
_BULLET_RE = re.compile(r'^(\s*)[-*+]\s+(.*)$')
_ORDERED_RE = re.compile(r'^(\s*)(\d+[.)])\s+(.*)$')

_TABLE_ROW_RE = re.compile(r'^\s*\|.*\|\s*$')
_TABLE_SEP_RE = re.compile(r'^\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*$')
_CELL_SPLIT_RE = re.compile(r'(?<!\\)\|')

CODE_INDENT = '  '

# Колонка уже этого нечитаема — таблицу целиком отдаём абзацем.
MIN_COL = 3


def _styled_chars(text: str, base: 'str | None' = None) -> list:
    """Текст → список (символ, стиль): маркеры разметки убраны."""
    chars = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        chars += [(ch, base) for ch in text[pos:m.start()]]
        group = m.lastindex
        chars += [(ch, _STYLE_BY_GROUP[group]) for ch in m.group(group)]
        pos = m.end()
    chars += [(ch, base) for ch in text[pos:]]
    return chars


def _joint_space(prev: tuple, nxt: 'tuple | None') -> tuple:
    # пробел внутри одного стиля остаётся стилизованным: иначе **a b**
    # распадается на два жирных куска с обычным пробелом посередине
    style = prev[1] if nxt is not None and prev[1] == nxt[1] else None
    return (' ', style)


def _wrap_chars(chars: list, width: int) -> list:
    """Перенос по словам над списком (символ, стиль).

    Движок общий с wrap_text — modules.text.wrap_words.
    """
    words, cur = [], []
    for pair in chars:
        if pair[0] == ' ':
            if cur:
                words.append(cur)
                cur = []
        else:
            cur.append(pair)
    if cur:
        words.append(cur)
    return wrap_words(words, width, _joint_space)


def _emit(chars: list, prefix: str = '') -> tuple:
    """Список (символ, стиль) → (plain, render).

    render=None, если стилей нет.
    """
    plain = prefix + ''.join(ch for ch, _ in chars)
    if not any(style for _, style in chars):
        return plain, None
    out = prefix
    i, n = 0, len(chars)
    while i < n:
        style = chars[i][1]
        j = i + 1
        while j < n and chars[j][1] == style:
            j += 1
        seg = ''.join(ch for ch, _ in chars[i:j])
        out += styled(seg, **_STYLE_KWARGS[style]) if style else seg
        i = j
    return plain, out


def _paragraph(text: str, width: int, first: str = '', cont: str = '') -> list:
    chars = _styled_chars(text)
    out = []
    for line in _wrap_chars(chars, max(1, width - len(cont))):
        out.append(_emit(line, first if not out else cont))
    return out


def _heading(text: str, width: int) -> tuple:
    chars = _styled_chars(text, base='head')
    if len(chars) > width:
        chars = chars[:width - 1] + [('…', 'head')]
    return _emit(chars)


def _code_line(code: str, ext: str, width: int) -> tuple:
    body = truncate(code, max(1, width - len(CODE_INDENT)))
    return CODE_INDENT + body, CODE_INDENT + render_code(body, ext)


def _split_row(raw: str) -> list[str]:
    body = raw.strip().strip('|')
    return [c.strip().replace('\\|', '|') for c in _CELL_SPLIT_RE.split(body)]


def _aligns(sep: str) -> list[str]:
    out = []
    for c in _split_row(sep):
        left, right = c.startswith(':'), c.endswith(':')
        out.append('center' if left and right else 'right' if right else 'left')
    return out


def _col_widths(rows: list, width: int, n: int) -> 'list[int] | None':
    """Ширины колонок ровно под width; None — таблица не влезает.

    Считаем по видимой длине (без маркеров разметки). Лишнее снимаем
    с самой широкой колонки, недостачу раздаём пропорционально — так
    таблица занимает всю ширину, как в Claude Code.
    """
    avail = width - (3 * n + 1)     # '│ ' на колонку + ' ' + закрывающая '│'
    if avail < n * MIN_COL:
        return None
    cols = [[len(_styled_chars(r[c])) if c < len(r) else 0 for r in rows]
            for c in range(n)]
    widths = [max(max(col), 1) for col in cols]
    while sum(widths) > avail:
        i = widths.index(max(widths))
        widths[i] -= 1
    extra = avail - sum(widths)
    if extra > 0:
        total = sum(widths)
        add = [extra * w // total for w in widths]
        for i in range(extra - sum(add)):
            add[i] += 1
        widths = [w + a for w, a in zip(widths, add)]
    return widths


_BORDER = {'top': '┌┬┐', 'mid': '├┼┤', 'bot': '└┴┘'}


def _rule(widths: list, kind: str) -> tuple:
    left, mid, right = _BORDER[kind]
    return left + mid.join('─' * (w + 2) for w in widths) + right, None


def _cell_lines(text: str, width: int, align: str) -> list:
    out = []
    for line in _wrap_chars(_styled_chars(text), width):
        gap = width - len(line)
        left = gap // 2 if align == 'center' else gap if align == 'right' else 0
        out.append([(' ', None)] * left + line + [(' ', None)] * (gap - left))
    return out


def _table_row(cells: list, widths: list, aligns: list) -> list:
    blocks = [_cell_lines(cells[c] if c < len(cells) else '', w, aligns[c])
              for c, w in enumerate(widths)]
    out = []
    for i in range(max(len(b) for b in blocks)):
        chars = []
        for c, block in enumerate(blocks):
            chars += [('│', None), (' ', None)]
            chars += block[i] if i < len(block) else [(' ', None)] * widths[c]
            chars.append((' ', None))
        chars.append(('│', None))
        out.append(_emit(chars))
    return out


def _table(raws: list, width: int) -> 'list | None':
    head = _split_row(raws[0])
    body = [_split_row(r) for r in raws[2:]]
    n = max(len(r) for r in [head, _split_row(raws[1])] + body)
    aligns = (_aligns(raws[1]) + ['left'] * n)[:n]
    widths = _col_widths([head] + body, width, n)
    if widths is None:
        return None
    out = [_rule(widths, 'top')]
    out += _table_row(head, widths, ['center'] * n)
    for row in body:
        # линейка перед каждой строкой: без неё многострочные ячейки
        # сливаются в одну простыню (и в Claude Code она есть)
        out.append(_rule(widths, 'mid'))
        out += _table_row(row, widths, aligns)
    out.append(_rule(widths, 'bot'))
    return out


def _table_at(raws: list, i: int) -> int:
    """Индекс за концом таблицы с началом на i; i — если её там нет."""
    if not (_TABLE_ROW_RE.match(raws[i]) and i + 1 < len(raws)
            and _TABLE_SEP_RE.match(raws[i + 1])):
        return i
    j = i + 2
    while j < len(raws) and _TABLE_ROW_RE.match(raws[j]):
        j += 1
    return j


def markdown_lines(text: str, width: int) -> list:
    """Разметка → список (plain, render) шириной не больше width."""
    width = max(10, width)
    out = []
    ext = None            # не None — мы внутри fenced-блока
    para: list[str] = []

    def flush():
        # абзац собираем целиком: в исходнике **жирный** переносится
        # через \n, и построчный разбор увидел бы половинку разметки
        if para:
            out.extend(_paragraph(' '.join(para), width))
            para.clear()

    raws = text.split('\n')
    i = 0
    while i < len(raws):
        raw = raws[i]
        i += 1
        fence = _FENCE_RE.match(raw)
        if fence:
            flush()
            ext = None if ext is not None else LANG_EXT.get(fence.group(1).lower(), '')
            continue
        if ext is not None:
            out.append(_code_line(raw, ext, width))
            continue
        if not raw.strip():
            flush()
            out.append(('', None))
            continue
        end = _table_at(raws, i - 1)
        if end > i - 1:
            table = _table(raws[i - 1:end], width)
            if table is not None:   # не влезла по ширине — покажем абзацем
                flush()
                out += table
                i = end
                continue
        head = _HEAD_RE.match(raw)
        if head:
            flush()
            out.append(_heading(head.group(2).strip(), width))
            continue
        bullet = _BULLET_RE.match(raw)
        if bullet:
            flush()
            pad = bullet.group(1)
            out += _paragraph(bullet.group(2), width, pad + '• ', pad + '  ')
            continue
        ordered = _ORDERED_RE.match(raw)
        if ordered:
            flush()
            pad, num = ordered.group(1), ordered.group(2)
            out += _paragraph(ordered.group(3), width,
                              f'{pad}{num} ', pad + ' ' * (len(num) + 1))
            continue
        para.append(raw.strip())
    flush()
    return out
