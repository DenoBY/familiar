"""Бэкенд режима Find in Files: git grep и разбор его вывода в
item'ы дерева.

git grep вместо собственного обходчика: учитывает .gitignore,
с --untracked видит и новые файлы, -I пропускает бинарники.
"""

from ..vcs.git import run_git, set_error


# Потолок совпадений: живой поиск по короткому запросу в большом
# репозитории не должен строить дерево на сотни тысяч строк.
MAX_MATCHES = 2000


def search_files(root: str, query: str,
                 regex: bool = False) -> tuple[list[dict], bool]:
    """item'ы DiffTreeView по совпадениям запроса и флаг обрезки.

    Регистр — smart-case: запрос без заглавных ищется без учёта
    регистра. Ошибки git (включая кривой regex) — через last_error();
    «нет совпадений» ошибкой не считается.
    """
    if not query:
        return [], False
    args = ['grep', '-I', '-n', '-z', '--untracked', '--no-color',
            '-E' if regex else '-F']
    if query == query.lower():
        args.append('-i')
    # rc=1 без stderr («нет совпадений») не трогает last_error —
    # старая ошибка выглядела бы причиной пустого результата
    set_error('')
    out = run_git(root, *args, '-e', query, '--', timeout=15)
    if not out:
        return [], False
    by_path: dict[str, dict] = {}
    total, truncated = 0, False
    # с -z и путь, и номер строки завершаются NUL: path\0lineno\0text
    for line in out.split('\n'):
        path, _, rest = line.partition('\0')
        lineno, sep, text = rest.partition('\0')
        if not sep or not lineno.isdigit():
            continue
        if total >= MAX_MATCHES:
            truncated = True
            break
        it = by_path.setdefault(path, {'path': path, 'rel': path,
                                       'kind': 'match', 'untracked': False,
                                       'lines': []})
        it['lines'].append((int(lineno), text))
        total += 1
    items = list(by_path.values())
    for it in items:
        it['stat'] = (len(it['lines']), None)
    return items, truncated
