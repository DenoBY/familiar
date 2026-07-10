"""Резолв определения символа: слово под курсором → файл:строка.

Source-agnostic слой без TUI. Два уровня точности:

- **контекст клика** (языко-независим): `symbol_at` → `(name, is_attr,
  is_call, qualifier)`; `obj.name` — метод, `name(` — вызов; учитывает
  `rank_candidates`;
- **резолв импортов** (точный файл): плагин-резолвер на язык (Python,
  JS/TS, PHP, Go) по импортам файла находит модуль → файл и ищет
  объявление там. Не вышло — падаем на repo-wide `git grep`.

Чистые функции отделены от subprocess (`run_git`), тестируются без git.
Границы имени в паттернах — классами `[^A-Za-z0-9_]`, а не `\\b`: движок
`git grep` (POSIX ERE vs PCRE) на разных сборках трактует `\\b`/`\\w`
по-разному, а классы работают везде.
"""

import json
import os
import re
from typing import NamedTuple

from .git import run_git


class Target(NamedTuple):
    path: str      # путь от корня репо
    line: int
    kind: str      # 'def' | 'method' (объявление с отступом) | 'assign'
    preview: str


class ImportHit(NamedTuple):
    path: str            # файл или каталог (Go), куда указывает импорт
    name: 'str | None'   # что искать внутри; None — сам модуль (прыжок в начало)


_IDENT = re.compile(r'[A-Za-z_]\w*')

# граница идентификатора для ERE-паттернов (начало/конец строки тоже)
_BB = r'(^|[^A-Za-z0-9_])'
_BA = r'([^A-Za-z0-9_]|$)'

_MAX_CANDIDATES = 50

_JS_EXTS = ('.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs')


def _match_at(plain: str, col: int) -> 're.Match | None':
    if col < 0:
        return None
    for m in _IDENT.finditer(plain):
        if m.start() <= col < m.end() or col == m.end():
            return m
    return None


# для выделения слова годится любое слово, включая кириллицу в
# комментариях — не только ASCII-идентификатор кода
_WORD = re.compile(r'\w+')


def word_span(plain: str, col: int) -> 'tuple[int, int] | None':
    """(start, end) слова под колонкой — выделение двойным кликом."""
    if col < 0:
        return None
    for m in _WORD.finditer(plain):
        if m.start() <= col < m.end() or col == m.end():
            return m.span()
    return None


def extract_symbol(plain: str, col: int) -> 'str | None':
    """Идентификатор под колонкой `col`. Клик по границе (col == конец)
    относим к слову слева — удобнее попадать по концу имени.
    """
    m = _match_at(plain, col)
    return m.group() if m else None


def symbol_at(plain: str, col: int) -> 'tuple[str, bool, bool, str | None] | None':
    """`(name, is_attr, is_call, qualifier)` под колонкой.

    is_attr — перед именем стоит `.` (`obj.name`); qualifier — объект
    слева от точки; is_call — за именем следует `(`.
    """
    m = _match_at(plain, col)
    if not m:
        return None
    s, e = m.span()
    is_attr = s > 0 and plain[s - 1] == '.'
    qualifier = None
    if is_attr:
        k = s - 1                       # позиция точки
        while k - 1 >= 0 and (plain[k - 1].isalnum() or plain[k - 1] == '_'):
            k -= 1
        qualifier = plain[k:s - 1] or None
    is_call = bool(re.match(r'[ \t]*\(', plain[e:]))
    return (m.group(), is_attr, is_call, qualifier)


# ───────────────────── паттерны объявления ─────────────────────

def def_patterns(ext: str, name: str) -> list[str]:
    """ERE-паттерны объявления `name` по расширению. Всегда добавляем
    generic-fallback: определение в смежном языке не должно теряться.
    """
    n = re.escape(name)
    ws = r'[ \t]+'
    lang = {
        '.py': [
            rf'{_BB}(def|class){ws}{n}{_BA}',
            rf'^[ \t]*{n}[ \t]*[:=]',                 # присваивание/аннотация
        ],
        '.php': [
            rf'{_BB}(function|class|trait|interface|const){ws}{n}{_BA}',
        ],
        '.go': [
            rf'{_BB}func(\s+\([^)]*\))?{ws}{n}{_BA}',
            rf'{_BB}type{ws}{n}{_BA}',
        ],
        '.rb': [
            rf'{_BB}(def|class|module){ws}{n}{_BA}',
        ],
    }
    js = [
        rf'{_BB}(function|class){ws}{n}{_BA}',
        rf'{_BB}(const|let|var){ws}{n}{_BA}',        # name = ... / => ...
        rf'^[ \t]*{n}[ \t]*[:(]',                     # метод объекта/класса
    ]
    for e in _JS_EXTS:
        lang[e] = js
    generic = [
        rf'{_BB}(def|class|func|fn|function|type|struct|interface|trait){ws}{n}{_BA}',
    ]
    pats = lang.get(ext) or []
    # generic дублирует язык-специфичный — дубли grep'у безвредны,
    # rank_candidates дедупит по (path, line)
    return pats + generic


_DECL = r'(def|class|func|fn|function|type|struct|interface|trait|module)'


def _classify(text: str, name: str) -> str:
    n = re.escape(name)
    if re.search(rf'{_BB}{_DECL}(\s+\([^)]*\))?[ \t]+{n}{_BA}', text):
        return 'method' if re.match(r'[ \t]+', text) else 'def'
    return 'assign'


def rank_candidates(raw: 'list[tuple[str, int, str]]', cur_rel: 'str | None',
                    name: str, *, is_attr: bool = False,
                    is_call: bool = False) -> list[Target]:
    """Ранжировать сырые совпадения. При `is_attr` приоритет методам
    (объявление с отступом), иначе — свободным функциям; при `is_call`
    присваивания уходят вниз. Дубли по (path, line) — прочь.
    """
    seen: set[tuple[str, int]] = set()
    targets: list[Target] = []
    for path, line, text in raw:
        key = (path, line)
        if key in seen:
            continue
        seen.add(key)
        targets.append(Target(path, line, _classify(text, name), text.strip()))

    def kind_rank(kind: str) -> int:
        order = ({'method': 0, 'def': 1, 'assign': 2} if is_attr
                 else {'def': 0, 'method': 1, 'assign': 2})
        r = order[kind]
        if is_call and kind == 'assign':
            r += 5
        return r

    # kind первичен: настоящее объявление важнее совпадения в текущем
    # файле (иначе call-сайт в нём обошёл бы def в соседнем файле)
    targets.sort(key=lambda t: (
        kind_rank(t.kind),
        0 if t.path == cur_rel else 1,
        t.path.count('/'),
        t.path,
        t.line,
    ))
    return targets[:_MAX_CANDIDATES]


# ────────────────────────── git grep ──────────────────────────

def _parse_grep(out: str) -> 'list[tuple[str, int, str]]':
    raw: list[tuple[str, int, str]] = []
    for ln in out.splitlines():
        parts = ln.split(':', 2)          # path:line:text (путь без ':' у git)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        raw.append((parts[0], int(parts[1]), parts[2]))
    return raw


def run_git_grep(root: str, patterns: list[str],
                 pathspec: 'str | None' = None) -> 'list[tuple[str, int, str]]':
    """git grep по OR-набору паттернов; пусто при отсутствии совпадений.

    `--untracked` — чтобы находить определения в новых (ещё не
    закоммиченных) файлах, частый кейс при ревью. `pathspec` сужает
    поиск до файла/каталога (резолв импортов).
    """
    args = ['grep', '-n', '-I', '-E', '--untracked']
    for p in patterns:
        args += ['-e', p]
    if pathspec:
        args += ['--', pathspec]
    out = run_git(root, *args)
    return _parse_grep(out) if out else []


def _list_files(root: str) -> list[str]:
    out = run_git(root, 'ls-files', '--cached', '--others', '--exclude-standard')
    return out.splitlines() if out else []


def _find_by_suffix(root: str, suffixes: list[str]) -> 'str | None':
    """Первый файл (трекнутый/новый), чей путь равен суффиксу или
    оканчивается на `/суффикс` (короткий путь — раньше). Пакет может
    лежать под префиксом (`plugins/modules/...`).
    """
    files = _list_files(root)
    best = None
    for suf in suffixes:
        for f in files:
            if f == suf or f.endswith('/' + suf):
                if best is None or f.count('/') < best.count('/'):
                    best = f
    return best


def _exists(root: str, rel: str) -> bool:
    return os.path.exists(os.path.join(root, rel))


# ─────────────── импорт-резолверы по языкам ───────────────

def _py_import(root: str, cur_rel: str, source: str, symbol: str) -> 'ImportHit | None':
    # многострочный `import (a, b)` схлопываем в одну строку
    src = re.sub(r'import[ \t]*\(([^)]*)\)',
                 lambda m: 'import ' + ' '.join(m.group(1).split()), source, flags=re.S)
    for m in re.finditer(r'^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+(.+)$',
                         src, re.M):
        dots, mod, names = m.group(1), m.group(2), m.group(3)
        for orig, alias in _import_names(names):
            if alias == symbol:
                path = _py_module_file(root, cur_rel, len(dots), mod)
                return ImportHit(path, orig) if path else None
    for m in re.finditer(r'^[ \t]*import[ \t]+(.+)$', src, re.M):
        for part in m.group(1).split(','):
            toks = part.split()
            if not toks:
                continue
            mod = toks[0]
            alias = toks[2] if len(toks) >= 3 and toks[1] == 'as' else mod.split('.')[0]
            if alias == symbol:
                path = _py_module_file(root, cur_rel, 0, mod)
                return ImportHit(path, None) if path else None
    return None


def _import_names(names: str) -> 'list[tuple[str, str]]':
    """`a, b as c` → [(a, a), (b, c)]: (имя в модуле, алиас)."""
    out = []
    for part in names.split(','):
        toks = part.replace('(', ' ').replace(')', ' ').split()
        if not toks or toks[0] == '*':
            continue
        if 'as' in toks:
            i = toks.index('as')
            out.append((toks[0], toks[i + 1]))
        else:
            out.append((toks[0], toks[0]))
    return out


def _py_module_file(root: str, cur_rel: str, level: int, mod: str) -> 'str | None':
    parts = mod.split('.') if mod else []
    if level > 0:                                   # относительный импорт
        base = os.path.dirname(cur_rel)
        for _ in range(level - 1):
            base = os.path.dirname(base)
        rel = '/'.join([p for p in [base, *parts] if p])
        for cand in (f'{rel}.py', f'{rel}/__init__.py'):
            if _exists(root, cand):
                return cand
        return None
    suffix = '/'.join(parts)                        # абсолютный dotted
    return _find_by_suffix(root, [f'{suffix}.py', f'{suffix}/__init__.py'])


# обе формы: group(1) — клауза импорта, group(2) — спецификатор модуля
_JS_IMPORT_RES = (
    re.compile(r'''import[ \t]+(.+?)[ \t]+from[ \t]+['"]([^'"]+)['"]'''),
    re.compile(r'''(?:const|let|var)[ \t]+([\w{},* \t]+?)[ \t]*=[ \t]*'''
               r'''require\([ \t]*['"]([^'"]+)['"]'''),
)


def _js_import(root: str, cur_rel: str, source: str, symbol: str) -> 'ImportHit | None':
    for rx in _JS_IMPORT_RES:
        for m in rx.finditer(source):
            hit = _js_clause(m.group(1), symbol)
            if hit is not None:          # (name|'') — символ есть в этом импорте
                path = _js_spec_file(root, cur_rel, m.group(2))
                return ImportHit(path, hit or None) if path else None
    return None


def _js_clause(clause: str, symbol: str) -> 'str | None':
    """Вернёт: имя-в-модуле (str) если символ импортирован именованно;
    '' (falsy) если символ — default/namespace/require-binding (прыжок в
    начало файла); None — символа в этом импорте нет.
    """
    braced = re.search(r'\{([^}]*)\}', clause)
    if braced:
        for orig, alias in _import_names(braced.group(1)):
            if alias == symbol:
                return orig
    ns = re.search(r'\*[ \t]+as[ \t]+(\w+)', clause)
    if ns and ns.group(1) == symbol:
        return ''
    # default import / require-binding: первый голый идентификатор
    head = re.match(r'[ \t]*(\w+)', clause)
    if head and head.group(1) == symbol:
        return ''
    return None


def _js_spec_file(root: str, cur_rel: str, spec: str) -> 'str | None':
    if not spec.startswith('.'):        # bare/alias (node_modules, tsconfig) — пас
        return None
    base = os.path.normpath(os.path.join(os.path.dirname(cur_rel), spec))
    cands = [f'{base}{e}' for e in _JS_EXTS]
    cands += [f'{base}/index{e}' for e in _JS_EXTS]
    for c in cands:
        if _exists(root, c):
            return c
    return None


def _php_import(root: str, cur_rel: str, source: str, symbol: str) -> 'ImportHit | None':
    for m in re.finditer(r'^[ \t]*use[ \t]+(?:function[ \t]+)?([\w\\]+)'
                         r'(?:[ \t]+as[ \t]+(\w+))?[ \t]*;', source, re.M):
        fqn, alias = m.group(1), m.group(2)
        name = alias or fqn.rstrip('\\').split('\\')[-1]
        if name == symbol:
            path = _php_psr4_file(root, fqn)
            leaf = fqn.rstrip('\\').split('\\')[-1]
            return ImportHit(path, leaf) if path else None
    return None


def _php_psr4_file(root: str, fqn: str) -> 'str | None':
    psr4 = _php_psr4_map(root)
    fqn = fqn.lstrip('\\')
    best = None
    for prefix, base in psr4.items():
        if fqn.startswith(prefix):
            tail = fqn[len(prefix):].replace('\\', '/')
            cand = '/'.join([p for p in [base.rstrip('/'), tail] if p]) + '.php'
            if _exists(root, cand) and (best is None or len(prefix) > best[0]):
                best = (len(prefix), cand)
    if best:
        return best[1]
    # без composer/PSR-4 — по имени класса (файл обычно = класс)
    return _find_by_suffix(root, [fqn.replace('\\', '/').split('/')[-1] + '.php'])


def _php_psr4_map(root: str) -> 'dict[str, str]':
    try:
        with open(os.path.join(root, 'composer.json'), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for key in ('autoload', 'autoload-dev'):
        block = data.get(key, {}).get('psr-4', {})
        for prefix, base in block.items():
            if isinstance(base, list):
                base = base[0] if base else ''
            out[prefix] = base
    return out


def _go_import(root: str, source: str, symbol: str,
               qualifier: 'str | None') -> 'ImportHit | None':
    if not qualifier:                   # Go резолвит через `pkg.Symbol`
        return None
    module, mod_root = _go_module(root)
    for path, alias in _go_imports(source):
        name = alias or path.rstrip('/').split('/')[-1]
        if name != qualifier:
            continue
        if module and path.startswith(module):
            tail = path[len(module):].lstrip('/')
            d = '/'.join([p for p in [mod_root, tail] if p])
            if _exists(root, d):
                return ImportHit(d, symbol)   # каталог пакета — grep внутри
        return None
    return None


def _go_imports(source: str) -> 'list[tuple[str, str | None]]':
    out: list[tuple[str, str | None]] = []
    block = re.search(r'import[ \t]*\((.*?)\)', source, re.S)
    if block:
        for ln in block.group(1).splitlines():
            m = re.search(r'(?:(\w+)[ \t]+)?"([^"]+)"', ln)
            if m:
                out.append((m.group(2), m.group(1)))
    for m in re.finditer(r'^[ \t]*import[ \t]+(?:(\w+)[ \t]+)?"([^"]+)"', source, re.M):
        out.append((m.group(2), m.group(1)))
    return out


def _go_module(root: str) -> 'tuple[str | None, str]':
    """(module path из go.mod, каталог go.mod относительно root)."""
    for cand in _find_gomod(root):
        try:
            with open(os.path.join(root, cand), encoding='utf-8') as f:
                for ln in f:
                    m = re.match(r'module[ \t]+(\S+)', ln)
                    if m:
                        return m.group(1), os.path.dirname(cand)
        except OSError:
            continue
    return None, ''


def _find_gomod(root: str) -> list[str]:
    return [f for f in _list_files(root) if os.path.basename(f) == 'go.mod']


def _resolve_import(root: str, ext: str, cur_rel: 'str | None', source: str,
                    symbol: str, qualifier: 'str | None') -> 'ImportHit | None':
    if cur_rel is None or not source:
        return None
    if ext == '.py':
        return _py_import(root, cur_rel, source, symbol)
    if ext in _JS_EXTS:
        return _js_import(root, cur_rel, source, symbol)
    if ext == '.php':
        return _php_import(root, cur_rel, source, symbol)
    if ext == '.go':
        return _go_import(root, source, symbol, qualifier)
    return None


def resolve_definition(root: str, cur_rel: 'str | None', ext: str, symbol: str, *,
                       is_attr: bool = False, is_call: bool = False,
                       qualifier: 'str | None' = None,
                       cur_source: 'str | None' = None) -> list[Target]:
    hit = _resolve_import(root, ext, cur_rel, cur_source or '', symbol, qualifier)
    if hit and hit.path:
        if hit.name is None:
            return [Target(hit.path, 1, 'def', hit.path)]
        hit_ext = os.path.splitext(hit.path)[1] or ext
        raw = run_git_grep(root, def_patterns(hit_ext, hit.name), pathspec=hit.path)
        ranked = rank_candidates(raw, cur_rel, hit.name, is_attr=is_attr, is_call=is_call)
        if ranked:
            return ranked
        return [Target(hit.path, 1, 'def', hit.path)]
    raw = run_git_grep(root, def_patterns(ext, symbol))
    ranked = rank_candidates(raw, cur_rel, symbol, is_attr=is_attr, is_call=is_call)
    return _prefer_self(ranked, cur_rel, qualifier)


# `self`/`this` привязаны к текущему классу: если объявление есть в этом
# же файле — это оно, чужие одноимённые методы не предлагаем (пикер не
# нужен). Нет в файле (метод унаследован) — оставляем всех кандидатов.
_SELF_REFS = frozenset({'self', 'this', 'cls', '$this'})


def _prefer_self(ranked: list[Target], cur_rel: 'str | None',
                 qualifier: 'str | None') -> list[Target]:
    if qualifier in _SELF_REFS and cur_rel:
        local = [t for t in ranked if t.path == cur_rel]
        if local:
            return local
    return ranked
