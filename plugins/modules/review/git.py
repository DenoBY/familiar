"""Git-слой review-кита: сбор списка изменённых файлов для working/staged/branch.

Специфика review (незакоммиченные правки и сравнение с базовой веткой) поверх общих
git-примитивов из modules.vcs.git. Без зависимостей от TUI.
"""

import os

from modules.vcs.git import (
    _classify, _count_lines, _diff_name_status, git_numstat, has_head, run_git,
)

# Ре-экспорт примитивов: тесты берут их отсюда (import modules.review.git as G).
from modules.vcs.git import git_blob, git_root, read_text  # noqa: F401


def git_changes(root: str) -> list[dict]:
    """working: незакоммиченные правки (git status vs HEAD), включая untracked."""
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
        items.append({'kind': _classify(xy), 'path': path, 'orig': orig, 'xy': xy,
                      'untracked': '?' in xy})
        i += 1
    stats = git_numstat(root, 'HEAD') if has_head(root) else {}
    for it in items:
        if it['untracked']:
            it['stat'] = (_count_lines(os.path.join(root, it['path'])), 0)
        else:
            it['stat'] = stats.get(it['path'])
    items.sort(key=lambda it: it['path'])
    return items


def detect_base(root: str) -> str:
    """Базовая ветка для scope 'branch': origin/HEAD → main → master → develop."""
    out = run_git(root, 'symbolic-ref', '--quiet', 'refs/remotes/origin/HEAD')
    if out:
        return out.strip().rsplit('/', 1)[-1]
    for b in ('main', 'master', 'develop'):
        if run_git(root, 'rev-parse', '--verify', '-q', b) is not None:
            return b
    return 'main'


def scan_changes(root: str, scope: str, base: str) -> list[dict]:
    """Список изменённых файлов для выбранного скоупа (working / staged / branch)."""
    if scope == 'working':
        return git_changes(root)
    if scope == 'staged':
        items, stats = _diff_name_status(root, '--cached'), git_numstat(root, '--cached')
    else:                                            # branch: рабочее дерево vs base
        items, stats = _diff_name_status(root, base), git_numstat(root, base)
    for it in items:
        it['stat'] = stats.get(it['path'])
    items.sort(key=lambda it: it['path'])
    return items
