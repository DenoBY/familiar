"""Данные session-кита: проекты, сессии и их живые процессы из ~/.claude.

Читает каталог ~/.claude/projects (файлы сессий *.jsonl) и реестр живых процессов
~/.claude/sessions/<pid>.json, разбирая их в структуры для показа. Без зависимостей от TUI.
"""

import glob
import json
import os
import re


# Хранилище переносится переменной CLAUDE_CONFIG_DIR (docs: env-vars); иначе ~/.claude.
CONFIG_DIR = os.environ.get('CLAUDE_CONFIG_DIR') or os.path.expanduser('~/.claude')
PROJECTS_DIR = os.path.join(CONFIG_DIR, 'projects')
SESSIONS_DIR = os.path.join(CONFIG_DIR, 'sessions')

# ANSI-escape (CSI/OSC/прочие) + управляющие байты. В JSONL они лежат как  и при
# json.loads становятся настоящими ESC — если печатать их в превью как есть, терминал
# исполняет очистку экрана/alt-screen/скрытие курсора и рендер ломается.
_ANSI_RE = re.compile(
    r'\x1b\[[0-?]*[ -/]*[@-~]'              # CSI: \x1b[…m, \x1b[2J, \x1b[?25l и т.п.
    r'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)'   # OSC
    r'|\x1b[@-Z\\-_0-9#()*+./=>]'           # прочие escape (charset, save/restore 7/8)
)
_CTRL_RE = re.compile('[\x00-\x08\x0b-\x1f\x7f]')   # управляющие, кроме \t и \n


def _sanitize(s: str) -> str:
    """Убрать ANSI-escape и управляющие символы из текста сессии (сырой вывод TUI)."""
    return _CTRL_RE.sub('', _ANSI_RE.sub('', s))

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
    """Путь проекта → имя папки в ~/.claude/projects (замена / и . на -)."""
    return path.replace('/', '-').replace('.', '-')


def decode_dir_name(name: str) -> str:
    """Грубый фолбэк: имя папки → путь (лоссово, только если нет cwd)."""
    return '/' + name.lstrip('-').replace('-', '/')


def _probe_session(path: str, max_lines: int = 50) -> 'tuple[str | None, str | None]':
    """Дёшево достать (cwd, entrypoint) из начала файла, не парся его целиком."""
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
    """Реально запущенные сейчас сессии {sessionId: {status, cwd, name, ...}}.

    Источник — реестр ~/.claude/sessions/<pid>.json, который Claude Code ведёт для
    каждого живого процесса; протухшие записи отфильтрованы по живости pid.
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
            'pid': pid,
        }
    return result


def scan_projects() -> list[dict]:
    """Сырой список проектов с пробами сессий (file, entrypoint, mtime).

    Фильтрация по entrypoint делается позже (в handler'е), чтобы переключать без
    повторного скана. Внутренние папки Claude (~/.claude/...) отсеиваются.
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
        # сравнение с разделителем: соседний ~/.claude-backup — не внутренняя папка
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
    """cli или старые сессии без поля — интерактивные; sdk-cli и прочее — нет."""
    return entrypoint in (None, 'cli')


def build_projects(all_projects: list, running_ids: set, show_all: bool) -> list:
    """Видимый список проектов из сырого скана: фильтр по entrypoint, агрегаты
    (count/mtime/active) и признак текущего каталога; сортировка по свежести."""
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
            # текущий проект: совпало закодированное имя папки ЛИБО реальный
            # путь проекта (надёжнее — не зависит от кодировки спецсимволов).
            'is_current': (p['dir_name'] == enc
                           or os.path.realpath(p['path'].rstrip('/')) == cwd),
        })
    res.sort(key=lambda x: x['mtime'], reverse=True)
    return res


def _user_text(record):
    """Достать текст из user-записи (content может быть строкой или списком блоков)."""
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


# Служебные блоки-обёртки первого сообщения: caveat/stdout — чистый шум, а
# command-message дублирует имя команды — целиком выкидываем.
_DROP_BLOCK_RE = re.compile(
    r'<(local-command-caveat|local-command-stdout|command-message)>.*?</\1>', re.S)
_CMD_NAME_RE = re.compile(r'<command-name>\s*(.*?)\s*</command-name>', re.S)
_CMD_ARGS_RE = re.compile(r'<command-args>\s*(.*?)\s*</command-args>', re.S)
_KNOWN_TAG_RE = re.compile(
    r'</?(?:command-name|command-args|command-contents|system-reminder)>')


def _clean_first_human(text: str) -> str:
    """Осмысленный заголовок из первого сообщения: у слэш-команд — `/cmd args`,
    служебные обёртки убраны. Возвращает '' для шумовых сообщений (caveat).
    """
    text = _DROP_BLOCK_RE.sub('', text).strip()
    if not text:
        return ''
    name = _CMD_NAME_RE.search(text)
    if name:
        args = _CMD_ARGS_RE.search(text)
        arg = args.group(1).strip() if args else ''
        return f'{name.group(1).strip()} {arg}'.strip()
    return ' '.join(_KNOWN_TAG_RE.sub(' ', text).split())


# Записи без этих маркеров (progress, snapshots и т.п.) метаданных не несут —
# их можно пропустить без json.loads. Подстроки с кавычками устойчивы к пробелам
# после двоеточия и не зависят от порядка ключей.
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
    """Дописать в jsonl запись custom-title — так же, как это делает /rename."""
    rec = json.dumps(
        {'type': 'custom-title', 'customTitle': name, 'sessionId': session_id},
        ensure_ascii=False,
    )
    # бинарный режим: в текстовом арифметика с tell() не определена
    # (seek принимает только непрозрачные cookie), не-ASCII хвост ломал бы позицию
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


# Кэш метаданных сессий: parse jsonl-файлов (бывают десятки МБ) не повторяется,
# пока файл не изменился. Ключ инвалидируется по (mtime, size).
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
    """Сессии проекта, отсортированные по времени изменения (свежие сверху)."""
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


def _tool_result_text(block):
    c = block.get('content')
    if isinstance(c, str):
        s = c
    elif isinstance(c, list):
        s = ' '.join(x.get('text', '') for x in c
                     if isinstance(x, dict) and x.get('type') == 'text')
    else:
        s = ''
    return ' '.join(_sanitize(s).split())[:200]


def load_conversation(path: str) -> list:
    """Список записей диалога [(kind, text)], kind ∈ {user, assistant, tool}."""
    entries = []
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                t = o.get('type')
                if t not in ('user', 'assistant'):
                    continue
                c = o.get('message', {}).get('content')
                if isinstance(c, str):
                    txt = _sanitize(c).strip()
                    if txt:
                        entries.append((t, txt))
                elif isinstance(c, list):
                    for b in c:
                        if not isinstance(b, dict):
                            continue
                        bt = b.get('type')
                        if bt == 'text':
                            txt = _sanitize(b.get('text', '')).strip()
                            if txt:
                                entries.append((t, txt))
                        elif bt == 'tool_use':
                            entries.append(('tool', f'→ {b.get("name", "tool")}'))
                        elif bt == 'tool_result':
                            txt = _tool_result_text(b)
                            if txt:
                                entries.append(('tool', f'‹result› {txt}'))
    except OSError:
        pass
    return entries
