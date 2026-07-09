"""Лёгкая подсветка синтаксиса и word-diff: общий модуль всех китов.

Лексер грубый и намеренно однопроходный — строка кода превращается
в ANSI без разбора грамматики языка. Нужен и vcs-китам (дифф), и
session (fenced-блоки в ответах Claude, diff правок), поэтому лежит
в корне пакета modules.
"""

import difflib
import re

from kittens.tui.operations import styled


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

ADD_BG = 22        # тёмно-зелёный фон добавленных строк (256-цвет)
DEL_BG = 52        # тёмно-красный фон удалённых строк
ADD_WORD_BG = 28   # ярче — на изменившихся словах (word-diff)
DEL_WORD_BG = 88
SEL_RANGE_BG = 25  # фон выделения — синий, чтобы читался и поверх add/del

# Ниже этой похожести строки считаем разными: подсвечивать в них
# «изменившиеся слова» бессмысленно — подсветилась бы вся строка.
WORD_DIFF_RATIO = 0.3

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


def word_ranges(old: str, new: str) -> tuple:
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
    """Код → ANSI: fg по синтаксису; фон = strong_bg на символах из
    strong, иначе base_bg.
    """
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
