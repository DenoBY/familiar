"""Граф веток (одна строка на коммит), как в tig/lazygit, но с горизонтальными связями.

Раскладка DAG по лейнам из хешей родителей. Два принципа для «IDE-вида»:
- основная ветка (цепочка первых родителей от HEAD) держится на лейне 0 — ствол слева,
  ветки отходят вбок (иначе крайний лейн достаётся просто самому свежему коммиту);
- каждый лейн занимает 2 колонки (узел + связь), поэтому мержи/ответвления рисуются
  горизонтальными пробегами `●─╮`, `├─╯`, а не тесными `╮╯`.

Чистая функция без TUI — цвет по индексу лейна выбирает вызывающий.
"""

NODE = '●'
VERT = '│'
HORIZ = '─'
CROSS = '┼'
OUT_R = '╮'   # ответвление вправо-вниз (merge-родитель открывает лейн правее)
OUT_L = '╭'   # ответвление влево-вниз
IN_R = '╯'    # ветка справа сходится в узел (влево-вверх)
IN_L = '╰'    # ветка слева сходится в узел (вправо-вверх)


def _free_slot(lanes: list, colors: list, allow_zero: bool) -> int:
    """Индекс свободного лейна. allow_zero=False резервирует лейн 0 под основную ветку."""
    for i in range(0 if allow_zero else 1, len(lanes)):
        if lanes[i] is None:
            return i
    if not allow_zero and not lanes:          # нужен ≥1, но лейнов нет — заводим ствол
        lanes.append(None)
        colors.append(0)
    lanes.append(None)
    colors.append(0)
    return len(lanes) - 1


def _main_chain(commits: list) -> set:
    """Множество sha основной ветки: цепочка первых родителей от коммита с HEAD (иначе
    от самого свежего). Эти коммиты держим на лейне 0.
    """
    by_sha = {c['sha']: c for c in commits}
    head = next((c for c in commits
                 if any(k == 'head' for _, k in c.get('refs', []))), None)
    if head is None:
        head = commits[0] if commits else None
    chain, cur = set(), head
    while cur is not None and cur['sha'] not in chain:
        chain.add(cur['sha'])
        parents = cur.get('parents') or []
        cur = by_sha.get(parents[0]) if parents else None
    return chain


def build_graph(commits: list) -> list:
    """commits (по порядку git log, с полями 'sha', 'parents', 'refs') → на каждый
    коммит {'col': int, 'cells': [(glyph, color)]}. cells — уже в 2-колоночной сетке
    (узлы на чётных позициях, связи на нечётных). color — индекс лейна для палитры.
    """
    main_shas = _main_chain(commits)
    lanes = []        # ожидаемый sha на каждом лейне (None — свободен)
    colors = []       # индекс-цвет лейна
    next_color = 0
    out = []

    for c in commits:
        sha = c['sha']
        parents = c.get('parents', [])
        here = [i for i, lane in enumerate(lanes) if lane == sha]
        if here:
            col = here[0]
        else:                                   # новый тип ветки
            col = _free_slot(lanes, colors, allow_zero=sha in main_shas)
            lanes[col] = sha
            colors[col] = next_color
            next_color += 1

        before = list(lanes)
        bcolors = list(colors)

        lanes[col] = parents[0] if parents else None
        for i in here:
            if i != col:
                lanes[i] = None

        opened = []
        for p in parents[1:]:
            existing = next((i for i, lane in enumerate(lanes) if lane == p), None)
            if existing is None:
                existing = _free_slot(lanes, colors, allow_zero=p in main_shas)
                lanes[existing] = p
                colors[existing] = next_color
                next_color += 1
            opened.append(existing)

        used = {col, *here, *opened}
        used |= {i for i in range(len(before)) if before[i] is not None}
        used |= {i for i in range(len(lanes)) if lanes[i] is not None}
        width = max(used) + 1

        size = 2 * width - 1
        chars = [' '] * size
        ccol = [0] * size
        for i in range(width):
            b = before[i] if i < len(before) else None
            a = lanes[i] if i < len(lanes) else None
            if i == col:
                chars[2 * i], ccol[2 * i] = NODE, bcolors[i] if i < len(bcolors) else colors[i]
            elif b is not None or a is not None:
                chars[2 * i], ccol[2 * i] = VERT, bcolors[i] if b is not None else colors[i]

        # горизонтальные связи: сходящиеся ветки (here) и открытые merge-лейны (opened)
        targets = [(j, 'in') for j in here if j != col]
        targets += [(j, 'out') for j in opened
                    if (before[j] if j < len(before) else None) is None]
        for j, kind in targets:
            color = bcolors[j] if (kind == 'in' and j < len(bcolors)) else colors[j]
            lo, hi = (col, j) if col < j else (j, col)
            for k in range(2 * lo + 1, 2 * hi):          # пробег между узлами
                if chars[k] == VERT:
                    chars[k] = CROSS
                elif chars[k] == ' ':
                    chars[k], ccol[k] = HORIZ, color
            if j > col:
                corner = IN_R if kind == 'in' else OUT_R
            else:
                corner = IN_L if kind == 'in' else OUT_L
            chars[2 * j], ccol[2 * j] = corner, color

        out.append({'col': col, 'cells': list(zip(chars, ccol))})

        while lanes and lanes[-1] is None:
            lanes.pop()
            colors.pop()
    return out
