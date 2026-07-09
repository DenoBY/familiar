"""Рендер диалога сессии в строки экрана (стиль Claude Code).

Чистые функции: записи (modules.session.data.Entry) + ширина + множество
раскрытых записей → готовые строки. Без состояния хендлера и без диска.
"""

import os
from typing import NamedTuple

from kittens.tui.operations import styled

from ..highlight import (
    ADD_BG,
    ADD_WORD_BG,
    DEL_BG,
    DEL_WORD_BG,
    render_code,
    word_ranges,
)
from .markdown import markdown_lines
from .util import pad, plural, short_path, truncate, wrap_text


class Line(NamedTuple):
    """Строка экрана превью.

    text — plain-вариант по ширине: по нему ищет поиск и он же
    печатается при подсветке совпадений. render — готовый ANSI для
    строк, где одного цвета мало. entry ≥ 0 — клик/ctrl+o по строке
    сворачивает запись с этим индексом. prompt — первая строка реплики
    пользователя (цель прыжков [ / ]).
    """

    text: str
    render: 'str | None' = None
    color: 'str | None' = None
    entry: int = -1
    prompt: bool = False


# Свёрнутый вывод инструмента показывает столько строк, дальше —
# «… +N lines» (как в Claude Code). План сворачивается позже: три
# строки рамки ничего не сообщают. Diff правки не сворачивается вовсе.
FOLD_LINES = 3
PLAN_FOLD_LINES = 10
EXPAND_HINT = ' (ctrl+o to expand)'

# Служебный текст (вывод инструментов, подсказки) — приглушённый. Именно
# 256-цвет, а не fg='gray': тот разворачивается в ANSI 37 («white») и
# светится наравне с речью.
DIM = 244

# Фон реплики пользователя: плашка на всю ширину отделяет вопрос
# от ответа (как в Claude Code). Чуть светлее типичного фона
# терминала, но темнее add/del — иначе спорит с diff'ом правки.
USER_BG = 236

# Ключ input'а, который несёт смысл вызова: `Bash(git status)`,
# `Read(~/x.py)`.
_ARG_KEY = {
    'Bash': 'command',
    'Read': 'file_path',
    'Edit': 'file_path',
    'Write': 'file_path',
    'NotebookEdit': 'notebook_path',
    'Grep': 'pattern',
    'Glob': 'pattern',
    'Task': 'description',
    'Agent': 'description',
    'WebFetch': 'url',
    'WebSearch': 'query',
    'Skill': 'skill',
}
_PATH_KEYS = frozenset({'file_path', 'notebook_path', 'path'})

# Claude Code называет правку файла Update — держим ту же терминологию.
_DISPLAY_NAME = {'Edit': 'Update', 'ExitPlanMode': 'Updated plan'}

# Вызовы, у которых аргумент в скобках не пишут: он либо огромный
# (план), либо пуст.
_NO_ARG = frozenset({'ExitPlanMode'})


def display_name(name: str) -> str:
    return _DISPLAY_NAME.get(name, name or 'tool')


def display_path(path: str, root: str = '') -> str:
    """Путь внутри проекта — относительный, чужой — через ~
    (как в Claude Code).
    """
    if root and (path == root or path.startswith(root + os.sep)):
        return os.path.relpath(path, root)
    return short_path(path)


def tool_arg(name: str, tool_input: 'dict | None', root: str = '') -> str:
    """Аргумент вызова как есть — переносы строк сохранены
    (команды bash многострочны).
    """
    inp = tool_input or {}
    key = _ARG_KEY.get(name)
    val = inp.get(key) if key else None
    if not isinstance(val, str):
        key = next((k for k, v in inp.items() if isinstance(v, str)), None)
        val = inp[key] if key else ''
    if key in _PATH_KEYS:
        val = display_path(val, root)
    return val.strip()


def tool_label(name: str, tool_input: 'dict | None', root: str = '') -> str:
    arg = ' '.join(tool_arg(name, tool_input, root).split())
    return f'{display_name(name)}({arg})'


def transcript_lines(entries: list, width: int,
                     expanded: 'set | frozenset' = frozenset(),
                     root: str = '',
                     cache: 'dict | None' = None) -> list[Line]:
    """cache {(индекс, раскрыта): [Line]} переживает вызовы: toggle_fold
    перерисовывает только изменившуюся запись, а не весь транскрипт.
    """
    width = max(20, width)
    lines: list[Line] = []
    for i, e in enumerate(entries):
        key = (i, i in expanded)
        block = cache.get(key) if cache is not None else None
        if block is None:
            block = _entry_block(e, i, width, root, i in expanded)
            if cache is not None and block is not None:
                cache[key] = block
        if block is None:
            continue
        lines += block
        nxt = _next_kind(entries, i)
        # вывод примыкает к своему вызову, вложение — к своей реплике
        if (e.kind == 'tool' and nxt == 'result') or nxt == 'attach':
            continue
        lines.append(Line(''))
    return lines


def _entry_block(e, i: int, width: int, root: str,
                 is_open: bool) -> 'list[Line] | None':
    if e.kind == 'user':
        return _user(e.text, width)
    if e.kind == 'assistant':
        return _assistant(e.text, width)
    if e.kind == 'tool':
        return _tool(e, i, width, root, is_open)
    if e.kind == 'result':
        return _result(e, i, width, is_open)
    if e.kind == 'attach':
        return [Line('  ⎿  ' + truncate(e.text, width - 5), color=DIM)]
    return None


def _next_kind(entries: list, i: int) -> str:
    return entries[i + 1].kind if i + 1 < len(entries) else ''


def _assistant(text: str, width: int) -> list[Line]:
    """Ответ Claude: markdown с подсветкой fenced-блоков,
    маркер ⏺ на первой строке.
    """
    out = []
    started = False
    for plain, render in markdown_lines(text, width - 2):
        if not plain.strip():
            out.append(Line(''))
            continue
        prefix = '  ' if started else '⏺ '
        started = True
        out.append(Line(prefix + plain,
                        render=None if render is None else prefix + render))
    return out


def _user(text: str, width: int) -> list[Line]:
    """Реплика пользователя: маркер «>» и плашка фона на всю ширину."""
    out = []
    for para in text.split('\n'):
        for wl in wrap_text(para, width - 2):
            first = not out
            prefix = '> ' if first else '  '
            render = (styled(prefix, fg=DIM, bg=USER_BG)
                      + styled(pad(wl, width - len(prefix)), bg=USER_BG))
            out.append(Line(prefix + wl, render=render, prompt=first))
    return out


def _fold(rows: list, is_open: bool, reserved: int = 0,
          limit: int = FOLD_LINES) -> tuple:
    """Свернуть список до limit строк: (показанные, скрыто, foldable).

    reserved — строки блока сверх rows (заголовок): высоту экрана
    занимают и они.
    """
    foldable = len(rows) + reserved > limit
    shown = rows if (is_open or not foldable) else rows[:limit - reserved]
    return shown, len(rows) - len(shown), foldable


def _fold_marker(hidden: int, idx: int, indent: str = '     ') -> Line:
    # entry=idx: маркер «… +N lines» — естественная цель клика
    # для раскрытия
    return Line(f'{indent}… +{hidden} lines{EXPAND_HINT}', color=DIM, entry=idx)


# Многострочную команду показываем не целиком: столько строк,
# дальше «…».
ARG_LINES = 2


def _tool(entry, idx: int, width: int, root: str = '',
          is_open: bool = False) -> list[Line]:
    name = display_name(entry.name)
    if entry.name in _NO_ARG:
        return _plan(entry, idx, name, width, is_open)

    head = f'⏺ {name}('
    rows = tool_arg(entry.name, entry.tool_input, root).split('\n')[:ARG_LINES + 1]
    cut = len(rows) > ARG_LINES
    del rows[ARG_LINES:]
    rows[-1] = rows[-1] + ('…)' if cut else ')')

    out = []
    for i, row in enumerate(rows):
        prefix = head if i == 0 else '    '
        plain = truncate(prefix + row, width)
        if i == 0:
            # рендер собираем из уже обрезанной строки: длинное
            # имя инструмента truncate мог срезать, полное вылезло
            # бы за экран
            name_part = plain[2:len(head) - 1]
            arg = plain[len(head) - 1:]
            render = (styled('⏺', fg='green') + ' ' + styled(name_part, bold=True)
                      + (styled(arg, fg=DIM) if arg else ''))
        else:
            render = styled(plain, fg=DIM)
        out.append(Line(plain, render=render))
    return out


def _plan(entry, idx: int, name: str, width: int, is_open: bool) -> list[Line]:
    """Выход из режима планирования: заголовок и сам план в рамке."""
    head = Line(f'⏺ {name}',
                render=styled('⏺', fg='green') + ' ' + styled(name, bold=True))
    text = (entry.tool_input or {}).get('plan', '')
    if not text:
        return [head]

    inner = max(10, width - 4)
    body = [(f'│ {plain}', render if render is None else f'│ {render}')
            for plain, render in markdown_lines(text, inner)]
    rule = '─' * inner
    body = [(f'┌{rule}', None), *body, (f'└{rule}', None)]

    shown, hidden, foldable = _fold(body, is_open, limit=PLAN_FOLD_LINES)
    out = [head._replace(entry=idx if foldable else -1)]
    for plain, render in shown:
        out.append(Line('  ' + plain, render=None if render is None else '  ' + render))
    if hidden:
        out.append(_fold_marker(hidden, idx, indent='  '))
    return out


_NUMW = 4
_SIGN_STYLE = {'-': ('red', DEL_BG, DEL_WORD_BG),
               '+': ('green', ADD_BG, ADD_WORD_BG),
               ' ': (DIM, None, None)}


# Строка «в основном та же»: только тогда word-diff показывает
# правку, а не красит половину строки. Порог выше общего
# WORD_DIFF_RATIO: там пары даёт SequenceMatcher по файлу, здесь мы
# подбираем их сами и вправе быть строже.
PAIR_RATIO = 0.6

# Подбор пар квадратичен по размеру блока; на больших — обратно
# к позиционному.
_MAX_PAIR_CELLS = 400


def _pair_rows(rem: list, add: list) -> list:
    """Пары ((i, j), (изменения в rem[i], изменения в add[j])).

    Каждую удалённую строку тянем к самой похожей добавленной, а не
    к соседней по счёту: в блоке правок порядок строк съезжает, и
    позиционная пара подсвечивает мусор.
    """
    scored = []
    if len(rem) * len(add) > _MAX_PAIR_CELLS:
        for k in range(min(len(rem), len(add))):
            scored.append((word_ranges(rem[k], add[k])[2], k, k))
    else:
        for i in range(len(rem)):
            for j in range(len(add)):
                scored.append((word_ranges(rem[i], add[j])[2], i, j))
    scored.sort(key=lambda t: (-t[0], abs(t[1] - t[2])))

    pairs, used_rem, used_add = [], set(), set()
    for ratio, i, j in scored:
        if ratio < PAIR_RATIO or i in used_rem or j in used_add:
            continue
        used_rem.add(i)
        used_add.add(j)
        dset, aset, _ = word_ranges(rem[i], add[j])
        pairs.append(((i, j), (dset, aset)))
    return pairs


def _patch_strong(rows: tuple) -> list:
    """Изменившиеся куски внутри спаренных строк «-» и «+» (word-diff).

    Возвращает параллельный rows список множеств индексов символов;
    None — строка без пары либо слишком непохожая на неё.
    """
    strong = [None] * len(rows)
    i = 0
    while i < len(rows):
        if rows[i][1] != '-':
            i += 1
            continue
        rem = i
        while i < len(rows) and rows[i][1] == '-':
            i += 1
        add = i
        while i < len(rows) and rows[i][1] == '+':
            i += 1
        for (ri, ai), (dset, aset) in _pair_rows(
                [r[2] for r in rows[rem:add]], [r[2] for r in rows[add:i]]):
            strong[rem + ri], strong[add + ai] = dset, aset
    return strong


def _patch_summary(entry) -> str:
    # счётчики из data (по всему патчу): сами строки обрезаны по
    # MAX_RESULT_LINES и для больших правок занижали бы сводку
    added, removed = entry.patch_stat or (
        sum(1 for _, sign, _ in entry.patch if sign == '+'),
        sum(1 for _, sign, _ in entry.patch if sign == '-'))
    parts = []
    if added or not removed:
        parts.append(f'Added {plural(added, "line")}')
    if removed:
        parts.append(('removed ' if parts else 'Removed ') + plural(removed, 'line'))
    return ', '.join(parts)


def _patch_result(entry, idx: int, width: int, is_open: bool) -> list[Line]:
    """Diff правки: серый жёлоб с номером, цветной знак, код с
    подсветкой синтаксиса и word-diff. Не сворачивается: правка —
    суть сообщения, прятать её за «… +N lines» незачем.
    """
    ext = os.path.splitext((entry.tool_input or {}).get('file_path', ''))[1]
    strong = _patch_strong(entry.patch)

    out = [Line('  ⎿  ' + _patch_summary(entry), color=DIM)]
    for i, (num, sign, text) in enumerate(entry.patch):
        color, bg, word_bg = _SIGN_STYLE[sign]
        gutter = f'     {num:>{_NUMW}} {sign} '
        code = truncate(text, max(1, width - len(gutter)))
        plain = gutter + code
        render = (styled(gutter[:-2], fg=DIM, bg=bg)
                  + styled(gutter[-2:], fg=color, bg=bg)
                  + render_code(code, ext, base_bg=bg, strong=strong[i],
                                strong_bg=word_bg))
        if bg is not None and len(plain) < width:
            render += styled(' ' * (width - len(plain)), bg=bg)
        out.append(Line(plain, render=render, color=color))
    return out


def _write_rows(tool_input: dict) -> list:
    n = len(tool_input.get('content', '').split('\n'))
    return [(f'Wrote {plural(n, "line")}', DIM)]


def _result_rows(entry) -> list:
    """Строки вывода как (текст, цвет). Правку файла Claude Code
    показывает не техническим «file updated», а сводкой и diff'ом —
    берём их из patch.
    """
    if entry.name == 'ExitPlanMode':
        return ([('Plan rejected', 'red')] if entry.error
                else [('Plan approved', DIM)])
    if not entry.error and entry.name == 'Write' and entry.tool_input:
        return _write_rows(entry.tool_input)
    color = 'red' if entry.error else DIM
    body = entry.text.split('\n')
    if entry.error:
        body[0] = 'Error: ' + body[0]
    return [(ln, color) for ln in body]


def _result(entry, idx: int, width: int, is_open: bool) -> list[Line]:
    if entry.patch and not entry.error and entry.name != 'Write':
        return _patch_result(entry, idx, width, is_open)
    # строки вывода НЕ переносим и не режем: обрезает отрисовка,
    # а копирование берёт текст строки целиком
    rows = _result_rows(entry)
    # Claude Code прячет вывод за сводкой («Read 402 lines») —
    # тело по запросу.
    if entry.summary and not entry.error:
        foldable = True
        shown, hidden = (rows, 0) if is_open else (
            [(entry.summary + EXPAND_HINT, DIM)], 0)
    else:
        shown, hidden, foldable = _fold(rows, is_open)

    out = []
    for i, (text, color) in enumerate(shown):
        line = ('  ⎿  ' if i == 0 else '     ') + text
        out.append(Line(line, color=color,
                        entry=idx if (i == 0 and foldable) else -1))
    if hidden:
        out.append(_fold_marker(hidden, idx))
    return out
