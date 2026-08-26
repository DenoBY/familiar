"""Координаты и цели: между тем, что на экране, и тем, что в LSP.

Чистые функции без TUI и без процессов — тестируются в отрыве от
всего остального. Две тонкости, ради которых модуль и существует:
в строках диффа табы уже развёрнуты в пробелы, а LSP считает колонку
в UTF-16 code units, а не в символах Python.
"""

import os
from typing import Callable, NamedTuple
from urllib.parse import quote, unquote, urlsplit


class Target(NamedTuple):
    path: str      # от корня репо; вне репо (vendor, stdlib) — абсолютный
    line: int
    kind: str
    preview: str


# SymbolKind из спеки LSP — только те, что стоит показать в пикере
_KINDS = {
    5: 'class', 6: 'method', 9: 'method', 10: 'enum', 11: 'interface',
    12: 'func', 13: 'var', 14: 'const', 22: 'enum', 23: 'struct', 26: 'type',
}

# объявление, а не упоминание: помечается в пикере полосой
DECL_KINDS = frozenset({'class', 'method', 'func', 'interface', 'struct',
                        'type', 'enum', 'def'})

MAX_CANDIDATES = 50

TAB_WIDTH = 4


def raw_index(raw: str, expanded_col: int, tab: int = TAB_WIDTH) -> int:
    """Колонка в развёрнутой строке → индекс в исходной.

    diff_plain хранит текст после `.replace('\\t', '    ')` — это
    фиксированные четыре пробела, а не позиции табуляции, поэтому
    обратный проход линеен. Клик внутрь таба даёт его собственный
    индекс.
    """
    if expanded_col <= 0:
        return 0
    pos = 0
    for i, ch in enumerate(raw):
        width = tab if ch == '\t' else 1
        if pos + width > expanded_col:
            return i
        pos += width
    return len(raw)


def encode_character(raw: str, idx: int, encoding: str = 'utf-16') -> int:
    """Индекс символа → `character` в кодировке позиций сервера.

    По умолчанию LSP меряет в UTF-16 code units: кириллица занимает
    один юнит, всё вне BMP — два, и индекс Python-строки разъезжается
    с тем, что ждёт сервер.
    """
    head = raw[:idx]
    if encoding == 'utf-32':
        return len(head)
    if encoding == 'utf-8':
        return len(head.encode('utf-8'))
    return len(head.encode('utf-16-le')) // 2


def uri_from_path(path: str) -> str:
    return 'file://' + quote(os.path.abspath(path))


def path_from_uri(uri: str) -> str:
    """Путь из `file://…`; пусто для схем, которых мы не открываем."""
    if not uri.startswith('file:'):
        return ''
    return unquote(urlsplit(uri).path)


def rel_or_abs(path: str, root: str) -> str:
    """Внутри репо — путь от корня, снаружи — абсолютный.

    Цель в vendor или stdlib показываем read-only с диска, и
    относительный путь туда не ведёт.
    """
    if not root:
        return path
    prefix = root.rstrip('/') + '/'
    return path[len(prefix):] if path.startswith(prefix) else path


def location_target(loc: dict, root: str, preview: 'Callable[[str, int], str]',
                    kind: str = 'def') -> 'Target | None':
    """`Location` либо `LocationLink` → `Target`.

    linkSupport не объявляем, но разбираем и его: часть серверов
    отдаёт ссылки независимо от флага.
    """
    uri = loc.get('uri') or loc.get('targetUri') or ''
    rng = (loc.get('range') or loc.get('targetSelectionRange')
           or loc.get('targetRange') or {})
    path = path_from_uri(uri)
    if not path:
        return None
    line = int((rng.get('start') or {}).get('line', 0)) + 1
    rel = rel_or_abs(path, root)
    return Target(rel, line, kind, preview(rel, line))


# перегрузки (`@overload` в typeshed, шаблоны в C++) живут вплотную
# друг к другу: прыжок в любую из них показывает и остальные
OVERLOAD_SPAN = 40


def collapse_overloads(targets: 'list[Target]') -> 'list[Target]':
    """Схлопнуть определения, стоящие рядом в одном файле.

    `getattr` в typeshed объявлен шесть раз подряд — выбор между ними
    не решение пользователя, а шум: любое ведёт в одно место файла.
    """
    kept: 'list[Target]' = []
    for target in targets:
        near = any(t.path == target.path and abs(t.line - target.line) <= OVERLOAD_SPAN
                   for t in kept)
        if not near:
            kept.append(target)
    return kept


# файлы одних лишь сигнатур: рядом с ними обычно лежит настоящий код
_STUB_SUFFIXES = ('.pyi', '.d.ts')


def prefer_sources(targets: 'list[Target]') -> 'list[Target]':
    """Убрать стаб, если для того же модуля нашёлся исходник.

    pyright на `quote` отдаёт и `urllib/parse.pyi` с одной сигнатурой,
    и `urllib/parse.py` с реализацией. В ревью читают код, поэтому
    выбор между ними — лишний вопрос. Стаб остаётся, когда исходника
    нет вовсе: у встроенных функций его и не бывает.
    """
    sources = {_module(t.path) for t in targets if not _is_stub(t.path)}
    kept = [t for t in targets
            if not _is_stub(t.path) or _module(t.path) not in sources]
    return kept or targets


def _is_stub(path: str) -> bool:
    return path.endswith(_STUB_SUFFIXES)


def _module(path: str) -> str:
    name = os.path.basename(path)
    for suffix in _STUB_SUFFIXES:
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return os.path.splitext(name)[0]


def locations(result: object) -> 'list[dict]':
    """Ответ `textDocument/definition`: один Location, список или
    null.
    """
    if isinstance(result, dict):
        return [result]
    if isinstance(result, list):
        return [x for x in result if isinstance(x, dict)]
    return []


def rank_symbols(raw: 'list[dict]', name: str, cur_rel: 'str | None',
                 root: str, preview: 'Callable[[str, int], str]') -> 'list[Target]':
    """Ответ `workspace/symbol` → кандидаты для пикера.

    Точное совпадение имени важнее похожего, объявление важнее
    упоминания, текущий файл важнее соседнего.
    """
    seen: 'set[tuple[str, int]]' = set()
    scored: 'list[tuple[tuple, Target, bool]]' = []
    for sym in raw:
        if not isinstance(sym, dict):
            continue
        target = location_target(sym.get('location') or {}, root, preview,
                                 _KINDS.get(sym.get('kind'), 'symbol'))
        if target is None or (target.path, target.line) in seen:
            continue
        seen.add((target.path, target.line))
        found = sym.get('name') or ''
        scored.append((_symbol_rank(found, name, target, cur_rel), target,
                       found == name))
    # symbol-поиск нечёткий: рядом с искомым классом приезжают стабы
    # и однокоренные имена. Есть точные — остальные только мешают, а
    # единственный кандидат открывается сразу, без пикера
    exact = [row for row in scored if row[2]]
    scored = exact or scored
    scored.sort(key=lambda row: row[0])
    return prefer_sources([target for _, target, _ in scored[:MAX_CANDIDATES]])


def _symbol_rank(found: str, wanted: str, target: Target,
                 cur_rel: 'str | None') -> tuple:
    return (
        0 if found == wanted else 1,
        0 if target.kind in DECL_KINDS else 1,
        0 if target.path == cur_rel else 1,
        target.path.count('/'),
        target.path,
        target.line,
    )
