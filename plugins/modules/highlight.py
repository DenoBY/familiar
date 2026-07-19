"""Подсветка синтаксиса и word-diff: общий модуль всех китов.

Подсветку даёт Pygments (лежит рядом, в plugins/vendor — kitty носит
свой Python, системных пакетов там нет): настоящий лексер различает
функции, типы, self, декораторы и многострочные строки. Если Pygments
недоступен или для языка нет лексера, работает встроенный однопроходный
регексп-лексер — грубее, но без зависимостей.

Нужен и vcs-китам (дифф), и session (fenced-блоки в ответах Claude,
diff правок), поэтому лежит в корне пакета modules.
"""

import difflib
import os
import re
import sys
from typing import Callable

from kittens.tui.operations import styled

from .theme import palette


def _load_pygments() -> 'tuple[Callable, type[Exception]] | None':
    """(get_lexer_for_filename, ClassNotFound) из plugins/vendor;
    None — библиотеки нет.
    """
    vendor = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'vendor')
    if os.path.isdir(vendor) and vendor not in sys.path:
        sys.path.append(vendor)   # append: свой pygments у пользователя важнее
    try:
        from pygments.lexers import get_lexer_for_filename
        from pygments.util import ClassNotFound
    except ImportError:
        return None
    return get_lexer_for_filename, ClassNotFound


_PYGMENTS = _load_pygments()


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

_P = palette()   # активная тема — FAMILIAR_THEME
C_COMMENT = _P['comment']
C_DOC = _P['doc']
C_STRING = _P['string']
C_NUMBER = _P['number']
C_CONST = _P['const']
C_KWCONST = _P['kwconst']
C_KEYWORD = _P['keyword']
C_FUNC = _P['func']
C_CLASS = _P['cls']
C_DECORATOR = _P['decorator']
C_SELF = _P['self']
C_BUILTIN = _P['builtin']
C_OPERATOR = _P['operator']
C_PUNCT = _P['punct']
C_ERROR = _P['error']

# Ключи — строковые пути токенов Pygments; проверяются от частного к
# общему (Token.Name.Function → Token.Name → Token), поэтому здесь
# достаточно перечислить только те роли, что отличаются от родителя.
_TOKEN_COLOR = {
    'Token.Comment': C_COMMENT,
    'Token.Literal.String': C_STRING,
    'Token.Literal.String.Doc': C_DOC,
    'Token.Literal.String.Escape': C_KWCONST,
    'Token.Literal.String.Interpol': C_SELF,
    'Token.Literal.String.Affix': C_KEYWORD,        # префикс f'' / b''
    'Token.Literal.Number': C_NUMBER,
    'Token.Keyword': C_KEYWORD,
    'Token.Keyword.Constant': C_KWCONST,
    'Token.Keyword.Type': C_CLASS,
    'Token.Operator': C_OPERATOR,
    'Token.Operator.Word': C_KEYWORD,               # and / or / not / in
    'Token.Punctuation': C_PUNCT,
    'Token.Name.Function': C_FUNC,
    'Token.Name.Function.Magic': C_FUNC,
    'Token.Name.Class': C_CLASS,
    'Token.Name.Namespace': C_CLASS,
    'Token.Name.Exception': C_CLASS,
    'Token.Name.Decorator': C_DECORATOR,
    'Token.Name.Builtin': C_BUILTIN,
    'Token.Name.Builtin.Pseudo': C_SELF,            # self / this / cls
    'Token.Name.Constant': C_CONST,
    'Token.Name.Variable': C_SELF,
    'Token.Name.Tag': C_KEYWORD,
    'Token.Name.Attribute': C_FUNC,
    'Token.Error': C_ERROR,
}
# Выше этого размера файл не лексим: Pygments стоит времени, а дифф
# грузится на каждый шаг курсора по дереву. Такие файлы получат
# грубую подсветку встроенным лексером — построчную и мгновенную.
MAX_HIGHLIGHT_BYTES = 400_000

_MISSING = object()          # None — валидный цвет («не красить»), нужен свой маркер
_TOKEN_CACHE: dict = {}      # token -> (цвет, это ли имя): str(token) дороже, чем кажется
_PYG_CACHE: dict = {}

ADD_BG = _P['add_bg']
DEL_BG = _P['del_bg']
ADD_WORD_BG = _P['add_word_bg']
DEL_WORD_BG = _P['del_word_bg']
ADD_FOCUS_BG = _P['add_focus_bg']
DEL_FOCUS_BG = _P['del_focus_bg']
CURSOR_BG = _P['cursor_bg']
SEL_RANGE_BG = _P['sel_range_bg']

# Ниже этой похожести строки считаем разными: подсвечивать в них
# «изменившиеся слова» бессмысленно — подсветилась бы вся строка.
WORD_DIFF_RATIO = 0.3
# Внутри блока правок строки спариваются позиционно, поэтому в паре
# может оказаться что угодно (комментарий и вызов функции); общих
# пробелов и запятых хватает, чтобы пройти WORD_DIFF_RATIO. Если
# «изменившиеся слова» покрывают больше этой доли строки, подсветка
# ничего не выделяет — гасим её, изменение и так видно по строке.
WORD_DIFF_COVER = 0.6

# Язык из инфо-строки fenced-блока markdown → расширение для лексера.
LANG_EXT = {
    'python': '.py', 'py': '.py',
    'javascript': '.js', 'js': '.js', 'jsx': '.jsx',
    'typescript': '.ts', 'ts': '.ts', 'tsx': '.tsx',
    'go': '.go', 'rust': '.rs', 'rs': '.rs',
    'java': '.java', 'kotlin': '.kt', 'swift': '.swift',
    'c': '.c', 'cpp': '.cpp', 'c++': '.cpp', 'cs': '.cs',
    'php': '.php', 'ruby': '.rb', 'lua': '.lua', 'haskell': '.hs',
    'sql': '.sql', 'sh': '.sh', 'bash': '.sh', 'zsh': '.sh', 'shell': '.sh',
    'json': '.json', 'yaml': '.yaml', 'yml': '.yaml', 'toml': '.toml',
}


_WORD_SPLIT = re.compile(r'\w+|\s+|[^\w\s]')

# Ровно те разделители, по которым рвёт str.splitlines(): цвета
# индексируются строками, что DiffSource режет тем же splitlines, и
# расхождение (form-feed, U+2028, одинокий \r) сдвинуло бы подсветку.
_LINE_SPLIT = re.compile('\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]')


def word_ranges(old: str, new: str) -> tuple[set[int], set[int], float]:
    """(изменившиеся символы old, то же для new, похожесть 0..1)."""
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


def strong_set(marked: set[int], ratio: float, line: str) -> 'set[int] | None':
    """Символы строки под word-diff подсветку, либо None — когда пара
    строк непохожа или подсветка накрыла бы почти всю строку.

    Отступ в marked не попадает, поэтому долю считаем от кода без
    ведущих пробелов.
    """
    code = line.strip()
    if not code or ratio < WORD_DIFF_RATIO:
        return None
    return marked if len(marked) <= WORD_DIFF_COVER * len(code) else None


def _token_style(token) -> 'tuple[int | None, bool]':
    """(цвет токена, имя ли это). Цвет — самый частный из объявленных,
    ищем поднимаясь к родителю (Token.Name.Function.Magic → … → Token).
    """
    cached = _TOKEN_CACHE.get(token, _MISSING)
    if cached is not _MISSING:
        return cached
    color, node = None, token
    while node is not None:
        if str(node) in _TOKEN_COLOR:
            color = _TOKEN_COLOR[str(node)]
            break
        node = node.parent
    style = (color, str(token).startswith('Token.Name'))
    _TOKEN_CACHE[token] = style
    return style


def _pygments_lexer(ext: str, startinline: bool = True):
    if not _PYGMENTS or not ext:
        return None
    key = (ext, startinline)
    if key in _PYG_CACHE:
        return _PYG_CACHE[key]
    by_filename, class_not_found = _PYGMENTS
    opts = dict(stripnl=False, stripall=False, ensurenl=False)
    # stripnl/stripall выключены: номера строк должны совпадать с
    # файлом, лишний перенос сдвинул бы всю раскраску. startinline —
    # чтобы php-код без открывающего <?php (fenced-блок) всё-таки
    # подсвечивался; для html+php он, наоборот, загоняет весь текст в
    # inline-php и ломает html/blade вокруг вставок — там он выключен.
    if startinline:
        opts['startinline'] = True
    try:
        lexer = by_filename('x' + ext, **opts)
    except (class_not_found, ImportError):
        lexer = None      # урезанный вендоринг: модуля лексера может не быть
    _PYG_CACHE[key] = lexer
    return lexer


_PHP_EXTS = {'.php', '.phtml', '.blade.php'}
# Blade/HTML-шаблон против голого php-фрагмента: тег <?php, html-тег
# или blade-вставка ({{ }}, @foreach) значит, что вне php идёт обычный
# текст — лексим как html+php, иначе '#' стало бы php-комментарием, а
# <td> — операторами. Голый фрагмент (```php без <?php) — inline-php.
_PHP_TEMPLATE_HINT = re.compile(r'<\?php|<[A-Za-z/!]|\{\{|@[A-Za-z]')


def _php_lexer(text: str):
    if _PHP_TEMPLATE_HINT.search(text):
        return _pygments_lexer('.phtml', startinline=False)   # html снаружи, php внутри
    return _pygments_lexer('.php')                             # голый фрагмент — inline-php


def _is_call(tokens: list, idx: int) -> bool:
    """За именем — открывающая скобка, значит это вызов функции.

    Лексеры метят Name.Function только в объявлении (`def foo`), а IDE
    красит и вызовы; отличить их можно лишь по следующему токену.
    """
    for _token, value in tokens[idx + 1:]:
        if not value.strip():
            continue                     # пробелы между именем и скобкой
        return value.startswith('(')
    return False


def _name_color(value: str, tokens: list, idx: int) -> 'int | None':
    """Цвет имени, которое лексер не отнёс ни к какой роли. Соглашения
    об именах те же, на которые смотрит глаз в IDE.
    """
    if _is_call(tokens, idx):
        return C_FUNC
    if len(value) > 1 and value.isupper():
        return C_CONST                   # UPPER_CASE — константа
    if value[:1].isupper():
        return C_CLASS                   # CapWords — класс или тип
    return None


def text_colors(text: str, ext: str) -> 'list[list[int | None]] | None':
    """Цвет каждого символа текста, разложенный по строкам, либо None —
    когда Pygments недоступен или не знает язык (тогда зовущий
    откатывается на построчный _fg_map).

    Лексим текст целиком, а не построчно: только так видны докстринги,
    многострочные строки и f-string-интерполяция.
    """
    lexer = _php_lexer(text) if ext in _PHP_EXTS else _pygments_lexer(ext)
    if lexer is None or len(text) > MAX_HIGHLIGHT_BYTES:
        return None
    tokens = list(lexer.get_tokens(text))
    lines: 'list[list[int | None]]' = [[]]
    for idx, (token, value) in enumerate(tokens):
        color, is_name = _token_style(token)
        if color is None and is_name:
            color = _name_color(value, tokens, idx)
        elif color == C_OPERATOR and value in ('.', '->', '::'):
            color = C_PUNCT              # доступ к члену — разделитель, не операция
        # по кускам между переносами, а не посимвольно: на большом
        # файле разница в разы
        chunks = _LINE_SPLIT.split(value)
        for i, chunk in enumerate(chunks):
            if i:
                lines.append([])
            if chunk:
                lines[-1].extend([color] * len(chunk))
    return lines


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


def fit_fgs(fgs: 'list[int | None] | None', start: int,
            length: int) -> 'list[int | None] | None':
    """Кусок карты цветов под видимый срез строки: с start, длиной
    length, добитый None (усечение могло дописать многоточие).
    """
    if fgs is None:
        return None
    cut = fgs[start:start + length]
    return cut + [None] * (length - len(cut))


def render_code(code: str, ext: str, base_bg: 'int | None' = None,
                strong: 'set | None' = None, strong_bg: 'int | None' = None,
                fgs: 'list | None' = None) -> str:
    """Код → ANSI: fg по синтаксису; фон = strong_bg на символах из
    strong, иначе base_bg.

    fgs — готовые цвета символов (Pygments лексит файл целиком). Без
    них строка лексится сама: Pygments по одной строке, а если его нет
    или язык незнаком — встроенным лексером.
    """
    if fgs is None:
        single = text_colors(code, ext)
        fgs = single[0] if single else _fg_map(code, ext)
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
