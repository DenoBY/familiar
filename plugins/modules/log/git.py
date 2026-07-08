"""Git-слой log-кита: список коммитов и изменения внутри выбранного коммита.

Тонкие обёртки над общими примитивами из modules.vcs.git: `git log` для списка и
разбор изменений коммита (name-status + numstat + содержимое blob'ов). Без TUI.
"""

from modules.vcs.git import (
    _diff_name_status, git_blob, git_numstat, run_git,
)

# Пустое дерево git — родитель корневого коммита (у которого нет `^`).
EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'

# US (\x1f) между полями, \n между записями. %P — хеши родителей (для пометки merge),
# %D — ref-names (ветки/теги/HEAD). %ad — дата автора (абсолютная, формат ниже).
_LOG_FMT = '%H%x1f%h%x1f%an%x1f%ad%x1f%P%x1f%D%x1f%s'
_DATE_FMT = '--date=format:%d.%m.%y, %H:%M'   # 30.06.26, 22:58 — как в IDE
_LOG_FIELDS = ('sha', 'short', 'author', 'date', 'parents', 'refs', 'subject')


def parse_refs(s: str) -> list[tuple[str, str]]:
    """`%D` (с --decorate=full) → [(имя, тип)], тип ∈ head|branch|remote|tag.

    Полные пути refs/heads|remotes|tags надёжно различают локальную ветку и удалённую
    (важно для веток со слэшем, напр. feature/x — по одному слэшу их не отличить).
    """
    out = []
    for part in s.split(', '):
        part = part.strip()
        if not part:
            continue
        head = False
        if part.startswith('HEAD -> '):
            head = True
            part = part[len('HEAD -> '):]
        elif part == 'HEAD':                  # detached HEAD
            out.append(('HEAD', 'head'))
            continue
        if part.startswith('tag: '):
            ref = part[len('tag: '):]
            out.append((ref[len('refs/tags/'):] if ref.startswith('refs/tags/') else ref,
                        'tag'))
        elif part.startswith('refs/heads/'):
            out.append((part[len('refs/heads/'):], 'head' if head else 'branch'))
        elif part.startswith('refs/remotes/'):
            name = part[len('refs/remotes/'):]
            if not name.endswith('/HEAD'):    # origin/HEAD — символическая, пропускаем
                out.append((name, 'remote'))
        else:                                 # fallback без --decorate=full
            out.append((part, 'head' if head else 'branch'))
    return out


def load_commits(root: str, all_branches: bool = False,
                 limit: int = 200, skip: int = 0) -> list[dict]:
    """Список коммитов: текущая ветка (HEAD) или все ветки (all_branches → --all).

    Каждый элемент: sha/short/author/date/subject + merge (True, если родителей > 1 —
    дифф такого коммита показываем к первому родителю) + refs ([(имя, тип)] веток/тегов).
    """
    # --topo-order: потомок всегда раньше родителя — иначе граф лейнов путается,
    # если у коммитов совпадают даты (без него git log идёт по дате).
    # --decorate=full: полные пути ссылок в %D — чтобы отличать локальные ветки со
    # слэшем (feature/x) от удалённых (origin/feature/x).
    args = ['log', '--no-color', '--topo-order', '--decorate=full', _DATE_FMT,
            f'--skip={skip}', f'--max-count={limit}', f'--pretty=format:{_LOG_FMT}']
    if all_branches:
        # --all включает refs/stash (коммиты «WIP on …»/«index on …») — исключаем;
        # --exclude должен идти перед --all.
        args.insert(1, '--all')
        args.insert(1, '--exclude=refs/stash')
    out = run_git(root, *args)
    if not out:
        return []
    commits = []
    for line in out.split('\n'):
        if not line:
            continue
        parts = line.split('\x1f')
        if len(parts) != len(_LOG_FIELDS):
            continue
        c = dict(zip(_LOG_FIELDS, parts))
        c['parents'] = c['parents'].split()          # хеши родителей (для графа лейнов)
        c['merge'] = len(c['parents']) > 1
        c['refs'] = parse_refs(c.pop('refs'))
        commits.append(c)
    return commits


def fetch(root: str) -> bool:
    """git fetch --all --prune (сеть, поэтому увеличенный таймаут). True при успехе."""
    return run_git(root, 'fetch', '--all', '--prune', timeout=60) is not None


def unpushed_shas(root: str) -> set[str]:
    """SHA локальных коммитов, которых нет ни в одной remote-ветке (не запушены).

    Без настроенных удалёнок пушить некуда — возвращаем пусто, чтобы не красить всю
    историю. `--branches` покрывает все локальные ветки, `HEAD` — ещё и detached-случай.
    """
    if not run_git(root, 'remote'):
        return set()
    out = run_git(root, 'rev-list', 'HEAD', '--branches', '--not', '--remotes')
    return set(out.split()) if out else set()


def commit_detail(root: str, sha: str) -> dict:
    """Подробности коммита для панели: полное сообщение, email автора, коммитер и его
    email, список веток, содержащих коммит. Отдельные git-вызовы (тяжеловато для списка —
    зовём лениво по выбранному коммиту).
    """
    # %B многострочное → ставим первым, разделитель \x1e только между остальными полями
    raw = run_git(root, 'show', '-s', '--format=%B%x1e%ae%x1e%cn%x1e%ce', sha)
    body = a_email = committer = c_email = ''
    if raw:
        parts = raw.rstrip('\n').split('\x1e')
        if len(parts) == 4:
            body, a_email, committer, c_email = parts
    br = run_git(root, 'branch', '-a', '--contains', sha)
    branches = []
    for line in (br.splitlines() if br else []):
        name = line.strip().lstrip('* ').strip()
        if not name or '->' in name:          # пропустить символическую origin/HEAD -> …
            continue
        if name.startswith('remotes/'):
            name = name[len('remotes/'):]
        branches.append(name)
    return {'body': body.strip(), 'author_email': a_email, 'committer': committer,
            'committer_email': c_email, 'branches': branches}


def first_parent(root: str, sha: str) -> str:
    """Первый родитель коммита; для корневого (без родителя) — пустое дерево."""
    out = run_git(root, 'rev-parse', '--verify', '-q', f'{sha}^')
    return out.strip() if out else EMPTY_TREE


def commit_files(root: str, sha: str, parent: 'str | None' = None) -> list[dict]:
    """Изменённые файлы коммита (vs первый родитель) со статистикой +/− и полем 'rel'.

    parent — заранее вычисленный первый родитель (иначе считается сам): позволяет не
    дёргать rev-parse повторно для того же коммита.
    """
    if parent is None:
        parent = first_parent(root, sha)
    items = _diff_name_status(root, parent, sha)
    stats = git_numstat(root, parent, sha)
    for it in items:
        it['stat'] = stats.get(it['path'])
        it['rel'] = it['path']
        it['untracked'] = False
    items.sort(key=lambda it: it['path'])
    return items


def commit_contents(root: str, sha: str, it: dict,
                    parent: 'str | None' = None) -> tuple[str, str]:
    """(before, after) для файла коммита: содержимое у родителя и в самом коммите."""
    if parent is None:
        parent = first_parent(root, sha)
    path = it['path']
    src = it.get('orig') or path
    before = '' if it['kind'] == 'added' else git_blob(root, parent, src)
    after = '' if it['kind'] == 'deleted' else git_blob(root, sha, path)
    return before, after
