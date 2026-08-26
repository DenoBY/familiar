"""Слова и идентификаторы в строке диффа.

Всё, что осталось от прежнего grep-резолвера: определить, по чему
кликнули. Само определение символа ищет language server (modules/lsp).
"""

import re


_IDENT = re.compile(r'[A-Za-z_]\w*')

# для выделения двойным кликом годится любое слово, включая кириллицу
# в комментариях — не только ASCII-идентификатор кода
_WORD = re.compile(r'\w+')


def _match_at(pattern: 're.Pattern', plain: str, col: int) -> 're.Match | None':
    if col < 0:
        return None
    for m in pattern.finditer(plain):
        if m.start() <= col < m.end() or col == m.end():
            return m
    return None


def word_span(plain: str, col: int) -> 'tuple[int, int] | None':
    """(start, end) слова под колонкой — выделение двойным кликом."""
    m = _match_at(_WORD, plain, col)
    return m.span() if m else None


def symbol_at(plain: str, col: int) -> 'str | None':
    """Идентификатор под колонкой."""
    m = _match_at(_IDENT, plain, col)
    return m.group() if m else None


def find_identifier(text: str, name: str) -> 'tuple[int, int] | None':
    """Первое вхождение `name` как отдельного слова: (строка с 1,
    индекс в строке).

    Нужно для удалённых строк: позиции в старой версии у сервера нет,
    но тот же идентификатор обычно есть и в новой — по нему и
    спрашиваем определение.
    """
    if not name:
        return None
    pattern = re.compile(r'(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])' % re.escape(name))
    for i, line in enumerate(text.splitlines(), start=1):
        m = pattern.search(line)
        if m:
            return i, m.start()
    return None
