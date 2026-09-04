"""Рабочее дерево как источник изменений: список незакоммиченных
правок и операции над ними (git add, откат).

Поверх общих git-примитивов из modules.vcs.git, без зависимостей
от TUI. Парная сторона — modules.vcs.commit (снимок коммита).
"""

import os

from .git import (
    classify_status,
    count_lines,
    current_branch,
    diff_name_status,
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


# Ветки, от которых обычно ведут работу. Порядок — приоритет: то, на
# что указывает origin/HEAD, важнее угадывания по имени.
BASE_CANDIDATES = ('main', 'master', 'develop')


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
    _fill_stats(root, items, stats)
    items.sort(key=lambda it: it['path'])
    return items


def scan_range(root: str, base: str) -> list[dict]:
    """Изменения рабочего дерева относительно base: и закоммиченные в
    ветке, и ещё не закоммиченные, плюс untracked — вся работа ветки
    одним списком.
    """
    items = diff_name_status(root, base)
    items += _untracked(root)
    _fill_stats(root, items, git_numstat(root, base))
    items.sort(key=lambda it: it['path'])
    return items


def _untracked(root: str) -> list[dict]:
    raw = run_git(root, 'status', '--porcelain=v1', '-z', '-uall')
    out = []
    for tok in (raw or '').split('\0'):
        if tok.startswith('?? '):
            out.append({'kind': 'untracked', 'path': tok[3:], 'orig': None,
                        'xy': '??', 'untracked': True})
    return out


def _fill_stats(root: str, items: list[dict],
                stats: 'dict[str, tuple[int | None, int | None]]') -> None:
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


def base_ref(root: str) -> 'tuple[str, str] | None':
    """(имя базовой ветки, sha точки расхождения) — относительно чего
    смотреть работу ветки целиком. None — базы нет или мы на ней
    самой, и сравнивать не с чем.
    """
    cur = current_branch(root)
    for name in _base_names(root):
        if name.rsplit('/', 1)[-1] == cur:
            return None
        out = run_git(root, 'merge-base', name, 'HEAD')
        if out and out.strip():
            return name, out.strip()
    return None


def _base_names(root: str) -> list[str]:
    out = run_git(root, 'symbolic-ref', '--quiet', '--short', 'refs/remotes/origin/HEAD')
    names = [out.strip()] if out and out.strip() else []
    for name in BASE_CANDIDATES:
        names += [name, f'origin/{name}']
    seen, exists = set(), []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        if run_git(root, 'rev-parse', '--verify', '-q', name, timeout=5) is not None:
            exists.append(name)
    return exists


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
