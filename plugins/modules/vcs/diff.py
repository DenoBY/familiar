"""Построение строк diff-представления: общий слой vcs-китов.

Source-agnostic слой без обращения к git и без состояния хендлера:
превращает данные (два текста файла, список изменений) в готовые к
печати строки — лёгкая подсветка синтаксиса, word-diff, свёртка
контекста в гэпы, sticky-скоупы и плоское дерево файлов. Используется
и review (рабочее дерево), и log (коммиты).

Представлений диффа два, и оба дают одинаковую DiffModel:
`unified_rows` — классический дифф со знаками +/−, `final_rows` —
финальный файл целиком, как в IDE: изменения видны только маркером
на полях.
"""

import bisect
import difflib
import re
from typing import NamedTuple

from kittens.tui.operations import styled

from ..highlight import (
    ADD_BG,
    ADD_FOCUS_BG,
    ADD_WORD_BG,
    CURSOR_BG,
    DEL_BG,
    DEL_FOCUS_BG,
    DEL_WORD_BG,
    SEL_RANGE_BG,
    fit_fgs,
    render_code,
    strong_set,
    text_colors,
    word_ranges,
)
from ..text import plural
from .util import truncate


# ──────────────────── unified дифф (одна колонка) ────────────────────

_HUNK_RE = re.compile(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@')
_NUMW = 4
# Строки-«определения» (для sticky-заголовка: в какой функции/классе мы
# находимся).
_DEF_RE = re.compile(
    r'^\s*(?:export\s+)?(?:default\s+)?(?:public\s+|private\s+|protected\s+|static\s+'
    r'|async\s+|final\s+|abstract\s+)*'
    r'(?:def|class|func|function|fn|module|interface|type|struct|impl|trait|enum|'
    r'namespace|sub|method)\b')
SEL_BG = CURSOR_BG
# Оттенок фокуса/выделения для строк со своим фоном: на add/del — ярче
# того же цвета (иначе курсор сливается с фоном строки), на контексте —
# серый.
_FOCUS_SHADE = {ADD_BG: ADD_FOCUS_BG, DEL_BG: DEL_FOCUS_BG, None: SEL_BG}


def _bg(text: str, bg: 'int | None') -> str:
    return styled(text, bg=bg) if bg is not None and text else text


def _split_code(body: str, gutter_w: int) -> 'tuple[str, str, str]':
    """(номера, знак(2 симв.), код) из plain-строки диффа — код
    перерисовать с подсветкой, гуттер/знак оставить плоскими.
    """
    return body[:gutter_w], body[gutter_w:gutter_w + 2], body[gutter_w + 2:]


def _geometry(one_col: bool, width: int) -> tuple[int, int]:
    """(ширина гуттера, ширина колонки кода). Два символа между ними —
    знак строки (+/− в unified, маркер на полях в final).
    """
    gutter_w = (_NUMW + 1) if one_col else (2 * _NUMW + 2)
    return gutter_w, max(1, width - gutter_w - 2)


def gutter_width(one_col: bool, width: int) -> int:
    """Ширина гуттера с номерами строк — граница «клик по номеру» vs
    «клик по коду» в просмотрщике.
    """
    return _geometry(one_col, width)[0]


def _render_diff_line(gut_plain: str, sign: str, sign_fg: 'str | None', code: str,
                      ext: str, base_bg: 'int | None', strong: 'set[int] | None',
                      strong_bg: 'int | None', num_fg: 'str | None', width: int,
                      fgs: 'list[int | None] | None' = None) -> str:
    """Строка диффа: номера, знак, фон на всю ширину,
    синтаксис + word-diff подсветка.
    """
    g = (styled(gut_plain, fg=num_fg, bg=base_bg)
         if (num_fg or base_bg is not None) else gut_plain)
    s = (styled(sign, fg=sign_fg, bold=True, bg=base_bg)
         if (sign_fg or base_bg is not None) else sign)
    line = g + s + render_code(code, ext, base_bg, strong, strong_bg, fgs)
    if base_bg is not None:
        used = len(gut_plain) + len(sign) + len(code)
        if used < width:
            line += styled(' ' * (width - used), bg=base_bg)
    return line


class DiffModel(NamedTuple):
    """Модель unified-диффа: параллельные массивы плюс индексы ханков.

    rows — готовые ANSI-строки; plains — полный plain-текст
    (поиск/копирование); hunks — индексы начал блоков изменений
    (прыжки [ / ]); linenos — номер строки нового файла
    (open-in-editor); scopes — объемлющая функция/класс (sticky);
    gaps — id гэпа (None, кроме строк-разделителей — раскрываются по
    Enter); kinds — фон строки (ADD_BG/DEL_BG/None); vis — видимый plain
    с применённым hscroll (фоновая подсветка курсора/выделения); fgs —
    цвета символов кода каждой строки (полнофайловый лексинг), чтобы
    выделение/курсор сохраняли подсветку, а не пере-лексили построчно.
    """
    rows: 'list[str]'
    plains: 'list[str]'
    hunks: 'list[int]'
    linenos: 'list[int]'
    scopes: 'list[str]'
    gaps: 'list[int | None]'
    kinds: 'list[int | None]'
    vis: 'list[str]'
    fgs: 'list[list | None]'


class DiffSource:
    """Разобранная пара (before, after): не зависит от ширины/скролла.

    SequenceMatcher по файлам, word-diff по парам строк и поиск
    определений — самая дорогая часть построения диффа; считаются один
    раз на файл, а unified_rows переиспользует их при каждом
    hscroll/раскрытии гэпа.
    """

    def __init__(self, before: str, after: str) -> None:
        self.before = before
        self.after = after
        self.a = before.splitlines()
        self.b = after.splitlines()
        self.one_col = (not before) or (not after)
        self.ops = difflib.SequenceMatcher(
            None, self.a, self.b, autojunk=False).get_opcodes()
        self.longest = max(
            (len(s.replace('\t', '    ')) for s in self.a + self.b), default=0)
        # final-вид показывает только новый файл — hscroll там не должен
        # уезжать вслед за длинной удалённой строкой
        self.longest_b = max((len(s.replace('\t', '    ')) for s in self.b), default=0)
        # строки-определения нового файла — для sticky-заголовка скоупа
        self.def_lns = [i + 1 for i, s in enumerate(self.b) if _DEF_RE.match(s)]
        self.def_txt = [self.b[n - 1].strip() for n in self.def_lns]
        self._ranges_by_op: 'dict[int, list]' = {}
        self._colors: 'dict[tuple, list | None]' = {}

    def colors(self, ext: str, new: bool) -> 'list[list] | None':
        """Цвета символов каждой строки файла (нового или старого),
        либо None — Pygments не знает язык.

        Лексим по табам, уже развёрнутым в пробелы: иначе индексы
        цветов разъехались бы с тем, что печатается на экране. Файл
        лексится один раз — hscroll и раскрытие гэпов переиспользуют.
        """
        key = (ext, new)
        if key not in self._colors:
            src = self.after if new else self.before
            self._colors[key] = text_colors(src.replace('\t', '    '), ext)
        return self._colors[key]

    def op_word_ranges(self, oi: int, rem: 'list[str]', add: 'list[str]',
                       pairs: int) -> list:
        cached = self._ranges_by_op.get(oi)
        if cached is None:
            cached = [word_ranges(rem[k], add[k]) for k in range(pairs)]
            self._ranges_by_op[oi] = cached
        return cached


def unified_rows(src: DiffSource, ext: str, width: int, context: int = 3,
                 hscroll: int = 0, expanded: 'set | None' = None,
                 expand_all: bool = False) -> DiffModel:
    """Модель диффа для готового DiffSource.

    context — строк контекста вокруг изменений. expanded — множество id
    раскрытых гэпов. expand_all — показать весь файл без сворачивания.
    hscroll — горизонтальный сдвиг. Соседние удалённая/добавленная
    строки спариваются — word-diff подсвечивает изменившиеся слова.
    """
    expanded = expanded or set()
    a, b = src.a, src.b
    one_col = src.one_col
    _, codew = _geometry(one_col, width)
    cols_a, cols_b = src.colors(ext, new=False), src.colors(ext, new=True)
    rows, plains, vis, hunks, linenos, gaps, kinds, fgs = (
        [], [], [], [], [], [], [], [])

    def line_fgs(cols, idx, cf):
        row = cols[idx] if (cols is not None and idx < len(cols)) else None
        return fit_fgs(row, hscroll, len(cf))

    def gutter(sign, old_ln, new_ln):
        if one_col:
            num = old_ln if sign == '-' else new_ln
            return f'{num:>{_NUMW}} '
        o = f'{old_ln:>{_NUMW}}' if sign in ('-', ' ') else ' ' * _NUMW
        n = f'{new_ln:>{_NUMW}}' if sign in ('+', ' ') else ' ' * _NUMW
        return f'{o} {n} '

    def clip(full):
        return truncate(full[hscroll:] if hscroll else full, codew)

    def clip_strong(strong):
        if not strong or not hscroll:
            return strong
        return {i - hscroll for i in strong if i >= hscroll}

    def emit(row, plain, lineno=0, gap=None, bg=None, visible=None, fg=None):
        rows.append(row)
        plains.append(plain)
        vis.append(plain if visible is None else visible)
        linenos.append(lineno)
        gaps.append(gap)
        kinds.append(bg)
        fgs.append(fg)

    def emit_ctx(ia, ib):
        full = a[ia].replace('\t', '    ')
        gut = gutter(' ', ia + 1, ib + 1)
        cf = clip(full)
        fg = line_fgs(cols_b, ib, cf)
        emit(_render_diff_line(gut, '  ', None, cf, ext, None, None, None,
                               'gray', width, fg),
             gut + '  ' + full, ib + 1, visible=gut + '  ' + cf, fg=fg)

    def emit_change(oi, i1, i2, j1, j2):
        hunks.append(len(rows))
        rem = [a[i].replace('\t', '    ') for i in range(i1, i2)]
        add = [b[j].replace('\t', '    ') for j in range(j1, j2)]
        pairs = min(len(rem), len(add))
        rng = src.op_word_ranges(oi, rem, add, pairs)
        for k, full in enumerate(rem):
            strong = (clip_strong(strong_set(rng[k][0], rng[k][2], full))
                      if k < pairs else None)
            gut = gutter('-', i1 + k + 1, j1 + 1)
            cf = clip(full)
            fg = line_fgs(cols_a, i1 + k, cf)
            emit(_render_diff_line(gut, '- ', 'red', cf, ext, DEL_BG, strong,
                                   DEL_WORD_BG, 'red', width, fg),
                 gut + '- ' + full, j1 + 1, bg=DEL_BG, visible=gut + '- ' + cf, fg=fg)
        for k, full in enumerate(add):
            strong = (clip_strong(strong_set(rng[k][1], rng[k][2], full))
                      if k < pairs else None)
            gut = gutter('+', i1 + 1, j1 + k + 1)
            cf = clip(full)
            fg = line_fgs(cols_b, j1 + k, cf)
            emit(_render_diff_line(gut, '+ ', 'green', cf, ext, ADD_BG, strong,
                                   ADD_WORD_BG, 'green', width, fg),
                 gut + '+ ' + full, j1 + k + 1, bg=ADD_BG, visible=gut + '+ ' + cf, fg=fg)

    def emit_gap(hidden, lineno, gid):
        inner = f' {plural(hidden, "line")} hidden — Enter to expand '  # линии вплотную к тексту
        dots = max(0, width - len(inner))
        left = dots // 2
        sep = '┈' * left + inner + '┈' * (dots - left)                  # метка по центру
        tsep = truncate(sep, width)
        emit('', '', 0, gid)                                           # padding сверху
        emit(styled(tsep, fg='cyan', bold=True), sep, lineno, gid, visible=tsep)
        emit('', '', 0, gid)                                           # padding снизу

    ops = src.ops
    n_ops = len(ops)
    gid = 0
    for oi, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == 'equal':
            length = i2 - i1
            lead = 0 if oi == 0 else context
            trail = 0 if oi == n_ops - 1 else context
            if expand_all or gid in expanded or length <= lead + trail:
                for off in range(length):
                    emit_ctx(i1 + off, j1 + off)
                if not (expand_all or length <= lead + trail):
                    gid += 1   # гэп раскрыт, но id занят (стабильность между перерисовками)
            else:
                for off in range(lead):
                    emit_ctx(i1 + off, j1 + off)
                emit_gap(length - lead - trail, j1 + lead + 1, gid)
                for off in range(length - trail, length):
                    emit_ctx(i1 + off, j1 + off)
                gid += 1
        else:
            emit_change(oi, i1, i2, j1, j2)

    return DiffModel(rows, plains, hunks, linenos, _scopes(src, linenos),
                     gaps, kinds, vis, fgs)


def _scopes(src: DiffSource, linenos: 'list[int]') -> 'list[str]':
    """Для каждой строки — ближайшее определение (def/class/…) выше по
    новому файлу; sticky-заголовок панели.
    """
    def scope_for(ln):
        j = bisect.bisect_right(src.def_lns, ln) - 1
        return src.def_txt[j] if j >= 0 else ''

    return [scope_for(ln) for ln in linenos]


# ─────────── final-вид (финальный файл, как в IDE) ───────────

_MARK_CHANGE = '▎'       # добавлена/изменена: полоса вдоль строки
_MARK_DELETE = '▔'       # перед этой строкой вырезан код (черта у верха)
_MARK_DELETE_END = '▁'   # после этой (последней) строки вырезан код (у низа)
MARK_FG = {'add': 'green', 'mod': 'blue', 'del': 'red', 'del_end': 'red'}
# Что важнее показать, когда в одну ячейку карты изменений попали
# разные правки: удаление заметить труднее всего (своей строки у него
# нет), добавление — легче всего.
_MARK_PRIORITY = {'del': 3, 'del_end': 3, 'mod': 2, 'add': 1}
_MARK_CHAR = {'del': _MARK_DELETE, 'del_end': _MARK_DELETE_END}


def line_marks(src: DiffSource) -> 'tuple[list[str | None], list[int]]':
    """Разметка строк нового файла: (marks, hunks).

    marks[j] — метка строки b[j]: 'add', 'mod' или 'del' («перед этой
    строкой вырезан код»). hunks — индексы строк, с которых
    начинаются изменения.
    """
    b = src.b
    marks: 'list[str | None]' = [None] * len(b)
    hunks: 'set[int]' = set()
    for tag, i1, i2, j1, j2 in src.ops:
        if tag == 'equal':
            continue
        if tag == 'delete':
            if not b:
                continue
            # индекс строки b == индекс её ряда, поэтому «удалено перед
            # b[j1]» ложится прямо в j1; удаление в конце файла своей
            # строки-«после» не имеет — метим низ последней строки
            row, mark = _del_row(j1, len(b))
            if marks[row] is None:   # add/mod важнее: своя строка изменилась
                marks[row] = mark
            hunks.add(row)
            continue
        hunks.add(j1)
        if tag == 'insert':
            for j in range(j1, j2):
                marks[j] = 'add'
            continue
        pairs = min(i2 - i1, j2 - j1)
        for k in range(j2 - j1):
            # строк стало больше — хвост блока просто добавлен
            marks[j1 + k] = 'mod' if k < pairs else 'add'
        if i2 - i1 > j2 - j1 and b:
            # строк стало меньше: вырезанному хвосту достаётся строка за
            # блоком — своей у него, как и у обычного delete, нет
            row, mark = _del_row(j2, len(b))
            if marks[row] is None:
                marks[row] = mark
            hunks.add(row)
    return marks, sorted(hunks)


def _del_row(pos: int, n: int) -> 'tuple[int, str]':
    """Строка нового файла для метки удаления и её вид: обычно «перед
    b[pos]» (черта у верха), но удаление за последней строкой метит её
    низ — кода «после» в файле уже нет.
    """
    return (pos, 'del') if pos < n else (n - 1, 'del_end')


def kinds_to_marks(kinds: 'list[int | None]') -> 'list[str | None]':
    """Фон строк unified-диффа → те же метки, что у final-вида: карта
    изменений на полосе прокрутки одна на оба вида.
    """
    return [{ADD_BG: 'add', DEL_BG: 'del'}.get(k) for k in kinds]


def change_map(marks: 'list[str | None]', height: int) -> 'list[str | None]':
    """Метки изменений, сжатые до height ячеек полосы прокрутки:
    сразу видно, куда листать. Строки диффа делятся между ячейками
    поровну; в ячейку попадает самая заметная из меток её строк.

    Дифф, который влезает в окно целиком, не растягиваем: риска
    должна стоять ровно напротив своей строки.
    """
    n = len(marks)
    if not n or height <= 0:
        return []
    out: 'list[str | None]' = [None] * height
    best = [0] * height
    for i, mark in enumerate(marks):
        if mark is None:
            continue
        r = i if n <= height else min(i * height // n, height - 1)
        if _MARK_PRIORITY[mark] > best[r]:
            best[r] = _MARK_PRIORITY[mark]
            out[r] = mark
    return out


def final_rows(src: DiffSource, ext: str, width: int, hscroll: int = 0) -> DiffModel:
    """Модель финального файла: все строки нового текста, без знаков
    +/− и без удалённых строк.

    Правки видны так же, как в IDE: только цветной маркер на полях
    (зелёный — добавлено, синий — изменено, красный — здесь что-то
    вырезано). Заливки внутри строки нет — код читается как код;
    что именно изменилось в строке, показывает unified-вид.
    """
    _, codew = _geometry(True, width)
    marks, hunks = line_marks(src)
    cols = src.colors(ext, new=True)
    rows, plains, vis, linenos, fgs = [], [], [], [], []
    for j, raw in enumerate(src.b):
        full = raw.replace('\t', '    ')
        mark = marks[j]
        char = _MARK_CHAR.get(mark, _MARK_CHANGE) if mark is not None else ' '
        sign = char + ' '
        gut = f'{j + 1:>{_NUMW}} '
        cf = truncate(full[hscroll:] if hscroll else full, codew)
        fg = fit_fgs(cols[j] if (cols is not None and j < len(cols)) else None,
                     hscroll, len(cf))
        rows.append(_render_diff_line(gut, sign, MARK_FG.get(mark), cf, ext, None,
                                      None, None, 'gray', width, fg))
        # маркер входит в plain: под курсором печатается именно plain,
        # иначе строка дёргалась бы влево на ширину маркера
        plains.append(gut + sign + full)
        vis.append(gut + sign + cf)
        linenos.append(j + 1)
        fgs.append(fg)
    n = len(rows)
    return DiffModel(rows, plains, hunks, linenos, _scopes(src, linenos),
                     [None] * n, [None] * n, vis, fgs)


def max_hscroll(src: DiffSource, width: int, final: bool = False) -> int:
    """Предел горизонтального скролла: дальше вправо некуда — самая
    длинная строка уже целиком помещается в видимую ширину кода.
    """
    _, codew = _geometry(True if final else src.one_col, width)
    longest = src.longest_b if final else src.longest
    return max(0, longest - codew)


# ──────── отрисовка одной строки диффа под курсором/выделением ────────

def is_code_row(di: int, linenos: list, gaps: list) -> bool:
    """Настоящая строка кода диффа (не padding/разделитель гэпа)."""
    line = linenos[di] if di < len(linenos) else 0
    return line > 0 and (di >= len(gaps) or gaps[di] is None)


def render_match(plain: str, rw: int, query: str) -> str:
    """Строка с подсвеченными вхождениями запроса — только для
    текущей (в фокусе) строки-совпадения поиска.
    """
    text = truncate(plain, rw)
    q = query.lower()
    if not q:
        return text
    low, out, i = text.lower(), '', 0
    while True:
        j = low.find(q, i)
        if j < 0:
            out += text[i:]
            break
        out += text[i:j] + styled(text[j:j + len(q)], reverse=True, bold=True)
        i = j + len(q)
    return out


def render_diff_cell(di: int, rw: int, focus_diff: bool, diff_cur: int,
                     diff_sel: 'tuple[int, int] | None', annotated: bool, *,
                     rows: list[str], plains: list[str], linenos: list[int],
                     kind_bg: 'list[int | None]', gaps: 'list[int | None]',
                     cur_match: int, query: str,
                     char_sel: 'tuple[int, int, int] | None' = None,
                     vis: 'list[str] | None' = None, hscroll: int = 0,
                     ext: str = '', gutter_w: int = 0,
                     fgs: 'list[list | None] | None' = None) -> str:
    """Правая ячейка строки диффа: курсор, выделение, маркер аннотации,
    совпадение поиска или обычная строка. Чистая — все данные и
    состояние приходят параметрами (annotated считает вызывающий: у
    review — по аннотациям, у log — всегда False).

    char_sel=(row, cs, ce) — выделение куска внутри одной строки
    (перекрывает всё остальное для этой строки): подсвечивается фоном
    диапазон символов [cs, ce). vis — видимый plain-текст с учётом
    hscroll (fallback на plains, если не передан): фон рисуется по нему,
    чтобы подсветка ехала вместе с горизонтальным скроллом.
    """
    if di >= len(rows):
        return ''
    visible = vis if vis is not None else plains
    if char_sel is not None and di == char_sel[0]:
        body = truncate(visible[di], rw)
        cs = max(0, min(char_sel[1] - hscroll, len(body)))
        ce = max(cs, min(char_sel[2] - hscroll, len(body)))
        base = kind_bg[di] if di < len(kind_bg) else None
        pad = ' ' * (rw - len(body)) if len(body) < rw else ''
        # код рисуем реальными цветами файла (fgs), выделение — фоном на
        # диапазоне (SEL_RANGE_BG как strong_bg), знак/номер — плоско
        head, sign, code = _split_code(body, gutter_w)
        row_fgs = fgs[di] if (fgs and di < len(fgs)) else None
        off = gutter_w + 2
        strong = set(range(max(0, cs - off), max(0, ce - off))) if ce > cs else None
        out = (_bg(head, base) + _bg(sign, base)
               + render_code(code, ext, base, strong, SEL_RANGE_BG, row_fgs)
               + _bg(pad, base))
        return out
    code_row = is_code_row(di, linenos, gaps)
    # выделение подсвечивает только реальные строки кода — не
    # padding/разделители гэпов
    in_sel = (focus_diff and diff_sel is not None
              and diff_sel[0] <= di <= diff_sel[1] and code_row)
    is_cur = focus_diff and di == diff_cur
    # во время выделения не подсвечиваем курсор на padding/разделителе
    # гэпа — иначе конец drag серым «прыгает» по пустым строкам гэпа
    if is_cur and diff_sel is not None and not code_row:
        is_cur = False
    if in_sel or is_cur:
        # выделение строк / строка под курсором: сохраняем реальную
        # подсветку файла (fgs), поверх — соответствующий фон
        body = truncate(visible[di], rw)
        if in_sel:
            sel_bg = SEL_RANGE_BG
        else:
            bg = kind_bg[di] if di < len(kind_bg) else None
            sel_bg = _FOCUS_SHADE.get(bg, SEL_BG)
        head, sign, code = _split_code(body, gutter_w)
        row_fgs = fgs[di] if (fgs and di < len(fgs)) else None
        if is_cur:
            marker = '●' if annotated else '▎'
            mfg = 'yellow' if annotated else 'cyan'
            out = styled(marker, fg=mfg, bg=sel_bg, bold=True) + _bg(head[1:], sel_bg)
        else:
            out = _bg(head, sel_bg)
        out += _bg(sign, sel_bg) + render_code(code, ext, sel_bg, None, None, row_fgs)
        if len(body) < rw:
            out += styled(' ' * (rw - len(body)), bg=sel_bg)
        return out
    if di == cur_match and di < len(visible):
        return render_match(visible[di], rw, query)
    if annotated:
        body = truncate(visible[di], rw)
        tail = styled(body[1:], fg='gray') if len(body) > 1 else ''
        return styled('●', fg='yellow', bold=True) + tail
    return rows[di]


# ─────────────────────── дерево файлов ───────────────────────

def _new_node() -> dict:
    return {'dirs': {}, 'files': [], 'count': 0}


def build_tree(items: list[dict], collapsed: set) -> list[dict]:
    """items (с полем 'rel') → плоский список строк дерева
    (dir/file) со сворачиванием; элементы с полем 'group' — под
    одноимённый узел в конце дерева.

    У строки-папки 'key' (ключ сворачивания) и 'path' (путь на
    диске) расходятся: у узла группы пути нет, а ключи её подпапок
    неймспейснуты именем группы — иначе свёрнутая папка внутри
    группы схлопнула бы одноимённую снаружи.
    """
    plain = _new_node()
    groups: dict[str, dict] = {}
    for idx, it in enumerate(items):
        group = it.get('group')
        node = plain if group is None else groups.setdefault(group, _new_node())
        parts = it['rel'].split('/')
        node['count'] += 1
        for p in parts[:-1]:
            node = node['dirs'].setdefault(p, _new_node())
            node['count'] += 1
        node['files'].append((parts[-1], idx))
    rows = []

    def walk(node, depth, keypath, pathpath, group):
        for name in sorted(node['dirs']):
            key = f'{keypath}/{name}' if keypath else name
            path = f'{pathpath}/{name}' if pathpath else name
            child = node['dirs'][name]
            is_collapsed = key in collapsed
            rows.append({'type': 'dir', 'depth': depth, 'name': name,
                         'key': key, 'path': path, 'count': child['count'],
                         'collapsed': is_collapsed, 'group': group})
            if not is_collapsed:
                walk(child, depth + 1, key, path, group)
        for fname, idx in sorted(node['files']):
            rows.append({'type': 'file', 'depth': depth, 'name': fname,
                         'idx': idx, 'kind': items[idx]['kind'],
                         'stat': items[idx].get('stat')})

    walk(plain, 0, '', '', None)
    for name in sorted(groups):
        key = group_key(name)
        is_collapsed = key in collapsed
        rows.append({'type': 'dir', 'depth': 0, 'name': name, 'key': key,
                     'path': None, 'count': groups[name]['count'],
                     'collapsed': is_collapsed, 'group': name, 'group_root': True})
        if not is_collapsed:
            walk(groups[name], 1, key, '', name)
    return rows


def group_key(name: str) -> str:
    """Ключ сворачивания узла-группы. Слэш в начале не может
    встретиться у ключа реальной папки (rel-пути относительные).
    """
    return f'/{name}'
