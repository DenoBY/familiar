"""Бэкенд режима Find in Files: git grep и разбор его вывода в
item'ы дерева.

git grep вместо собственного обходчика: учитывает .gitignore,
с --untracked видит и новые файлы, -I пропускает бинарники.
С ревизией ищем в снимке коммита — там же, что показывает дифф.
"""

from .git import git_lines, grep_scope, set_error, strip_rev


# Потолок совпадений: живой поиск по короткому запросу в большом
# репозитории не должен строить дерево на сотни тысяч строк.
MAX_MATCHES = 2000


def search_files(root: str, query: str, regex: bool = False,
                 rev: str = '') -> tuple[list[dict], bool]:
    """item'ы DiffTreeView по совпадениям запроса и флаг обрезки.

    Регистр — smart-case: запрос без заглавных ищется без учёта
    регистра. Ошибки git (включая кривой regex) — через last_error();
    «нет совпадений» ошибкой не считается.
    """
    if not query:
        return [], False
    args = ['grep', '-I', '-n', '-z', '--no-color', '-E' if regex else '-F']
    if query == query.lower():
        args.append('-i')
    # rc=1 без stderr («нет совпадений») не трогает last_error —
    # старая ошибка выглядела бы причиной пустого результата
    set_error('')
    by_path: dict[str, dict] = {}
    total, truncated = 0, False
    # Построчно, а не одним run_git: на короткий запрос git grep
    # отдаёт десятки мегабайт, и захват целиком тратил бы память и
    # время event loop на строки, которые всё равно за MAX_MATCHES.
    # с -z и путь, и номер строки завершаются NUL: path\0lineno\0text
    for line in git_lines(root, *args, '-e', query, *grep_scope(rev), '--'):
        if total >= MAX_MATCHES:
            truncated = True
            break
        path, _, rest = line.partition('\0')
        path = strip_rev(path, rev)   # с ревизией git отдаёт `<rev>:<path>`
        lineno, sep, text = rest.partition('\0')
        if not sep or not lineno.isdigit():
            continue
        it = by_path.setdefault(path, {'path': path, 'kind': 'match',
                                       'untracked': False, 'lines': []})
        it['lines'].append((int(lineno), text))
        total += 1
    items = list(by_path.values())
    for it in items:
        it['matches'] = len(it['lines'])
    return items, truncated
