"""Разбор одной сессии Claude Code (jsonl) в ленту записей.

Слой над реестром сессий (modules.session.data): тот отвечает на
«какие сессии есть и что в них», а этот читает выбранный файл и
превращает поток JSON-объектов в записи диалога — реплики, вызовы
инструментов и их вывод. Без TUI: рендер записей — в transcript.
"""

import json
import os
import re
from typing import NamedTuple

from ..text import plural
from .data import _sanitize, _user_text, user_display
from .util import ASK_REJECTED, ASK_TOOL


class Entry(NamedTuple):
    """Одна запись диалога: реплика, вызов инструмента или его вывод.

    Блоки thinking не разбираем: Claude Code пишет в jsonl только
    их подпись, текста размышлений в файле нет.
    """

    kind: str                       # user | assistant | tool | result | attach
    text: str = ''
    name: str = ''                  # имя инструмента (kind='tool' и его 'result')
    tool_input: 'dict | None' = None
    error: bool = False             # kind='result', из is_error
    patch: tuple = ()               # правка файла: (номер строки, знак, текст)
    patch_stat: tuple = ()          # (добавлено, удалено) по ВСЕМУ патчу:
                                    # patch обрезан по MAX_RESULT_LINES
    summary: str = ''               # чем заменить вывод, пока он свёрнут


# Вывод инструмента бывает в десятки мегабайт (дампы, логи).
# Держать его целиком незачем: раскрытый блок всё равно листается,
# а память жрут все записи разом.
MAX_RESULT_LINES = 200
MAX_RESULT_CHARS = 20_000


def _content_text(block: dict) -> str:
    c = block.get('content')
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return '\n'.join(x.get('text', '') for x in c
                         if isinstance(x, dict) and x.get('type') == 'text')
    return ''


def _patch_lines(patch: list) -> 'tuple[tuple, tuple[int, int]]':
    """structuredPatch (хунки Claude Code) → строки и статистика.

    Строка: (номер, знак, текст); номер берётся из нового файла,
    у удалённых строк — из старого: так же нумерует сам Claude Code.
    Строк не больше MAX_RESULT_LINES, но счётчики (добавлено, удалено)
    — по всему патчу: их показывает сводка свёрнутой правки.
    """
    rows = []
    added = removed = 0
    for hunk in patch:
        if not isinstance(hunk, dict):
            continue
        old = hunk.get('oldStart', 0)
        new = hunk.get('newStart', 0)
        for raw in hunk.get('lines', []):
            if not isinstance(raw, str) or not raw:
                continue
            sign, text = raw[0], _sanitize(raw[1:]).rstrip()
            if sign == '-':
                row = (old, '-', text)
                removed += 1
                old += 1
            elif sign == '+':
                row = (new, '+', text)
                added += 1
                new += 1
            else:
                row = (new, ' ', text)
                old += 1
                new += 1
            if len(rows) < MAX_RESULT_LINES:
                rows.append(row)
    return tuple(rows), (added, removed)


def _tokens(n: int) -> str:
    if n >= 1_000_000:
        return f'{n / 1_000_000:.1f}M'
    return f'{n / 1000:.1f}k' if n >= 1000 else str(n)


def _duration(ms: int) -> str:
    sec = round(ms / 1000)
    if sec < 60:
        return f'{sec}s'
    minutes, sec = divmod(sec, 60)
    return f'{minutes}m {sec}s' if sec else f'{minutes}m'


# Отчёт субагента: «Done (1 tool use · 25.5k tokens · 18s)» —
# сводка Claude Code.
_AGENT_TOOLS = frozenset({'Agent', 'Task'})


def _agent_summary(tur: dict) -> str:
    status = tur.get('status')
    head = 'Done' if status == 'completed' else str(status or 'done').capitalize()
    parts = []
    if isinstance(tur.get('totalToolUseCount'), int):
        parts.append(plural(tur['totalToolUseCount'], 'tool use'))
    if isinstance(tur.get('totalTokens'), int):
        parts.append(f'{_tokens(tur["totalTokens"])} tokens')
    if isinstance(tur.get('totalDurationMs'), int):
        parts.append(_duration(tur['totalDurationMs']))
    return f'{head} ({" · ".join(parts)})' if parts else head


def _result_summary(name: str, tur: 'dict | None') -> str:
    """Строка, которой Claude Code подменяет свёрнутый вывод
    («Read 402 lines»).
    """
    if not isinstance(tur, dict):
        return ''
    if name in _AGENT_TOOLS:
        return _agent_summary(tur)
    if name != 'Read':
        return ''
    info = tur.get('file')
    n = info.get('numLines') if isinstance(info, dict) else None
    if not isinstance(n, int):
        return ''
    return f'Read {plural(n, "line")}'


_TOOL_ERR_RE = re.compile(r'</?tool_use_error>')


def _result_text(block: dict) -> str:
    raw = _TOOL_ERR_RE.sub('', _content_text(block)[:MAX_RESULT_CHARS])
    lines = _sanitize(raw).split('\n')
    del lines[MAX_RESULT_LINES:]
    return '\n'.join(ln.rstrip() for ln in lines).strip('\n')


def _answers_text(tur: object) -> str:
    """Ответы на AskUserQuestion: «· вопрос → ответ».

    Сам tool_result — простыня с пересказом вопроса и превью
    выбранного варианта; читателю нужен только выбор.
    """
    answers = tur.get('answers') if isinstance(tur, dict) else None
    if not isinstance(answers, dict):
        return ''
    return '\n'.join(f'· {_sanitize(q).strip()} → {_sanitize(str(a)).strip()}'
                     for q, a in answers.items())


def _is_rejected(block: dict, tur: object) -> bool:
    """Отказ отвечать (Esc), а не «ответы не разобрались»: неизвестный
    формат toolUseResult не должен выдавать ответ за отказ.
    """
    return bool(block.get('is_error')) or (
        isinstance(tur, str) and tur.startswith('User rejected'))


# Служебная запись (isMeta) вида «[Image: source: …/12.png]» — так
# Claude Code протоколирует вложение предыдущей реплики; её номер —
# имя файла.
_IMAGE_META_RE = re.compile(r'\[Image: source: (.+?)\]')


def _meta_attachments(text: str) -> list[Entry]:
    """isMeta-запись → вложения реплики; всё прочее (caveat) — шум."""
    return [Entry('attach', f'[Image #{os.path.splitext(os.path.basename(m))[0]}]')
            for m in _IMAGE_META_RE.findall(text)]


def _active_chain(objs: list) -> set:
    """uuid записей на пути от последнего листа к корню.

    Файл сессии — дерево, а не лог: отменённый (Esc) или
    отредактированный промпт остаётся веткой-тупиком. Claude Code
    показывает только актуальную ветку — от последнего листа вверх
    по parentUuid.
    """
    parent = {}
    leaf = None
    for o in objs:
        uid = o.get('uuid')
        if not uid:
            continue
        parent[uid] = o.get('parentUuid')
        leaf = uid
    chain = set()
    while leaf and leaf not in chain:
        chain.add(leaf)
        leaf = parent.get(leaf)
    return chain


def _read_objs(path: str) -> list:
    objs = []
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    objs.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return objs


def load_conversation(path: str) -> list[Entry]:
    """Записи актуальной ветки диалога в порядке появления в файле.

    Исключение — вывод инструмента: он встаёт сразу за своим
    вызовом, а не по порядку файла (при параллельных вызовах
    результаты приходят пачкой после всех tool_use и по соседству
    легли бы под чужие заголовки).
    """
    entries = []
    calls = {}   # tool_use_id → (имя, input, позиция вызова в entries)
    objs = _read_objs(path)
    chain = _active_chain(objs)
    for o in objs:
        t = o.get('type')
        if t not in ('user', 'assistant'):
            continue
        if chain and o.get('uuid') not in chain:
            continue
        c = o.get('message', {}).get('content')
        if o.get('isMeta'):
            entries += _meta_attachments(_user_text(o))
            continue
        if isinstance(c, str):
            txt = _entry_text(t, c)
            if txt:
                entries.append(Entry(t, txt))
        elif isinstance(c, list):
            tur = o.get('toolUseResult')
            for b in c:
                if isinstance(b, dict):
                    _append_block(entries, t, b, calls, tur)
    return entries


def _entry_text(kind: str, raw: str) -> str:
    txt = _sanitize(raw).strip()
    return user_display(txt) if kind == 'user' else txt


def _append_block(entries: list, kind: str, block: dict, calls: dict,
                  tur: 'dict | None' = None) -> None:
    bt = block.get('type')
    if bt == 'text':
        txt = _entry_text(kind, block.get('text', ''))
        if txt:
            entries.append(Entry(kind, txt))
    elif bt == 'tool_use':
        inp = block.get('input')
        inp = inp if isinstance(inp, dict) else None
        name = block.get('name', 'tool')
        if block.get('id'):
            calls[block['id']] = (name, inp, len(entries))
        entries.append(Entry('tool', name=name, tool_input=inp))
    elif bt == 'tool_result':
        tid = block.get('tool_use_id')
        name, inp, pos = calls.pop(tid, ('', None, None)) if tid else ('', None, None)
        txt = _result_text(block)
        if name == ASK_TOOL:
            if _is_rejected(block, tur):
                name = ASK_REJECTED
                txt = ''
                if pos is not None:
                    entries[pos] = entries[pos]._replace(name=name)
            else:
                txt = _answers_text(tur) or txt
        patch, stat = (), ()
        if isinstance(tur, dict) and isinstance(tur.get('structuredPatch'), list):
            patch, stat = _patch_lines(tur['structuredPatch'])
        if txt or inp is not None:
            entry = Entry('result', txt, name=name, tool_input=inp,
                          error=bool(block.get('is_error')), patch=patch,
                          patch_stat=stat, summary=_result_summary(name, tur))
            if pos is None:
                entries.append(entry)
            else:
                entries.insert(pos + 1, entry)
                # вызовы после точки вставки сдвинулись —
                # обновить их позиции
                for k, (n, i, p) in calls.items():
                    if p > pos:
                        calls[k] = (n, i, p + 1)
