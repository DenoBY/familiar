"""Источник изменений для вьюера: откуда берутся файлы, их содержимое
и где искать по проекту.

Рабочее дерево (review) и снимок коммита (log) отличаются только этим,
поэтому разница собрана в один объект, а не размазана по хендлерам:
экран ревью одинаков для обоих и ходит в git только отсюда.
"""

import os

from .commit import commit_contents, commit_files, first_parent
from .git import git_blob, has_head, read_text
from .worktree import scan_changes


UNVERSIONED = 'Unversioned Files'


class Source:
    """Контракт источника. `rev` — ревизия для поиска по проекту
    (пусто — рабочее дерево), `mutable` — можно ли править файлы
    (stage/revert/комментарии осмысленны только в рабочем дереве).
    """

    mutable = False
    rev = ''

    def __init__(self, root: 'str | None') -> None:
        self.root = root

    def files(self) -> list[dict]:
        raise NotImplementedError

    def contents(self, it: dict) -> tuple[str, str]:
        raise NotImplementedError

    def read(self, rel: str) -> str:
        """Файл целиком — для go-to-definition и результатов поиска."""
        raise NotImplementedError


class EmptySource(Source):
    """Заглушка, пока ревьюить нечего: экран уже собран (log сначала
    показывает список коммитов), а файлов ещё нет.
    """

    def files(self) -> list[dict]:
        return []

    def contents(self, it: dict) -> tuple[str, str]:
        return '', ''

    def read(self, rel: str) -> str:
        return ''


class WorkTreeSource(Source):
    """Незакоммиченные правки: `git status` vs HEAD плюс untracked."""

    mutable = True

    def __init__(self, root: 'str | None') -> None:
        super().__init__(root)
        # есть ли HEAD: обновляется при пересканировании, а не на каждую
        # загрузку диффа — иначе шаг курсора по дереву стоил бы вдвое
        # больше git-процессов
        self._has_head = False

    def files(self) -> list[dict]:
        if not self.root:
            return []
        self._has_head = has_head(self.root)
        items = scan_changes(self.root)
        for it in items:
            if it.get('untracked'):
                it['group'] = UNVERSIONED
        return items

    def contents(self, it: dict) -> tuple[str, str]:
        after = self.read(it['path'])
        if it['untracked'] or not self._has_head:
            return '', after
        return git_blob(self.root, 'HEAD', it.get('orig') or it['path']), after

    def read(self, rel: str) -> str:
        absp = os.path.join(self.root, rel)
        return read_text(absp) if os.path.exists(absp) else ''


class CommitSource(Source):
    """Изменения одного коммита относительно первого родителя."""

    def __init__(self, root: 'str | None', sha: str) -> None:
        super().__init__(root)
        self.sha = sha
        self.rev = sha
        self.parent = first_parent(root, sha) if root else ''

    def files(self) -> list[dict]:
        return commit_files(self.root, self.sha, self.parent) if self.root else []

    def contents(self, it: dict) -> tuple[str, str]:
        return commit_contents(self.root, self.sha, it, self.parent)

    def read(self, rel: str) -> str:
        return git_blob(self.root, self.sha, rel)
