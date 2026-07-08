"""Общие git-примитивы: запуск git через subprocess и разбор его вывода.

Source-agnostic слой без зависимостей от TUI и от конкретного плагина: обёртка
`run_git`, чтение содержимого blob'ов и парсеры `git diff --name-status`/`--numstat`,
параметризованные произвольными ref'ами. Используется и review (рабочее дерево), и
log (коммиты).
"""

import os
import subprocess


def run_git(root: str, *args: str, binary: bool = False,
            timeout: int = 8) -> 'str | bytes | None':
    try:
        out = subprocess.run(['git', '-C', root, *args],
                             capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout if binary else out.stdout.decode('utf-8', 'replace')


def git_root(cwd: str) -> 'str | None':
    out = run_git(cwd, 'rev-parse', '--show-toplevel')
    return out.strip() if out else None


def has_head(root: str) -> bool:
    try:
        r = subprocess.run(['git', '-C', root, 'rev-parse', '--verify', '-q', 'HEAD'],
                           capture_output=True, timeout=5)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def read_text(path: str) -> str:
    try:
        with open(path, errors='replace') as f:
            return f.read()
    except OSError:
        return ''


def git_blob(root: str, ref: str, path: str) -> str:
    """Содержимое файла из git-объекта: ref='' → индекс (:path), иначе <ref>:path."""
    b = run_git(root, 'show', f'{ref}:{path}', binary=True)
    return b.decode('utf-8', 'replace') if b else ''


def _classify(xy: str) -> str:
    if '?' in xy:
        return 'untracked'
    if 'R' in xy:
        return 'renamed'
    if 'D' in xy:
        return 'deleted'
    if 'A' in xy:
        return 'added'
    return 'modified'


def _count_lines(path: str) -> int:
    try:
        with open(path, 'rb') as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def git_numstat(root: str, *args: str) -> 'dict[str, tuple[int | None, int | None]]':
    """path → (added, deleted) из `git diff --numstat <args>` (бинарники → (None, None))."""
    out = run_git(root, 'diff', '--numstat', *args)
    stats = {}
    if not out:
        return stats
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) < 3:
            continue
        a, d, path = parts[0], parts[1], parts[2]
        if ' => ' in path:   # переименование: "old => new" / "dir/{old => new}/f"
            path = path.replace('{', '').replace('}', '').split(' => ')[-1]
        stats[path] = (None if a == '-' else int(a), None if d == '-' else int(d))
    return stats


_NAME_STATUS = {'M': 'modified', 'A': 'added', 'D': 'deleted', 'T': 'modified'}


def _diff_name_status(root: str, *args: str) -> list[dict]:
    """items из `git diff --name-status -z <args>` (staged / vs ветка / коммит)."""
    raw = run_git(root, 'diff', '--name-status', '-z', *args)
    if raw is None:
        return []
    toks = raw.split('\0')
    items, i = [], 0
    while i < len(toks):
        st = toks[i]
        if not st:
            i += 1
            continue
        code = st[0]
        if code in ('R', 'C'):                       # переименование: код, old, new
            old = toks[i + 1] if i + 1 < len(toks) else ''
            new = toks[i + 2] if i + 2 < len(toks) else ''
            items.append({'kind': 'renamed', 'path': new, 'orig': old, 'untracked': False})
            i += 3
        else:
            path = toks[i + 1] if i + 1 < len(toks) else ''
            items.append({'kind': _NAME_STATUS.get(code, 'modified'), 'path': path,
                          'orig': None, 'untracked': False})
            i += 2
    return items
