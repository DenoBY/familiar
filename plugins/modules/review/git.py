"""Git-слой review-кита: сбор списка изменённых файлов.

Скоупы working/staged/branch. Специфика review (незакоммиченные
правки и сравнение с базовой веткой) поверх общих git-примитивов
из modules.vcs.git. Без зависимостей от TUI.
"""

import os

# Ре-экспорт примитивов: тесты берут их отсюда
# (import modules.review.git as G).
from modules.vcs.git import (  # noqa: F401
    EMPTY_TREE,
    classify_status,
    count_lines,
    diff_name_status,
    git_blob,
    git_numstat,
    git_root,
    has_head,
    last_error,
    read_text,
    run_git,
)
from modules.vcs.util import is_noise


def git_changes(root: str) -> list[dict]:
    """working: незакоммиченные правки (git status vs HEAD),
    включая untracked.
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
        items.append({'kind': classify_status(xy), 'path': path, 'orig': orig, 'xy': xy,
                      'untracked': '?' in xy})
        i += 1
    stats = git_numstat(root, 'HEAD') if has_head(root) else {}
    for it in items:
        if it['untracked']:
            # noise-каталоги (venv, node_modules…) не читаем: их
            # может быть тысячи, а в дереве они по умолчанию
            # скрыты — статистика не нужна.
            noise = is_noise(it['path'])
            it['stat'] = None if noise else (count_lines(os.path.join(root, it['path'])), 0)
        else:
            it['stat'] = stats.get(it['path'])
    items.sort(key=lambda it: it['path'])
    return items


def detect_base(root: str) -> str:
    """Базовая ветка для scope 'branch'.

    Порядок: origin/HEAD → main → master → develop.
    """
    out = run_git(root, 'symbolic-ref', '--quiet', 'refs/remotes/origin/HEAD')
    if out:
        # срезаем весь префикс, а не последний сегмент:
        # default-ветка может быть со слэшем
        # (refs/remotes/origin/release/1.0 → release/1.0)
        return out.strip().removeprefix('refs/remotes/origin/')
    for b in ('main', 'master', 'develop'):
        if run_git(root, 'rev-parse', '--verify', '-q', b) is not None:
            return b
    return 'main'


def merge_base(root: str, base: str) -> str:
    """Точка расхождения ветки с base.

    Сам base, если merge-base не вычислился.
    """
    out = run_git(root, 'merge-base', base, 'HEAD')
    return out.strip() if out else base


def scan_changes(root: str, scope: str, base: str) -> list[dict]:
    """Список изменённых файлов для выбранного скоупа.

    Скоуп: working / staged / branch.
    """
    if scope == 'working':
        return git_changes(root)
    if scope == 'staged':
        # явная ревизия: в репозитории без коммитов индекс
        # сравнивается с пустым деревом (голый --cached там
        # поддержан не всеми версиями git)
        ref = 'HEAD' if has_head(root) else EMPTY_TREE
        items = diff_name_status(root, '--cached', ref)
        stats = git_numstat(root, '--cached', ref)
    else:
        # merge-base, а не сам base: two-dot diff против
        # ушедшего вперёд base показал бы чужие коммиты base
        # как «обратные» изменения ветки
        ref = merge_base(root, base)
        items, stats = diff_name_status(root, ref), git_numstat(root, ref)
    for it in items:
        it['stat'] = stats.get(it['path'])
    items.sort(key=lambda it: it['path'])
    return items
