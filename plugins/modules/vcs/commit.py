"""Снимок коммита как источник изменений: изменённые им файлы и их
содержимое до/после.

Поверх общих git-примитивов из modules.vcs.git, без зависимостей от
TUI. Парная сторона — modules.vcs.worktree (рабочее дерево); история
коммитов и работа с удалёнками живут в modules.log.git.
"""

from .git import EMPTY_TREE, diff_name_status, git_blob, git_numstat, run_git


def first_parent(root: str, sha: str) -> str:
    """Первый родитель коммита; для корневого (без родителя) — пустое
    дерево.
    """
    out = run_git(root, 'rev-parse', '--verify', '-q', f'{sha}^')
    return out.strip() if out else EMPTY_TREE


def commit_files(root: str, sha: str, parent: 'str | None' = None) -> list[dict]:
    """Изменённые файлы коммита (vs первый родитель) со статистикой +/−.

    parent — заранее вычисленный первый родитель (иначе считается сам):
    позволяет не дёргать rev-parse повторно для того же коммита.
    """
    if parent is None:
        parent = first_parent(root, sha)
    items = diff_name_status(root, parent, sha)
    stats = git_numstat(root, parent, sha)
    for it in items:
        it['stat'] = stats.get(it['path'])
        it['untracked'] = False
    items.sort(key=lambda it: it['path'])
    return items


def commit_contents(root: str, sha: str, it: dict,
                    parent: 'str | None' = None) -> tuple[str, str]:
    """(before, after) для файла коммита: содержимое у родителя и в
    самом коммите.
    """
    if parent is None:
        parent = first_parent(root, sha)
    path = it['path']
    src = it.get('orig') or path
    before = '' if it['kind'] == 'added' else git_blob(root, parent, src)
    after = '' if it['kind'] == 'deleted' else git_blob(root, sha, path)
    return before, after
