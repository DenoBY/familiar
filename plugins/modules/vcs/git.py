"""Общие git-примитивы: запуск git и разбор его вывода.

Source-agnostic слой без зависимостей от TUI и от конкретного
плагина: обёртка `run_git` поверх subprocess, чтение содержимого
blob'ов и парсеры `git diff --name-status`/`--numstat`,
параметризованные произвольными ref'ами. Используется и review
(рабочее дерево), и log (коммиты).
"""

import subprocess


_last_error = ''


def last_error() -> str:
    """stderr последнего неудачного вызова git; иначе пусто.

    Для хендлеров: «список пуст из-за ошибки git» (index.lock,
    битый репозиторий) иначе неотличим от честного «изменений нет».
    """
    return _last_error


def set_error(msg: str) -> None:
    """Сообщить об ошибке не от git (например, os.remove) через тот
    же канал, что и сбои git.
    """
    global _last_error
    _last_error = msg


def run_git(root: str, *args: str, binary: bool = False,
            timeout: int = 8) -> 'str | bytes | None':
    global _last_error
    try:
        out = subprocess.run(['git', '-C', root, *args],
                             capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        _last_error = str(e)
        return None
    if out.returncode != 0:
        err = out.stderr.decode('utf-8', 'replace').strip()
        # тихие пробы (--verify -q) падают без stderr —
        # прежнюю ошибку не затираем
        if err:
            _last_error = err.splitlines()[0]
        return None
    _last_error = ''
    return out.stdout if binary else out.stdout.decode('utf-8', 'replace')


def git_root(cwd: str) -> 'str | None':
    out = run_git(cwd, 'rev-parse', '--show-toplevel')
    return out.strip() if out else None


def has_head(root: str) -> bool:
    return run_git(root, 'rev-parse', '--verify', '-q', 'HEAD', timeout=5) is not None


# Пустое дерево git — родитель корневого коммита (у которого нет `^`)
# и база для diff в репозитории без коммитов.
EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'


def read_text(path: str) -> str:
    # git-сторона диффа декодируется как utf-8 (run_git/git_blob) —
    # читаем диск так же, иначе при LANG=C стороны разъедутся и
    # появятся ложные изменения.
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return ''


def git_blob(root: str, ref: str, path: str) -> str:
    """Содержимое файла из git-объекта.

    ref='' → индекс (:path), иначе <ref>:path.
    """
    b = run_git(root, 'show', f'{ref}:{path}', binary=True)
    return b.decode('utf-8', 'replace') if b else ''


def classify_status(xy: str) -> str:
    if '?' in xy:
        return 'untracked'
    if 'R' in xy:
        return 'renamed'
    if 'D' in xy:
        return 'deleted'
    if 'A' in xy:
        return 'added'
    return 'modified'


def count_lines(path: str) -> int:
    try:
        with open(path, 'rb') as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def git_numstat(root: str, *args: str) -> 'dict[str, tuple[int | None, int | None]]':
    """path → (added, deleted) из `git diff --numstat -z <args>`.

    Бинарники → (None, None). -z вместо разбора фигурной записи
    "dir/{old => new}/f": у переименования пути идут отдельными
    NUL-токенами (old, new), ключом становится new — тот же путь,
    что отдаёт diff_name_status.
    """
    out = run_git(root, 'diff', '--numstat', '-z', *args)
    stats = {}
    if not out:
        return stats
    toks = out.split('\0')
    i = 0
    while i < len(toks):
        parts = toks[i].split('\t')
        if len(parts) < 3:
            i += 1
            continue
        a, d, path = parts[0], parts[1], parts[2]
        if not path:
            path = toks[i + 2] if i + 2 < len(toks) else ''
            i += 3
        else:
            i += 1
        stats[path] = (None if a == '-' else int(a), None if d == '-' else int(d))
    return stats


_NAME_STATUS = {'M': 'modified', 'A': 'added', 'D': 'deleted', 'T': 'modified'}


def diff_name_status(root: str, *args: str) -> list[dict]:
    """items из `git diff --name-status -z <args>`.

    staged / vs ветка / коммит.
    """
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
