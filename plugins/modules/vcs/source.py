"""Источник изменений для вьюера: откуда берутся файлы, их содержимое
и где искать по проекту.

Рабочее дерево (review) и снимок коммита (log) отличаются только этим,
поэтому разница собрана в один объект, а не размазана по хендлерам:
экран ревью одинаков для обоих и ходит в git только отсюда.

Веер по репозиториям тоже здесь: git-слой ниже (worktree, commit) о том,
что репозиториев бывает много, не знает.
"""

import os

from .commit import commit_contents, commit_files, first_parent
from .git import current_branch, git_blob, has_head, last_error, read_text
from .workspace import Workspace, map_repos
from .worktree import base_ref, scan_changes, scan_range


UNVERSIONED = 'Unversioned Files'


class Source:
    """Контракт источника. `rev` — ревизия для поиска по проекту
    (пусто — рабочее дерево), `mutable` — можно ли править файлы
    (stage/revert/комментарии осмысленны только в рабочем дереве).
    """

    mutable = False
    rev = ''

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws
        # ошибка git последнего files(): в мультирепо она снята в
        # чужом потоке, и хендлеру взять её больше негде
        self.error = ''

    @property
    def root(self) -> 'str | None':
        """Корень, когда он один на весь источник; в мультирепо его
        нет — там корень спрашивают у элемента (root_of).
        """
        return self.ws.single_root

    def root_of(self, it: 'dict | None') -> 'str | None':
        return (it or {}).get('repo') or self.root

    def files(self) -> list[dict]:
        raise NotImplementedError

    def contents(self, it: dict) -> tuple[str, str]:
        raise NotImplementedError

    def read(self, rel: str, repo: 'str | None' = None) -> str:
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

    def read(self, rel: str, repo: 'str | None' = None) -> str:
        return ''


class WorkTreeSource(Source):
    """Незакоммиченные правки: `git status` vs HEAD плюс untracked.

    В режиме vs_base сравнивает не с HEAD, а с точкой расхождения от
    базовой ветки: тогда видно всю работу ветки — и уже закоммиченное,
    и ещё нет.
    """

    mutable = True

    def __init__(self, ws: Workspace) -> None:
        super().__init__(ws)
        # есть ли HEAD и на какой ветке: обновляется при
        # пересканировании, а не на каждую загрузку диффа — иначе шаг
        # курсора по дереву стоил бы лишних git-процессов
        self._has_head: dict[str, bool] = {}
        self.branches: dict[str, str] = {}
        self.bases: 'dict[str, tuple[str, str]]' = {}   # репо → (база, sha)
        self._bases_known = False
        self.vs_base = False

    def files(self) -> list[dict]:
        if self.vs_base:
            self.find_bases()
        else:
            self.forget_bases()
        items = []
        self.error = ''
        for repo, res, err in map_repos(self.ws.repos, self._scan):
            self.error = self.error or err
            if res is None:
                continue          # этот репозиторий не прочитан, прочие покажем
            changes, self._has_head[repo.root], self.branches[repo.root] = res
            for it in changes:
                if self.ws.multi:
                    it['repo'] = repo.root
                if it.get('untracked'):
                    it['group'] = UNVERSIONED
            items += changes
        return items

    def _scan(self, repo) -> tuple[list[dict], bool, str]:
        base = self.bases.get(repo.root)
        changes = scan_range(repo.root, base[1]) if base else scan_changes(repo.root)
        return changes, has_head(repo.root), current_branch(repo.root)

    def find_bases(self) -> dict:
        """Базы репозиториев; спрашиваем до пересборки дерева, чтобы
        не перестраивать его впустую, когда сравнивать не с чем.

        Ответ держим до forget_bases(): каждый репозиторий стоит
        symbolic-ref и до шести rev-parse, а на переключение режима
        и следующую перерисовку дерева зовут по нескольку раз.
        """
        if not self._bases_known:
            self.bases = {repo.root: base for repo, base, _err
                          in map_repos(self.ws.repos, lambda r: base_ref(r.root)) if base}
            self._bases_known = True
        return self.bases

    def forget_bases(self) -> None:
        """Перепросить базы: точка расхождения уезжает от коммита в
        базовую ветку и от fetch.
        """
        self.bases = {}
        self._bases_known = False

    def base_name(self) -> str:
        """Общее имя базы для шапки; у разных репозиториев она может
        быть разной.
        """
        names = {name for name, _sha in self.bases.values()}
        return names.pop() if len(names) == 1 else 'base'

    def contents(self, it: dict) -> tuple[str, str]:
        root = self.root_of(it)
        after = self.read(it['path'], root)
        if it['untracked'] or not self._has_head.get(root):
            return '', after
        base = self.bases.get(root)
        ref = base[1] if base else 'HEAD'
        return git_blob(root, ref, it.get('orig') or it['path']), after

    def read(self, rel: str, repo: 'str | None' = None) -> str:
        root = repo or self.root
        if not root:
            return ''
        absp = os.path.join(root, rel)
        return read_text(absp) if os.path.exists(absp) else ''


class CommitSource(Source):
    """Изменения одного коммита относительно первого родителя.

    Всегда один репозиторий: log открывает коммит с ws.sub(его
    репозиторий), поэтому экран ревью коммита про мультирепо не знает
    вовсе — но имя репозитория в @-ссылках сохраняется.
    """

    def __init__(self, ws: Workspace, sha: str) -> None:
        super().__init__(ws)
        self.sha = sha
        self.rev = sha
        self.parent = first_parent(self.root, sha) if self.root else ''

    def files(self) -> list[dict]:
        if not self.root:
            return []
        items = commit_files(self.root, self.sha, self.parent)
        self.error = last_error()
        return items

    def contents(self, it: dict) -> tuple[str, str]:
        return commit_contents(self.root, self.sha, it, self.parent)

    def read(self, rel: str, repo: 'str | None' = None) -> str:
        return git_blob(self.root, self.sha, rel)
