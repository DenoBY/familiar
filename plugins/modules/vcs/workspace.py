"""Что открыто оверлеем: один репозиторий или папка над несколькими.

Киты запускаются из cwd окна, а оно бывает и верхней папкой, в которой
лежат независимые репозитории (~/Projects/yr с api, web, infra). Здесь
эта папка превращается в список корней, а остальной код спрашивает у
Workspace, один репозиторий на экране или много.

Слой без TUI и без знания о том, что показывают review и log: git-вееру
нужны только корни и порядок.
"""

import os
import threading
from collections import deque
from typing import Callable, NamedTuple

from .git import git_root, last_error
from .util import is_noise_dir


# Глубина скана подпапок: покрывает и ~/Projects/yr/api, и папку с
# группировкой по клиентам (yr/client-a/api). Глубже начинается
# содержимое самих проектов.
MAX_DEPTH = 2

# Потолок числа репозиториев: дальше экран нечитаем, а каждый корень
# стоит ещё одного git status при каждом обновлении.
MAX_REPOS = 24

MAX_WORKERS = 8


class Repo(NamedTuple):
    root: str    # абсолютный путь к корню репозитория
    name: str    # путь корня относительно базы: 'api', 'packages/ui'


class Workspace:
    """Папка, из которой открыт кит, и найденные в ней репозитории."""

    def __init__(self, base: str, repos: list[Repo], truncated: bool = False) -> None:
        self.base = base
        self.repos = repos
        self.truncated = truncated    # нашлось больше, чем показываем

    @classmethod
    def single(cls, root: str) -> 'Workspace':
        return cls(root, [Repo(root, '')])

    def sub(self, root: 'str | None') -> 'Workspace':
        """Один репозиторий этой рабочей области, но с прежней базой.

        Экран, показывающий один репозиторий (ревью коммита), про
        мультирепо знать не должен, а @-ссылки всё равно резолвятся
        из папки, откуда открыт кит, — значит имя репозитория в пути
        обязано сохраниться.
        """
        repo = self.repo_at(root) or Repo(root or self.base, '')
        return Workspace(self.base, [repo])

    @property
    def multi(self) -> bool:
        """Показывать ли уровень репозитория в UI."""
        return len(self.repos) > 1

    @property
    def single_root(self) -> 'str | None':
        return self.repos[0].root if len(self.repos) == 1 else None

    def repo_at(self, root: 'str | None') -> 'Repo | None':
        return next((r for r in self.repos if r.root == root), None)

    def name_of(self, root: 'str | None') -> str:
        repo = self.repo_at(root)
        return repo.name if repo else ''

    def rel_prefix(self, root: 'str | None') -> str:
        """Префикс к пути файла, чтобы @-ссылка резолвилась из базы.

        Кит наследует cwd окна, поэтому Claude Code развернёт @path
        относительно базы, а не корня репозитория; для одного
        репозитория в самой базе префикса нет.
        """
        name = self.name_of(root or self.single_root)
        return f'{name}/' if name else ''


def open_workspace(cwd: str) -> Workspace:
    """cwd внутри репозитория — он единственный; иначе ищем
    в подпапках.
    """
    root = git_root(cwd)
    if root:
        return Workspace(root, [Repo(root, '')])
    # на один больше потолка — чтобы отличить «ровно столько» от
    # «показываем не всё»
    found = discover_repos(cwd, limit=MAX_REPOS + 1)
    return Workspace(cwd, found[:MAX_REPOS], truncated=len(found) > MAX_REPOS)


def discover_repos(base: str, depth: int = MAX_DEPTH,
                   limit: int = MAX_REPOS) -> list[Repo]:
    """Репозитории в подпапках base: обход в ширину до глубины
    depth.
    """
    found: list[Repo] = []
    queue = deque([(base, 0)])
    while queue and len(found) < limit:
        path, level = queue.popleft()
        for entry in _subdirs(path):
            if os.path.exists(os.path.join(entry.path, '.git')):
                # линкованный worktree и submodule дают .git-файл,
                # а не папку — для просмотра изменений это репозиторий
                found.append(Repo(entry.path, os.path.relpath(entry.path, base)))
                continue    # вложенные репозитории показывает сам git
            if level + 1 < depth:
                queue.append((entry.path, level + 1))
    found.sort(key=lambda r: r.name)
    return found[:limit]


def _subdirs(path: str) -> list:
    """Подпапки, в которых имеет смысл искать проект.

    follow_symlinks=False: симлинк на родителя закольцевал бы обход.
    Артефакты отсеиваем по имени — .git внутри node_modules или
    vendor принадлежит зависимости, а не подпроекту.
    """
    try:
        entries = list(os.scandir(path))
    except OSError:
        return []
    return [e for e in entries
            if not e.name.startswith('.') and not is_noise_dir(e.name)
            and e.is_dir(follow_symlinks=False)]


def map_repos(repos: list[Repo], work: Callable[[Repo], object],
              workers: int = MAX_WORKERS) -> list[tuple[Repo, object, str]]:
    """Выполнить work на каждом репозитории: (репо, результат, ошибка).

    Порядок результатов — как в repos: дерево и лента не должны
    перетасовываться от того, кто из git'ов ответил первым. Упавший
    воркер отдаёт ошибку, а не роняет весь скан: один битый
    репозиторий не повод скрыть остальные. Ошибка в третьем поле —
    и от исключения, и от самого git: last_error() потребителю не
    видна, она принадлежит потоку воркера.

    Демон-потоки, а не ThreadPoolExecutor: его потоки обычные, и на
    выходе интерпретатор join'ит их — закрытие кита посреди сетевого
    fetch ждало бы минуту (та же причина, что у run_background).
    """
    if not repos:
        return []
    if len(repos) == 1:
        return [_run_one(repos[0], work)]
    results: list = [None] * len(repos)
    slots = threading.Semaphore(max(1, workers))

    def runner(i: int, repo: Repo) -> None:
        with slots:
            results[i] = _run_one(repo, work)

    threads = [threading.Thread(target=runner, args=(i, repo), daemon=True)
               for i, repo in enumerate(repos)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def _run_one(repo: Repo, work: Callable[[Repo], object]) -> tuple[Repo, object, str]:
    """Результат воркера и его ошибка.

    Ошибку git снимаем здесь: last_error() живёт в своём потоке, и
    вызвавший веер до неё уже не доберётся. Ловим любое исключение —
    это граница потока: без перехвата вместо кортежа осталось бы
    None, и распаковка у потребителя уронила бы кит.
    """
    try:
        return repo, work(repo), last_error()
    except Exception as e:            # noqa: BLE001
        return repo, None, f'{type(e).__name__}: {e}'


def by_repo(items: list[dict]) -> dict[str, list[dict]]:
    """Элементы по корню репозитория — для батчевых git add и
    restore.
    """
    out: dict[str, list[dict]] = {}
    for it in items:
        out.setdefault(it.get('repo') or '', []).append(it)
    return out
