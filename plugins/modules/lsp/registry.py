"""Реестр языковых серверов: какой сервер отвечает за файл и как его
запускать.

Три уровня конфигов, ближний перекрывает дальний по полям (как
languages.toml в Helix): встроенный `config/lsp.conf` → пользовательский
`~/.config/familiar/lsp.conf` → проектный `<репо>/.familiar/lsp.conf`.
Третий уровень нужен монорепозиториям, где корень и сервер приходится
задавать руками.

Модуль на чистом stdlib и без импортов kitty: его импортирует и CLI,
чтобы разбор конфига не разъехался между `familiar lsp` и китом.
"""

import hashlib
import os
from typing import NamedTuple


class ServerSpec(NamedTuple):
    lang: str
    exts: 'tuple[str, ...]'
    language_id: str
    argv: 'tuple[str, ...]'
    roots: 'tuple[str, ...]'
    roots_mode: str            # 'nearest' | 'git'
    init_options: dict
    settings: dict
    env: 'dict[str, str]'
    busy: str                  # нотификация «индексация пошла»
    ready: str                 # нотификация «индексация кончилась»
    timeout: float
    shutdown_wait: float
    install: 'tuple[str, ...]'


# От __file__, как PALETTE_DIR в theme.py: переменную окружения kitty
# китенам не передаёт, а раскладка brew повторяет раскладку
# репозитория. Модуль лежит на уровень глубже — отсюда лишний dirname
CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))),
    'config')

PROJECT_CONFIG = os.path.join('.familiar', 'lsp.conf')

DEFAULT_TIMEOUT = 5.0
DEFAULT_SHUTDOWN_WAIT = 0.15

_cache: 'dict[tuple, dict]' = {}


def builtin_path() -> str:
    return os.path.join(CONFIG_DIR, 'lsp.conf')


def user_path() -> str:
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(base, 'familiar', 'lsp.conf')


def project_path(project_root: str) -> str:
    return os.path.join(project_root, PROJECT_CONFIG) if project_root else ''


def lsp_home() -> str:
    base = os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
    return os.path.join(base, 'familiar', 'lsp')


def index_home() -> str:
    """Индексы отдельно от самих серверов: `familiar lsp clean` сносит
    их, а установленные пакеты трогать не должен.
    """
    return os.path.join(lsp_home(), 'index')


def cache_dir(lang: str, root: str) -> str:
    """Куда сервер кладёт свой индекс: имя репозитория для читаемости
    плюс хэш пути, чтобы одноимённые проекты не делили кэш.
    """
    digest = hashlib.sha1(root.encode('utf-8')).hexdigest()[:8]
    name = os.path.basename(root.rstrip('/')) or 'repo'
    return os.path.join(index_home(), lang, f'{name}-{digest}')


def server_home() -> str:
    """Префикс, куда `familiar lsp install` ставит npm-серверы."""
    return os.path.join(lsp_home(), 'servers')


def bin_dir() -> str:
    return os.path.join(server_home(), 'bin')


# ─────────────────────────── разбор ───────────────────────────

def parse(text: str) -> 'dict[str, dict[str, list]]':
    """Блоки конфига: язык → поле → список значений (по значению на
    каждое вхождение поля).
    """
    blocks: 'dict[str, dict[str, list]]' = {}
    current: 'dict[str, list] | None' = None
    for line in text.splitlines():
        parts = line.split('#', 1)[0].split()
        if not parts:
            continue
        if parts[0] == 'server' and len(parts) >= 2:
            current = blocks.setdefault(parts[1], {})
            continue
        if current is None or len(parts) < 2:
            continue
        current.setdefault(parts[0], []).append(parts[1:])
    return blocks


def load(project_root: str = '') -> 'dict[str, dict[str, list]]':
    """Слитые три уровня без выключенных блоков."""
    paths = (builtin_path(), user_path(), project_path(project_root))
    key = (paths, tuple(_mtime(p) for p in paths))
    cached = _cache.get(key)
    if cached is not None:
        return cached
    blocks: 'dict[str, dict[str, list]]' = {}
    for path in paths:
        if path:
            blocks = _merge(blocks, parse(_read(path)))
    live = {lang: fields for lang, fields in blocks.items()
            if _first(fields, 'disabled', 'no') not in ('yes', 'true', '1')}
    _cache[key] = live
    return live


def reset_cache() -> None:
    _cache.clear()


def _merge(base: dict, extra: dict) -> dict:
    out = {lang: dict(fields) for lang, fields in base.items()}
    for lang, fields in extra.items():
        out.setdefault(lang, {}).update(fields)   # поле перекрывается целиком
    return out


def _read(path: str) -> str:
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except OSError:
        return ''


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime if path else 0.0
    except OSError:
        return 0.0


def _values(fields: dict, name: str) -> 'list[list[str]]':
    return fields.get(name) or []


def _first(fields: dict, name: str, default: str = '') -> str:
    rows = _values(fields, name)
    return rows[0][0] if rows and rows[0] else default


def _flat(fields: dict, name: str) -> 'tuple[str, ...]':
    return tuple(tok for row in _values(fields, name) for tok in row)


# ─────────────────────── выбор и подстановки ───────────────────────

def for_path(rel: str, project_root: str = '',
             first_line: str = '') -> 'str | None':
    """Язык по имени файла, а если расширения нет — по shebang.

    Составное расширение важнее простого (`.blade.php` перекрывает
    `.php`), а при равной длине побеждает блок, объявленный позже:
    новые блоки приходят из ближних к проекту конфигов, и они должны
    перебивать встроенные.
    """
    blocks = load(project_root)
    name = os.path.basename(rel).lower()
    best: 'tuple[int, str] | None' = None
    for lang, fields in blocks.items():
        for ext in _flat(fields, 'extensions'):
            if name.endswith(ext.lower()) and (best is None or len(ext) >= best[0]):
                best = (len(ext), lang)
    if best:
        return best[1]
    return _by_shebang(blocks, first_line)


def _by_shebang(blocks: dict, first_line: str) -> 'str | None':
    """Скрипты без расширения (`bin/familiar`, git-хуки) опознаём по
    первой строке — иначе для них не нашлось бы никакого сервера.
    """
    if not first_line.startswith('#!'):
        return None
    # `#!/usr/bin/env python3` и `#!/bin/bash -e`: имя интерпретатора —
    # последнее слово пути, флаги игнорируем
    words = [os.path.basename(w) for w in first_line[2:].split()
             if not w.startswith('-')]
    for lang, fields in blocks.items():
        for token in _flat(fields, 'shebang'):
            if token.lower() in words:
                return lang
    return None


def spec_for(lang: str, root: str, project_root: str = '') -> 'ServerSpec | None':
    fields = load(project_root or root).get(lang)
    if not fields:
        return None
    subst = {'CACHE': cache_dir(lang, root), 'ROOT': root,
             'SERVERS': server_home(), 'HOME': os.path.expanduser('~')}
    return ServerSpec(
        lang=lang,
        exts=_flat(fields, 'extensions'),
        language_id=_first(fields, 'language', lang),
        argv=tuple(expand(tok, subst) for tok in _flat(fields, 'command')),
        roots=_flat(fields, 'roots'),
        roots_mode=_first(fields, 'roots-mode', 'nearest'),
        init_options=_options(fields, 'initopt', subst),
        settings=_options(fields, 'setting', subst),
        env={row[0]: expand(' '.join(row[1:]), subst)
             for row in _values(fields, 'env') if len(row) >= 2},
        busy=_first(fields, 'busy'),
        ready=_first(fields, 'ready'),
        timeout=_number(_first(fields, 'timeout'), DEFAULT_TIMEOUT),
        shutdown_wait=_number(_first(fields, 'shutdown-wait'),
                              DEFAULT_SHUTDOWN_WAIT),
        install=_flat(fields, 'install'),
    )


def expand(value: str, subst: 'dict[str, str]') -> str:
    """`${CACHE}`, `${ROOT}`, `${HOME}` и `${env:VAR}` в значении."""
    for name, replacement in subst.items():
        value = value.replace('${%s}' % name, replacement)
    while '${env:' in value:
        head, _, rest = value.partition('${env:')
        name, closed, tail = rest.partition('}')
        if not closed:
            break
        value = head + os.environ.get(name, '') + tail
    return value


def _options(fields: dict, name: str, subst: 'dict[str, str]') -> dict:
    """Повторяемые `initopt`/`setting` с точечными ключами → вложенный
    словарь, как ждут серверы (`gopls.directoryFilters`).

    Один ключ, названный несколько раз, копит значения в список: так
    длинный `files.exclude` пишется в конфиге несколькими читаемыми
    строками, а не одной на пол-экрана.
    """
    collected: 'dict[str, list[str]]' = {}
    order: 'list[str]' = []
    for row in _values(fields, name):
        if len(row) < 2:
            continue
        raw = [expand(tok, subst) for tok in row[1:]]
        if not any(raw):
            continue      # ${env:...} не задан — ключ не шлём вовсе
        if row[0] not in collected:
            order.append(row[0])
        collected.setdefault(row[0], []).extend(raw)
    out: dict = {}
    for key in order:
        _nest(out, key.split('.'), _coerce(collected[key]))
    return out


def _nest(dst: dict, keys: 'list[str]', value: object) -> None:
    for key in keys[:-1]:
        nxt = dst.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            dst[key] = nxt
        dst = nxt
    dst[keys[-1]] = value


def _coerce(raw: 'list[str]') -> object:
    if len(raw) > 1:
        return raw
    value = raw[0]
    if value in ('yes', 'true'):
        return True
    if value in ('no', 'false'):
        return False
    return value


def _number(value: str, default: float) -> float:
    try:
        return float(value)
    except ValueError:
        return default


# ──────────────────────── корень проекта ────────────────────────

def find_root(path: str, spec: ServerSpec, git_root: str) -> str:
    """Корень для `rootUri`: ближайший предок с файлом-маркером, но не
    выше git-корня. `roots-mode git` отключает поиск — в монорепо два
    composer.json иначе поднимут два сервера.
    """
    if spec.roots_mode == 'git' or not spec.roots:
        return git_root
    start = path if os.path.isdir(path) else os.path.dirname(path)
    current = os.path.abspath(start)
    stop = os.path.abspath(git_root)
    # прыжок мог увести за пределы репозитория (stdlib, node_modules):
    # оттуда подъём не упрётся в git-корень и найдёт маркер в чужом
    # дереве — лишний сервер, вытесняющий рабочий по MAX_SESSIONS
    if current != stop and not current.startswith(stop.rstrip(os.sep) + os.sep):
        return git_root
    while True:
        if any(os.path.exists(os.path.join(current, m)) for m in spec.roots):
            return current
        if current == stop or os.path.dirname(current) == current:
            return git_root
        current = os.path.dirname(current)
