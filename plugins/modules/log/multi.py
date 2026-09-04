"""Общая лента коммитов нескольких репозиториев.

У каждого репозитория своя история и своя пагинация, а показать их
надо одним списком по времени. Лента репозитория (Feed) грузится
пачками, слияние отдаёт наружу только тот префикс, который уже не
изменится от догрузки: иначе новые строки вставало бы в середину
прочитанного и курсор прыгал бы.

Чистый слой без TUI: хендлер отсюда получает готовый список коммитов.
"""

import time
from typing import NamedTuple

from ..vcs.workspace import Repo, map_repos
from .git import load_commits


# Пачка на репозиторий: общая пачка делится между ними, но не мельче
# экрана — иначе докрутка дёргала бы git на каждый шаг.
MIN_PAGE = 60


class Feed(NamedTuple):
    repo: Repo
    commits: list[dict]
    exhausted: bool     # история кончилась, догружать нечего


def page_size(n_repos: int, batch: int) -> int:
    return max(MIN_PAGE, batch // max(1, n_repos))


def load_feeds(repos: list[Repo], all_branches: bool,
               page: int) -> 'tuple[list[Feed], str]':
    """Ленты репозиториев и первая ошибка git.

    Ошибку отдаём наружу: веер идёт в своих потоках, и хендлеру
    иначе не отличить «истории нет» от «git не смог».
    """
    def work(repo: Repo) -> list[dict]:
        return load_commits(repo.root, all_branches, page)

    feeds, error = [], ''
    for repo, commits, err in map_repos(repos, work):
        feeds.append(_feed(repo, _stamp(commits or [], repo), page))
        error = error or err
    return feeds, error


def extend_feeds(feeds: list[Feed], all_branches: bool, page: int) -> list[Feed]:
    """Догрузить следующую пачку тем лентам, что ещё не кончились."""
    pending = {f.repo.root: f for f in feeds if not f.exhausted}
    if not pending:
        return feeds

    def work(repo: Repo) -> list[dict]:
        return load_commits(repo.root, all_branches, page, len(pending[repo.root].commits))

    grown = {}
    for repo, commits, _err in map_repos([f.repo for f in pending.values()], work):
        more = _stamp(commits or [], repo)
        grown[repo.root] = _feed(repo, pending[repo.root].commits + more, page,
                                 fetched=len(more))
    return [grown.get(f.repo.root, f) for f in feeds]


def merge_feeds(feeds: list[Feed]) -> 'tuple[list[dict], bool]':
    """Коммиты всех лент по времени и признак «больше нет».

    Наружу идёт префикс до самой поздней границы незавершённых лент:
    коммиты старше неё ещё могут прийти при догрузке, и показать их
    сейчас значило бы вставить строки в уже прочитанную середину.
    Отсечка держится, только пока 'ts' — тот же ключ, по которому
    git выдаёт историю пачками (дата коммита, см. _LOG_FMT).
    """
    merged = sorted((c for f in feeds for c in f.commits), key=lambda c: -c['ts'])
    borders = [f.commits[-1]['ts'] for f in feeds if not f.exhausted and f.commits]
    if not borders:
        return merged, True
    cutoff = max(borders)
    return [c for c in merged if c['ts'] >= cutoff], False


def relative_age(ts: int, now: 'float | None' = None) -> str:
    """Возраст коммита в одну колонку: 5m, 2h, 4d, 3mo, 2y."""
    delta = max(0, int((now if now is not None else time.time()) - ts))
    for limit, unit, suffix in ((3600, 60, 'm'), (86400, 3600, 'h'),
                                (2592000, 86400, 'd'), (31536000, 2592000, 'mo')):
        if delta < limit:
            return f'{delta // unit}{suffix}' if delta >= unit else 'now'
    return f'{delta // 31536000}y'


def _feed(repo: Repo, commits: list[dict], page: int,
          fetched: 'int | None' = None) -> Feed:
    got = len(commits) if fetched is None else fetched
    return Feed(repo, commits, got < page)


def _stamp(commits: list[dict], repo: Repo) -> list[dict]:
    for c in commits:
        c['repo'] = repo.root
        c['repo_name'] = repo.name
    return commits
