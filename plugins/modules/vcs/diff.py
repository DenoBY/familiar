"""Построение строк представления review-кита: unified-дифф и дерево файлов.

Здесь всё, что превращает данные (два текста файла, список изменений) в готовые
к печати строки: лёгкая подсветка синтаксиса, word-diff, свёртка контекста в гэпы,
sticky-скоупы и плоское дерево файлов. Без обращения к git и без состояния хендлера.
"""

import bisect
import difflib
import re
from typing import NamedTuple

from kittens.tui.operations import styled

from .util import truncate


# ──────────────────── лёгкая подсветка синтаксиса ────────────────────

_KEYWORDS = frozenset("""
and or not in is if elif else for while return def class import from as try except
finally with lambda yield global nonlocal pass break continue raise assert del match case
function var let const new delete typeof instanceof void this super extends implements
interface enum public private protected static readonly export default async await
func package type struct chan go defer select range fallthrough map
echo fn use namespace trait abstract final foreach endforeach endif endwhile switch do
int float double bool boolean string char long short unsigned signed union typedef sizeof
True False None null true false nil undefined self throw catch then
""".split())

_STR = r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`'
_NUM = r'\b\d[\d_]*\.?\d*\b'
_WORD = r'[A-Za-z_]\w*'
_COMMENT_BY_EXT = {
    '.js': '//', '.ts': '//', '.jsx': '//', '.tsx': '//', '.c': '//', '.h': '//',
    '.cpp': '//', '.cc': '//', '.go': '//', '.rs': '//', '.java': '//', '.php': '//',
    '.swift': '//', '.kt': '//', '.scala': '//', '.cs': '//',
    '.sql': '--', '.lua': '--', '.hs': '--',
}
_TOK_COLOR = {'comment': 'gray', 'string': 'yellow', 'number': 'cyan'}
_LEX_CACHE = {}


def _lexer(ext):
    if ext not in _LEX_CACHE:
        cprefix = _COMMENT_BY_EXT.get(ext, '#')
        cpat = re.escape(cprefix) + r'.*'
        _LEX_CACHE[ext] = re.compile(
            r'(?P<comment>%s)|(?P<string>%s)|(?P<number>%s)|(?P<word>%s)|(?P<other>.)'
            % (cpat, _STR, _NUM, _WORD), re.DOTALL)
    return _LEX_CACHE[ext]


def _fg_map(code, ext):
    """Список fg-цветов по символам строки (None — без подсветки)."""
    fgs = [None] * len(code)
    for m in _lexer(ext).finditer(code):
        kind, tok = m.lastgroup, m.group()
        if kind == 'word':
            fg = 'magenta' if tok in _KEYWORDS else None
        else:
            fg = _TOK_COLOR.get(kind)
        if fg:
            for i in range(m.start(), m.end()):
                fgs[i] = fg
    return fgs


def render_code(code: str, ext: str, base_bg: 'int | None' = None,
                strong: 'set | None' = None, strong_bg: 'int | None' = None) -> str:
    """Код → ANSI: fg по синтаксису; фон = strong_bg на символах из strong, иначе base_bg."""
    fgs = _fg_map(code, ext)
    out, i, n = '', 0, len(code)
    while i < n:
        fg = fgs[i]
        bg = strong_bg if (strong and i in strong) else base_bg
        j = i + 1
        while (j < n and fgs[j] == fg
               and (strong_bg if (strong and j in strong) else base_bg) == bg):
            j += 1
        seg = code[i:j]
        out += styled(seg, fg=fg, bg=bg) if (fg or bg is not None) else seg
        i = j
    return out


_WORD_SPLIT = re.compile(r'\w+|\s+|[^\w\s]')


def _word_ranges(old, new):
    """(изменившиеся символы old, изменившиеся символы new, похожесть 0..1) — word-level."""
    a, b = _WORD_SPLIT.findall(old), _WORD_SPLIT.findall(new)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ap, p = [], 0
    for t in a:
        ap.append(p)
        p += len(t)
    bp, p = [], 0
    for t in b:
        bp.append(p)
        p += len(t)
    dset, aset = set(), set()
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ('replace', 'delete'):
            for k in range(i1, i2):
                dset.update(range(ap[k], ap[k] + len(a[k])))
        if tag in ('replace', 'insert'):
            for k in range(j1, j2):
                aset.update(range(bp[k], bp[k] + len(b[k])))
    return dset, aset, sm.ratio()


# ──────────────────────── unified дифф (одна колонка) ────────────────────────

_HUNK_RE = re.compile(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@')
_NUMW = 4
# Строки-«определения» (для sticky-заголовка: в какой функции/классе мы находимся).
_DEF_RE = re.compile(
    r'^\s*(?:export\s+)?(?:default\s+)?(?:public\s+|private\s+|protected\s+|static\s+'
    r'|async\s+|final\s+|abstract\s+)*'
    r'(?:def|class|func|function|fn|module|interface|type|struct|impl|trait|enum|'
    r'namespace|trait|sub|method)\b')
ADD_BG = 22        # тёмно-зелёный фон добавленных строк (256-цвет)
DEL_BG = 52        # тёмно-красный фон удалённых строк
ADD_WORD_BG = 28   # ярче — на изменившихся словах (word-diff)
DEL_WORD_BG = 88
SEL_BG = 238       # фон строки-курсора для контекста (там своего фона нет)
SEL_RANGE_BG = 25  # фон диапазона выделения строк — синий, чтобы читался и на add/del
# Оттенок фокуса/выделения для строк со своим фоном: на add/del — ярче того же цвета
# (иначе курсор сливается с зелёным/красным), на контексте — серый.
_FOCUS_SHADE = {ADD_BG: 34, DEL_BG: 124, None: SEL_BG}


def _bg(text: str, bg: 'int | None') -> str:
    return styled(text, bg=bg) if bg is not None and text else text


def _render_diff_line(gut_plain, sign, sign_fg, code, ext, base_bg, strong, strong_bg,
                      num_fg, width):
    """Строка диффа: номера, знак, фон на всю ширину, синтаксис + word-diff подсветка."""
    g = (styled(gut_plain, fg=num_fg, bg=base_bg)
         if (num_fg or base_bg is not None) else gut_plain)
    s = (styled(sign, fg=sign_fg, bold=True, bg=base_bg)
         if (sign_fg or base_bg is not None) else sign)
    line = g + s + render_code(code, ext, base_bg, strong, strong_bg)
    if base_bg is not None:
        used = len(gut_plain) + len(sign) + len(code)
        if used < width:
            line += styled(' ' * (width - used), bg=base_bg)
    return line


class DiffModel(NamedTuple):
    """Модель unified-диффа: параллельные массивы по строкам плюс индексы ханков.

    rows — готовые ANSI-строки; plains — полный plain-текст (поиск/копирование);
    hunks — индексы начал блоков изменений (прыжки [ / ]); linenos — номер строки
    нового файла (open-in-editor); scopes — объемлющая функция/класс (sticky);
    gaps — id гэпа (None, кроме строк-разделителей — раскрываются по Enter);
    kinds — фон строки (ADD_BG/DEL_BG/None); vis — видимый plain с применённым
    hscroll (фоновая подсветка курсора/выделения).
    """
    rows: 'list[str]'
    plains: 'list[str]'
    hunks: 'list[int]'
    linenos: 'list[int]'
    scopes: 'list[str]'
    gaps: 'list[int | None]'
    kinds: 'list[int | None]'
    vis: 'list[str]'


class DiffSource:
    """Разобранная пара (before, after): всё, что не зависит от ширины и скролла.

    SequenceMatcher по файлам, word-diff по парам строк и поиск определений —
    самая дорогая часть построения диффа; считаются один раз на файл, а
    unified_rows переиспользует их при каждом hscroll/раскрытии гэпа.
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
        # строки-определения нового файла — для sticky-заголовка скоупа
        self.def_lns = [i + 1 for i, s in enumerate(self.b) if _DEF_RE.match(s)]
        self.def_txt = [self.b[n - 1].strip() for n in self.def_lns]
        self._ranges_by_op: 'dict[int, list]' = {}

    def word_ranges(self, oi: int, rem: 'list[str]', add: 'list[str]',
                    pairs: int) -> list:
        cached = self._ranges_by_op.get(oi)
        if cached is None:
            cached = [_word_ranges(rem[k], add[k]) for k in range(pairs)]
            self._ranges_by_op[oi] = cached
        return cached


def unified_rows(src: DiffSource, ext: str, width: int, context: int = 3,
                 hscroll: int = 0, expanded: 'set | None' = None,
                 expand_all: bool = False) -> DiffModel:
    """Модель диффа для готового DiffSource.

    context — строк контекста вокруг изменений. expanded — множество id раскрытых гэпов.
    expand_all — показать весь файл без сворачивания. hscroll — горизонтальный сдвиг.
    Соседние удалённая/добавленная строки спариваются — word-diff подсвечивает изменившиеся слова.
    """
    expanded = expanded or set()
    a, b = src.a, src.b
    one_col = src.one_col
    gutter_w = (_NUMW + 1) if one_col else (2 * _NUMW + 2)
    codew = max(1, width - gutter_w - 2)   # gutter + знак
    rows, plains, vis, hunks, linenos, gaps, kinds = [], [], [], [], [], [], []

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

    def emit(row, plain, lineno=0, gap=None, bg=None, visible=None):
        rows.append(row)
        plains.append(plain)
        vis.append(plain if visible is None else visible)
        linenos.append(lineno)
        gaps.append(gap)
        kinds.append(bg)

    def emit_ctx(ia, ib):
        full = a[ia].replace('\t', '    ')
        gut = gutter(' ', ia + 1, ib + 1)
        cf = clip(full)
        emit(_render_diff_line(gut, '  ', None, cf, ext, None, None, None,
                               'gray', width), gut + '  ' + full, ib + 1,
             visible=gut + '  ' + cf)

    def emit_change(oi, i1, i2, j1, j2):
        hunks.append(len(rows))
        rem = [a[i].replace('\t', '    ') for i in range(i1, i2)]
        add = [b[j].replace('\t', '    ') for j in range(j1, j2)]
        pairs = min(len(rem), len(add))
        rng = src.word_ranges(oi, rem, add, pairs)
        for k, full in enumerate(rem):
            strong = clip_strong(rng[k][0]) if (k < pairs and rng[k][2] >= 0.3) else None
            gut = gutter('-', i1 + k + 1, j1 + 1)
            cf = clip(full)
            emit(_render_diff_line(gut, '- ', 'red', cf, ext, DEL_BG, strong,
                                   DEL_WORD_BG, 'red', width), gut + '- ' + full, j1 + 1,
                 bg=DEL_BG, visible=gut + '- ' + cf)
        for k, full in enumerate(add):
            strong = clip_strong(rng[k][1]) if (k < pairs and rng[k][2] >= 0.3) else None
            gut = gutter('+', i1 + 1, j1 + k + 1)
            cf = clip(full)
            emit(_render_diff_line(gut, '+ ', 'green', cf, ext, ADD_BG, strong,
                                   ADD_WORD_BG, 'green', width), gut + '+ ' + full, j1 + k + 1,
                 bg=ADD_BG, visible=gut + '+ ' + cf)

    def emit_gap(hidden, lineno, gid):
        noun = 'line' if hidden == 1 else 'lines'
        inner = f' {hidden} {noun} hidden — Enter to expand '           # линии вплотную к тексту
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

    # скоупы: для каждой строки — ближайшее определение (def/class/…) выше по новому файлу
    def scope_for(ln):
        j = bisect.bisect_right(src.def_lns, ln) - 1
        return src.def_txt[j] if j >= 0 else ''

    scopes = [scope_for(ln) for ln in linenos]
    return DiffModel(rows, plains, hunks, linenos, scopes, gaps, kinds, vis)


def max_hscroll(src: DiffSource, width: int) -> int:
    """Предел горизонтального скролла: дальше вправо некуда — самая длинная строка
    уже целиком помещается в видимую ширину кода. gutter/codew считаются как в unified_rows."""
    gutter_w = (_NUMW + 1) if src.one_col else (2 * _NUMW + 2)
    codew = max(1, width - gutter_w - 2)
    return max(0, src.longest - codew)


# ──────────────── отрисовка одной строки диффа под курсором/выделением ────────────────

def is_code_row(di: int, linenos: list, gaps: list) -> bool:
    """Настоящая строка кода диффа (не padding/разделитель гэпа)."""
    line = linenos[di] if di < len(linenos) else 0
    return line > 0 and (di >= len(gaps) or gaps[di] is None)


def render_match(plain: str, rw: int, query: str) -> str:
    """Строка с подсвеченными вхождениями запроса — только для текущей (в фокусе)
    строки-совпадения поиска."""
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
                     diff_sel: 'tuple | None', annotated: bool, *,
                     rows: list, plains: list, linenos: list, kind_bg: list,
                     gaps: list, cur_match: int, query: str,
                     char_sel: 'tuple | None' = None, vis: 'list | None' = None,
                     hscroll: int = 0) -> str:
    """Правая ячейка строки диффа: курсор, выделение, маркер аннотации, совпадение
    поиска или обычная строка. Чистая — все данные и состояние приходят параметрами
    (annotated считает вызывающий: у review — по аннотациям, у log — всегда False).

    char_sel=(row, cs, ce) — выделение куска внутри одной строки (перекрывает всё
    остальное для этой строки): подсвечивается фоном диапазон символов [cs, ce).
    vis — видимый plain-текст с учётом hscroll (fallback на plains, если не передан):
    фон рисуется по нему, чтобы подсветка ехала вместе с горизонтальным скроллом.
    """
    if di >= len(rows):
        return ''
    visible = vis if vis is not None else plains
    if char_sel is not None and di == char_sel[0]:
        body = truncate(visible[di], rw)
        cs = max(0, min(char_sel[1] - hscroll, len(body)))
        ce = max(cs, min(char_sel[2] - hscroll, len(body)))
        base = kind_bg[di] if di < len(kind_bg) else None
        span_bg = SEL_RANGE_BG   # тот же синий, что у многострочного выделения
        pad = ' ' * (rw - len(body)) if len(body) < rw else ''
        out = (_bg(body[:cs], base) + _bg(body[cs:ce], span_bg)
               + _bg(body[ce:], base) + _bg(pad, base))
        return out
    code_row = is_code_row(di, linenos, gaps)
    # выделение подсвечивает только реальные строки кода — не padding/разделители гэпов
    in_sel = (focus_diff and diff_sel is not None
              and diff_sel[0] <= di <= diff_sel[1] and code_row)
    is_cur = focus_diff and di == diff_cur
    # во время выделения не подсвечиваем курсор на padding/разделителе гэпа —
    # иначе конец drag серым «прыгает» по пустым строкам гэпа
    if is_cur and diff_sel is not None and not code_row:
        is_cur = False
    if is_cur or in_sel:
        body = truncate(visible[di], rw)
        if in_sel:
            sel_bg = SEL_RANGE_BG   # выделение важнее фона курсора
        else:
            bg = kind_bg[di] if di < len(kind_bg) else None
            sel_bg = _FOCUS_SHADE.get(bg, SEL_BG)   # другой оттенок того же цвета
        if is_cur:
            marker = '●' if annotated else '▎'
            mfg = 'yellow' if annotated else 'cyan'
            out = styled(marker, fg=mfg, bg=sel_bg, bold=True)
            out += styled(body[1:], bg=sel_bg) if len(body) > 1 else ''
        else:
            out = styled(body, bg=sel_bg) if body else ''
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


# ──────────────────────────── дерево файлов ────────────────────────────

def build_tree(items: list[dict], collapsed: set) -> list[dict]:
    """items (с полем 'rel') → плоский список строк дерева (dir/file) со сворачиванием."""
    root = {'dirs': {}, 'files': [], 'count': 0}
    for idx, it in enumerate(items):
        parts = it['rel'].split('/')
        node = root
        node['count'] += 1
        for p in parts[:-1]:
            node = node['dirs'].setdefault(p, {'dirs': {}, 'files': [], 'count': 0})
            node['count'] += 1
        node['files'].append((parts[-1], idx))
    rows = []

    def walk(node, depth, keypath):
        for name in sorted(node['dirs']):
            key = f'{keypath}/{name}' if keypath else name
            child = node['dirs'][name]
            is_collapsed = key in collapsed
            rows.append({'type': 'dir', 'depth': depth, 'name': name,
                         'key': key, 'count': child['count'], 'collapsed': is_collapsed})
            if not is_collapsed:
                walk(child, depth + 1, key)
        for fname, idx in sorted(node['files']):
            rows.append({'type': 'file', 'depth': depth, 'name': fname,
                         'idx': idx, 'kind': items[idx]['kind'],
                         'stat': items[idx].get('stat')})

    walk(root, 0, '')
    return rows
