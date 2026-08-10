"""Рабочее дерево как источник изменений: список незакоммиченных
правок и операции над ними (git add, откат).

Поверх общих git-примитивов из modules.vcs.git, без зависимостей
от TUI. Парная сторона — modules.vcs.commit (снимок коммита).
"""

import os

from .git import (
    classify_status,
    count_lines,
    git_numstat,
    has_head,
    run_git,
    set_error,
)
from .util import is_noise


# Потолок на построчный подсчёт untracked-файла: считается на КАЖДОМ
# скане (в т.ч. при живом refresh), а дамп на сотни мегабайт даёт
# только бесполезное «+N строк».
MAX_COUNT_BYTES = 4_000_000


def _too_big(path: str) -> bool:
    try:
        return os.path.getsize(path) > MAX_COUNT_BYTES
    except OSError:
        return True    # нет доступа/файла — считать нечего


def scan_changes(root: str) -> list[dict]:
    """Незакоммиченные правки (git status vs HEAD), включая
    untracked.
    """
    raw = run_git(root, 'status', '--porcelain=v1', '-z', '-uall')
    if raw is None:
        return []
    tokens = raw.split('\0')
    items, i = [], 0
    while i < len(tokens):
        tok = tokens[i]
        if not tok or len(tok) < 3:
            i += 1
            continue
        xy, path = tok[:2], tok[3:]
        orig = None
        if 'R' in xy or 'C' in xy:
            i += 1
            orig = tokens[i] if i < len(tokens) else None
        if xy == 'AD':
            # застейджен как новый и затем удалён с диска:
            # относительно HEAD изменений нет — не показываем
            i += 1
            continue
        items.append({'kind': classify_status(xy), 'path': path, 'orig': orig, 'xy': xy,
                      'untracked': '?' in xy})
        i += 1
    stats = git_numstat(root, 'HEAD') if has_head(root) else {}
    for it in items:
        if it['untracked']:
            # noise-каталоги (venv, node_modules…) не читаем: их
            # может быть тысячи, а в дереве они по умолчанию
            # скрыты — статистика не нужна. is_noise — по
            # относительному пути: в абсолютном сработали бы папки
            # над корнем репозитория.
            absp = os.path.join(root, it['path'])
            skip = is_noise(it['path']) or _too_big(absp)
            it['stat'] = None if skip else (count_lines(absp), 0)
        else:
            it['stat'] = stats.get(it['path'])
    items.sort(key=lambda it: it['path'])
    return items


def stage_paths(root: str, paths: list[str]) -> bool:
    """False — git отказал; причина в last_error()."""
    if not paths:
        return False
    return run_git(root, 'add', '--', *paths) is not None


def revert_paths(root: str, tracked: list[str], untracked: list[str]) -> bool:
    """Откатить файлы к HEAD (диск и индекс); untracked — удалить с
    диска, откатывать их не к чему.

    False — что-то не удалось (причина в last_error()); остальное всё
    равно откачено: частичный успех виднее в дереве, чем молчание.
    """
    ok = True
    if tracked:
        if not has_head(root):
            return False    # нет HEAD — восстанавливать не из чего
        ok = run_git(root, 'restore', '--source=HEAD', '--staged', '--worktree',
                     '--', *tracked) is not None
    for rel in untracked:
        try:
            os.remove(os.path.join(root, rel))
        except OSError as e:
            set_error(str(e))
            ok = False
    return ok
