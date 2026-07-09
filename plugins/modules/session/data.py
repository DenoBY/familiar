"""Данные session-кита: проекты, сессии и живые процессы из ~/.claude.

Читает каталог ~/.claude/projects (файлы сессий *.jsonl) и реестр
живых процессов ~/.claude/sessions/<pid>.json, разбирая их в
структуры для показа. Без зависимостей от TUI.
"""

import glob
import json
import os
import re
from typing import NamedTuple

from ..text import plural
from .util import ASK_REJECTED, ASK_TOOL


# Хранилище переносится переменной CLAUDE_CONFIG_DIR (docs:
# env-vars); иначе ~/.claude.
CONFIG_DIR = os.environ.get('CLAUDE_CONFIG_DIR') or os.path.expanduser('~/.claude')
PROJECTS_DIR = os.path.join(CONFIG_DIR, 'projects')
SESSIONS_DIR = os.path.join(CONFIG_DIR, 'sessions')

# ANSI-escape (CSI/OSC/прочие) + управляющие байты. В JSONL они
# лежат как  и при json.loads становятся настоящими ESC — если
# печатать их в превью как есть, терминал исполняет
# очистку экрана/alt-screen/скрытие курсора и рендер ломается.
_ANSI_RE = re.compile(
    r'\x1b\[[0-?]*[ -/]*[@-~]'              # CSI: \x1b[…m, \x1b[2J, \x1b[?25l и т.п.
    r'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)'   # OSC
    r'|\x1b[@-Z\\-_0-9#()*+./=>]'           # прочие escape (charset, save/restore 7/8)
)
_CTRL_RE = re.compile('[\x00-\x08\x0b-\x1f\x7f]')   # управляющие, кроме \t и \n


def _sanitize(s: str) -> str:
    """Убрать ANSI-escape и управляющие символы из текста сессии
    (сырой вывод TUI).

    Табы раскрываются в пробелы: терминал раздувает \\t до 8 колонок, а
    truncate/wrap считают символы — строка с табом вылезала бы за экран.
    """
    return _CTRL_RE.sub('', _ANSI_RE.sub('', s)).expandtabs()

# Метки статуса живой сессии (из реестра ~/.claude/sessions/<pid>.json)
STATUS_LABEL = {
    'busy': 'busy',
    'idle': 'idle',
    'waiting': 'waiting',
}
STATUS_COLOR = {
    'busy': 'green',
    'idle': 'cyan',
    'waiting': 'yellow',
}


def encode_path(path: str) -> str:
    """Путь проекта → имя папки в ~/.claude/projects (/ и . → -)."""
    return path.replace('/', '-').replace('.', '-')


def decode_dir_name(name: str) -> str:
    """Грубый фолбэк: имя папки → путь (лоссово, если нет cwd)."""
    return '/' + name.lstrip('-').replace('-', '/')


def _probe_session(path: str, max_lines: int = 50) -> 'tuple[str | None, str | None]':
    """Дёшево достать (cwd, entrypoint) из начала файла, не
    парся его целиком.
    """
    cwd = ep = None
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            for i, line in enumerate(fh):
                if i > max_lines:
                    break
                if '"entrypoint"' not in line and '"cwd"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if cwd is None and o.get('cwd'):
                    cwd = o['cwd']
                if ep is None and o.get('entrypoint'):
                    ep = o['entrypoint']
                if cwd and ep:
                    break
    except OSError:
        pass
    return cwd, ep


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # процесс есть, просто не наш
    except OSError:
        return False
    return True


def running_sessions() -> 'dict[str, dict]':
    """Реально запущенные сессии: {sessionId: {status, cwd, ...}}.

    Источник — реестр ~/.claude/sessions/<pid>.json, который Claude
    Code ведёт для каждого живого процесса; протухшие записи
    отфильтрованы по живости pid.
    """
    result = {}
    try:
        files = glob.glob(os.path.join(SESSIONS_DIR, '*.json'))
    except OSError:
        return result
    for f in files:
        try:
            with open(f, encoding='utf-8') as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        pid = data.get('pid')
        sid = data.get('sessionId')
        if not pid or not sid or not _pid_alive(pid):
            continue
        result[sid] = {
            'status': data.get('status'),
            'waitingFor': data.get('waitingFor'),
            'cwd': data.get('cwd'),
            'name': data.get('name'),
            'kind': data.get('kind'),
            'pid': pid,
        }
    return result


def scan_projects() -> list[dict]:
    """Сырой список проектов с пробами сессий (file, entrypoint, mtime).

    Фильтрация по entrypoint делается позже (в handler'е), чтобы
    переключать без повторного скана. Внутренние папки Claude
    (~/.claude/...) отсеиваются.
    """
    projects = []
    try:
        names = os.listdir(PROJECTS_DIR)
    except OSError:
        return projects

    claude_dir = CONFIG_DIR
    for name in names:
        d = os.path.join(PROJECTS_DIR, name)
        if not os.path.isdir(d):
            continue
        files = glob.glob(os.path.join(d, '*.jsonl'))
        if not files:
            continue

        probes = []
        path = None
        for f in files:
            try:
                mtime = os.path.getmtime(f)
            except OSError:
                continue
            cwd, ep = _probe_session(f)
            if path is None and cwd:
                path = cwd
            probes.append({'file': f, 'entrypoint': ep, 'mtime': mtime})

        if not probes:
            continue
        if path is None:
            path = decode_dir_name(name)
        # сравнение с разделителем: соседний ~/.claude-backup — не
        # внутренняя папка
        if path == claude_dir or path.startswith(claude_dir + os.sep):
            continue

        projects.append({
            'dir': d,
            'dir_name': name,
            'path': path,
            'name': os.path.basename(path.rstrip('/')) or path,
            'probes': probes,
        })

    return projects


def is_interactive(entrypoint: 'str | None') -> bool:
    """cli или старые сессии без поля — интерактивные; sdk-cli и
    прочее — нет.
    """
    return entrypoint in (None, 'cli')


def build_projects(all_projects: list, running_ids: set, show_all: bool) -> list:
    """Видимый список проектов из сырого скана: фильтр по entrypoint,
    агрегаты (count/mtime/active) и признак текущего каталога;
    сортировка по свежести.
    """
    cwd = os.path.realpath(os.getcwd())
    enc = encode_path(cwd)
    res = []
    for p in all_projects:
        if show_all:
            probes = p['probes']
        else:
            probes = [pr for pr in p['probes'] if is_interactive(pr['entrypoint'])]
        if not probes:
            continue
        files = [pr['file'] for pr in probes]
        ids = {os.path.splitext(os.path.basename(f))[0] for f in files}
        res.append({
            'dir': p['dir'],
            'dir_name': p['dir_name'],
            'path': p['path'],
            'name': p['name'],
            'files': files,
            'count': len(files),
            'mtime': max(pr['mtime'] for pr in probes),
            'active': len(ids & running_ids),
            # текущий проект: совпало закодированное имя папки ЛИБО
            # реальный путь проекта (надёжнее — не зависит от
            # кодировки спецсимволов).
            'is_current': (p['dir_name'] == enc
                           or os.path.realpath(p['path'].rstrip('/')) == cwd),
        })
    res.sort(key=lambda x: x['mtime'], reverse=True)
    return res


def _user_text(record):
    """Достать текст из user-записи (content — строка или список
    блоков).
    """
    msg = record.get('message', {})
    content = msg.get('content')
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'text':
                parts.append(block.get('text', ''))
        return ' '.join(parts).strip()
    return ''


# Служебные блоки в user-записи: caveat/stdout — чистый шум,
# command-message дублирует имя команды, task-notification — отчёт
# фоновой задачи (килобайты JSON, которые пользователь не писал).
# Выкидываем целиком.
_DROP_BLOCK_RE = re.compile(
    r'<(local-command-caveat|local-command-stdout|command-message'
    r'|task-notification)>.*?</\1>', re.S)
_CMD_NAME_RE = re.compile(r'<command-name>\s*(.*?)\s*</command-name>', re.S)
_CMD_ARGS_RE = re.compile(r'<command-args>\s*(.*?)\s*</command-args>', re.S)
_KNOWN_TAG_RE = re.compile(
    r'</?(?:command-name|command-args|command-contents|system-reminder)>')
# Напоминания системы приходят внутри user-записи, но пользователь
# их не писал.
_REMINDER_RE = re.compile(r'<system-reminder>.*?</system-reminder>', re.S)


def user_display(text: str) -> str:
    """Реплика пользователя без служебных обёрток: у слэш-команд —
    `/cmd args`.
    Возвращает '' для чисто шумовых сообщений (caveat, system-reminder).
    """
    text = _REMINDER_RE.sub('', _DROP_BLOCK_RE.sub('', text)).strip()
    if not text:
        return ''
    name = _CMD_NAME_RE.search(text)
    if name:
        args = _CMD_ARGS_RE.search(text)
        arg = args.group(1).strip() if args else ''
        return f'{name.group(1).strip()} {arg}'.strip()
    # тег замещаем пробелом, не пустотой: «слово<тег>слово»
    # не должно склеиться
    return _KNOWN_TAG_RE.sub(' ', text).strip()


def _clean_first_human(text: str) -> str:
    return ' '.join(user_display(text).split())


# Записи без этих маркеров (progress, snapshots и т.п.) метаданных
# не несут — их можно пропустить без json.loads. Подстроки с
# кавычками устойчивы к пробелам после двоеточия и не зависят от
# порядка ключей.
_META_MARKERS = ('"user"', '"assistant"', '"custom-title"', '"ai-title"',
                 '"gitBranch"', '"cwd"')


def load_session_meta(path: str) -> dict:
    """Разобрать файл сессии: заголовок, число сообщений, cwd."""
    custom_title = None   # из /rename (запись custom-title) — высший приоритет
    ai_title = None
    first_human = None
    cwd = None
    branch = None
    msg_count = 0
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if not any(m in line for m in _META_MARKERS):
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get('gitBranch'):
                    branch = o['gitBranch']   # последняя (свежая) ветка сессии
                t = o.get('type')
                if t == 'custom-title':
                    custom_title = o.get('customTitle') or custom_title
                elif t == 'ai-title':
                    ai_title = o.get('aiTitle') or ai_title
                elif t == 'user':
                    msg_count += 1
                    if cwd is None and o.get('cwd'):
                        cwd = o['cwd']
                    if first_human is None:
                        txt = _clean_first_human(_user_text(o))
                        if txt:   # пропускаем шумовые (caveat) — берём следующее
                            first_human = txt
                elif t == 'assistant':
                    msg_count += 1
                    if cwd is None and o.get('cwd'):
                        cwd = o['cwd']
                elif cwd is None and o.get('cwd'):
                    cwd = o['cwd']
    except OSError:
        pass

    auto = ai_title or first_human or '(untitled)'
    title = custom_title or auto
    return {
        'title': ' '.join(title.split()),
        'auto_title': ' '.join(auto.split()),
        'custom': custom_title is not None,
        'msg_count': msg_count,
        'cwd': cwd,
        'branch': branch,
    }


def append_custom_title(path: str, session_id: str, name: str) -> bool:
    """Дописать в jsonl запись custom-title — как это делает
    /rename.
    """
    rec = json.dumps(
        {'type': 'custom-title', 'customTitle': name, 'sessionId': session_id},
        ensure_ascii=False,
    )
    # бинарный режим: в текстовом арифметика с tell() не определена
    # (seek принимает только непрозрачные cookie), не-ASCII хвост
    # ломал бы позицию
    try:
        with open(path, 'rb+') as f:
            f.seek(0, os.SEEK_END)
            if f.tell() > 0:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b'\n':
                    f.write(b'\n')
            f.write(rec.encode('utf-8') + b'\n')
        return True
    except OSError:
        return False


# Кэш метаданных сессий: parse jsonl-файлов (бывают десятки МБ) не
# повторяется, пока файл не изменился. Ключ инвалидируется по
# (mtime, size).
_meta_cache: 'dict[str, tuple[tuple[float, int], dict]]' = {}


def _cached_meta(path: str) -> 'dict | None':
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (st.st_mtime, st.st_size)
    hit = _meta_cache.get(path)
    if hit is not None and hit[0] == key:
        return hit[1]
    meta = load_session_meta(path)
    meta['mtime'] = st.st_mtime
    _meta_cache[path] = (key, meta)
    return meta


def load_sessions(project: dict) -> list[dict]:
    """Сессии проекта, свежие сверху (сортировка по времени)."""
    sessions = []
    for f in project['files']:
        meta = _cached_meta(f)
        if meta is None:
            continue
        mtime = meta['mtime']
        sessions.append({
            'id': os.path.splitext(os.path.basename(f))[0],
            'file': f,
            'title': meta['title'],
            'auto_title': meta['auto_title'],
            'custom': meta['custom'],
            'msg_count': meta['msg_count'],
            'cwd': meta['cwd'] or project['path'],
            'branch': meta['branch'],
            'mtime': mtime,
        })
    sessions.sort(key=lambda s: s['mtime'], reverse=True)
    return sessions


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
