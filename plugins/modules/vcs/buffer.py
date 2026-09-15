"""Текст правки в final-view: строки, каретка, выделение и история.

Чистая модель без TUI и git — режим правки review (editmode) рисует её
через обычную модель строк диффа. Колонки двух сортов: сырые (индекс
символа в строке файла) и экранные — табы развёрнуты в TAB_WIDTH
пробелов, как в diff_plain.
"""

import re

from ..lsp.position import TAB_WIDTH, raw_index


# Разделители, по которым режут строки str.splitlines и лексер
# подсветки, кроме \n. Строки буфера обязаны один в один совпадать со
# строками экрана, поэтому файл с ними не правим, а при вводе их
# выбрасываем. \r сюда же: CRLF при записи молча стал бы LF.
_FOREIGN_BREAKS = re.compile('[\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]')

# Глубины хватает на осмысленный откат; снимки держат ссылки на строки,
# а не копии текста, но список на каждый шаг всё равно не бесплатен.
UNDO_LIMIT = 200

_WORD = re.compile(r'\w')

Pos = tuple[int, int]


def decode_editable(raw: bytes) -> 'str | None':
    """Текст файла, если его можно править без порчи, иначе None:
    не-UTF-8 после записи потерял бы байты, а чужие переводы строк
    разъехались бы со строками экрана.
    """
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return None
    return None if _FOREIGN_BREAKS.search(text) else text


def line_splice(old: list[str], new: list[str]) -> tuple[int, int, int]:
    """(lo, old_n, new_n): new получен из old заменой old[lo:lo+old_n]
    на new[lo:lo+new_n]. Годится для любой правки — набора, вставки,
    отката — без знания, что именно случилось.
    """
    n = min(len(old), len(new))
    lo = 0
    while lo < n and old[lo] == new[lo]:
        lo += 1
    k = 0
    while k < n - lo and old[-1 - k] == new[-1 - k]:
        k += 1
    return lo, len(old) - lo - k, len(new) - lo - k


def _expand(s: str) -> str:
    return s.replace('\t', ' ' * TAB_WIDTH)


class TextBuffer:

    def __init__(self, text: str) -> None:
        self.lines = text.split('\n')
        # хвостовой перевод строки — свойство файла, а не пустая
        # последняя строка: иначе в конце файла висела бы лишняя строка
        self.eol = len(self.lines) > 1 and self.lines[-1] == ''
        if self.eol:
            self.lines.pop()
        # вырезано всё — файл пустой, а не «одна пустая строка»; но
        # стоит тексту вернуться — вернётся и перевод, какой был у файла
        self._file_eol = self.eol
        self.line = 0
        self.col = 0
        self.anchor: 'Pos | None' = None
        # экранная колонка, к которой тянутся ↑/↓: короткая строка по
        # пути не должна сбивать каретку влево насовсем
        self.goal: 'int | None' = None
        self.unit = '\t' if any(s.startswith('\t') for s in self.lines) else ' ' * TAB_WIDTH
        self.saved = text
        self._undo: list[tuple] = []
        self._redo: list[tuple] = []
        # вид последней правки: набранное подряд откатывается разом
        self._group: 'str | None' = None

    # --- текст ---

    def text(self) -> str:
        return '\n'.join(self.lines) + ('\n' if self.eol else '')

    def display_text(self) -> str:
        """Текст для модели строк: всегда с хвостовым переводом, чтобы
        splitlines давал ровно строку экрана на строку буфера — и у
        пустого буфера тоже.
        """
        return '\n'.join(self.lines) + '\n'

    @property
    def modified(self) -> bool:
        return self.text() != self.saved

    def mark_saved(self) -> None:
        self.saved = self.text()

    @property
    def caret(self) -> Pos:
        return self.line, self.col

    def display_col(self, line: int, col: int) -> int:
        return len(_expand(self.lines[line][:col]))

    def col_at_display(self, line: int, dcol: int) -> int:
        return raw_index(self.lines[line], dcol)

    # --- выделение ---

    def selection(self) -> 'tuple[Pos, Pos] | None':
        if self.anchor is None or self.anchor == self.caret:
            return None
        return min(self.anchor, self.caret), max(self.anchor, self.caret)

    def selected_text(self) -> str:
        sel = self.selection()
        if sel is None:
            return ''
        (la, ca), (lb, cb) = sel
        if la == lb:
            return self.lines[la][ca:cb]
        return '\n'.join([self.lines[la][ca:], *self.lines[la + 1:lb], self.lines[lb][:cb]])

    def set_caret(self, line: int, col: int, extend: bool = False) -> None:
        self._begin_move(extend)
        self.line = max(0, min(line, len(self.lines) - 1))
        self.col = max(0, min(col, len(self.lines[self.line])))
        self.goal = None

    def select(self, anchor: Pos, caret: Pos) -> None:
        self.set_caret(*anchor)
        self.set_caret(*caret, extend=True)

    def select_all(self) -> None:
        self.select((0, 0), (len(self.lines) - 1, len(self.lines[-1])))

    # --- движение ---

    def _begin_move(self, extend: bool) -> None:
        self._group = None
        if not extend:
            self.anchor = None
        elif self.anchor is None:
            self.anchor = self.caret

    def move(self, kind: str, extend: bool = False, page: int = 1) -> None:
        sel = self.selection()
        if not extend and sel is not None and kind in ('left', 'right'):
            # стрелка по выделению схлопывает его к краю, как везде
            self.set_caret(*(sel[0] if kind == 'left' else sel[1]))
            return
        self._begin_move(extend)
        if kind in ('up', 'down', 'page_up', 'page_down'):
            step = page if kind.startswith('page') else 1
            self._vertical(-step if kind in ('up', 'page_up') else step)
            return
        self.goal = None
        line, col = self.line, self.col
        text = self.lines[line]
        if kind == 'left':
            if col:
                col -= 1
            elif line:
                line -= 1
                col = len(self.lines[line])
        elif kind == 'right':
            if col < len(text):
                col += 1
            elif line < len(self.lines) - 1:
                line, col = line + 1, 0
        elif kind == 'home':
            # умный Home: сначала к коду, повторно — к началу строки
            indent = len(text) - len(text.lstrip())
            col = 0 if col == indent else indent
        elif kind == 'end':
            col = len(text)
        elif kind == 'word_left':
            line, col = self._word_left(line, col)
        elif kind == 'word_right':
            line, col = self._word_right(line, col)
        elif kind == 'doc_start':
            line, col = 0, 0
        elif kind == 'doc_end':
            line = len(self.lines) - 1
            col = len(self.lines[line])
        self.line, self.col = line, col

    def _vertical(self, delta: int) -> None:
        if self.goal is None:
            self.goal = self.display_col(self.line, self.col)
        line = max(0, min(self.line + delta, len(self.lines) - 1))
        if line == self.line:
            # упёрлись в край — дойти до начала/конца строки
            self.col = 0 if delta < 0 else len(self.lines[line])
            return
        self.line = line
        self.col = self.col_at_display(line, self.goal)

    def _word_left(self, line: int, col: int) -> Pos:
        if col == 0:
            return (line - 1, len(self.lines[line - 1])) if line else (0, 0)
        text = self.lines[line]
        while col and text[col - 1].isspace():
            col -= 1
        word = bool(col) and bool(_WORD.match(text[col - 1]))
        while col and not text[col - 1].isspace() and bool(_WORD.match(text[col - 1])) == word:
            col -= 1
        return line, col

    def _word_right(self, line: int, col: int) -> Pos:
        text = self.lines[line]
        if col == len(text):
            return (line + 1, 0) if line < len(self.lines) - 1 else (line, col)
        while col < len(text) and text[col].isspace():
            col += 1
        word = col < len(text) and bool(_WORD.match(text[col]))
        while (col < len(text) and not text[col].isspace()
               and bool(_WORD.match(text[col])) == word):
            col += 1
        return line, col

    # --- правка ---

    def _checkpoint(self, group: 'str | None' = None) -> None:
        if group is not None and group == self._group:
            return
        self._undo.append(self._snapshot())
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()
        self._group = group

    def _snapshot(self) -> tuple:
        return list(self.lines), self.caret, self.anchor, self.eol

    def _settle_eol(self) -> None:
        self.eol = self._file_eol and self.lines != ['']

    def _delete_range(self, a: Pos, b: Pos) -> None:
        (la, ca), (lb, cb) = a, b
        self.lines[la:lb + 1] = [self.lines[la][:ca] + self.lines[lb][cb:]]
        self.line, self.col = la, ca
        self.anchor = None
        self.goal = None
        self._settle_eol()

    def _delete_selection(self) -> bool:
        sel = self.selection()
        if sel is None:
            self.anchor = None
            return False
        self._delete_range(*sel)
        return True

    def insert(self, text: str, group: 'str | None' = None) -> None:
        """Вставить текст на место каретки (или выделения). group —
        вид правки для склейки истории: подряд набранные символы
        одного вида откатываются разом.
        """
        text = _FOREIGN_BREAKS.sub('', text.replace('\r\n', '\n').replace('\r', '\n'))
        if not text and self.selection() is None:
            return
        self._checkpoint(group)
        self._delete_selection()
        cur = self.lines[self.line]
        head, tail = cur[:self.col], cur[self.col:]
        parts = text.split('\n')
        if len(parts) == 1:
            self.lines[self.line] = head + text + tail
            self.col += len(text)
        else:
            self.lines[self.line:self.line + 1] = (
                [head + parts[0], *parts[1:-1], parts[-1] + tail])
            self.line += len(parts) - 1
            self.col = len(parts[-1])
        self.goal = None
        self._settle_eol()

    def type_char(self, ch: str) -> None:
        # слово откатывается целиком, пробелы между словами — отдельно
        self.insert(ch, 'space' if ch.isspace() else 'type')

    def newline(self) -> None:
        cur = self.lines[self.line]
        indent = cur[:len(cur) - len(cur.lstrip())][:self.col]
        self.insert('\n' + indent)

    def backspace(self) -> None:
        if self.selection() is not None:
            self._checkpoint()
            self._delete_selection()
        elif self.col:
            self._checkpoint('del')
            self._delete_range((self.line, self.col - 1), self.caret)
        elif self.line:
            self._checkpoint('del')
            self._delete_range((self.line - 1, len(self.lines[self.line - 1])), self.caret)

    def delete(self) -> None:
        if self.selection() is not None:
            self._checkpoint()
            self._delete_selection()
        elif self.col < len(self.lines[self.line]):
            self._checkpoint('del')
            self._delete_range(self.caret, (self.line, self.col + 1))
        elif self.line < len(self.lines) - 1:
            self._checkpoint('del')
            self._delete_range(self.caret, (self.line + 1, 0))

    def delete_word_left(self) -> None:
        if self.selection() is not None or self.caret == (0, 0):
            self.backspace()
            return
        self._checkpoint()
        self._delete_range(self._word_left(*self.caret), self.caret)

    def _block(self) -> range:
        """Строки под выделением; строка, где оно кончается в колонке 0,
        не в счёт — так выделяют строки целиком.
        """
        sel = self.selection()
        if sel is None:
            return range(self.line, self.line + 1)
        (la, _), (lb, cb) = sel
        return range(la, lb if (cb == 0 and lb > la) else lb + 1)

    def indent(self) -> None:
        sel = self.selection()
        if sel is None or sel[0][0] == sel[1][0]:
            self.insert(self.unit)
            return
        self._checkpoint()
        block = self._block()
        for i in block:
            if self.lines[i]:
                self.lines[i] = self.unit + self.lines[i]
        self._shift_cols(block, len(self.unit))

    def outdent(self) -> None:
        block = self._block()
        cuts = {i: self._indent_cut(self.lines[i]) for i in block}
        if not any(cuts.values()):
            return
        self._checkpoint()
        for i, n in cuts.items():
            self.lines[i] = self.lines[i][n:]
        for i, n in cuts.items():
            self._shift_cols(range(i, i + 1), -n)

    @staticmethod
    def _indent_cut(text: str) -> int:
        if text.startswith('\t'):
            return 1
        head = text[:TAB_WIDTH]
        return len(head) - len(head.lstrip(' '))

    def _shift_cols(self, block: range, delta: int) -> None:
        # каретка и якорь едут вместе с текстом своей строки; колонка 0
        # остаётся на месте — иначе выделение целых строк после сдвига
        # теряло бы их отступ
        def shifted(pos: Pos) -> Pos:
            line, col = pos
            if line not in block or (delta > 0 and (col == 0 or not self.lines[line])):
                return pos
            return line, max(0, min(col + delta, len(self.lines[line])))
        self.line, self.col = shifted(self.caret)
        if self.anchor is not None:
            self.anchor = shifted(self.anchor)
        self.goal = None

    def replace_lines(self, lo: int, hi: int, new: list[str]) -> None:
        """Заменить строки [lo, hi) — откат блока изменений в буфере."""
        self._checkpoint()
        self.lines[lo:hi] = new
        if not self.lines:
            self.lines = ['']
        self._settle_eol()
        self.set_caret(min(lo, len(self.lines) - 1), 0)

    # --- история ---

    def undo(self) -> bool:
        return self._swap(self._undo, self._redo)

    def redo(self) -> bool:
        return self._swap(self._redo, self._undo)

    def _swap(self, src: list, dst: list) -> bool:
        if not src:
            return False
        dst.append(self._snapshot())
        lines, (self.line, self.col), self.anchor, self.eol = src.pop()
        self.lines = list(lines)
        self._group = None
        self.goal = None
        return True
