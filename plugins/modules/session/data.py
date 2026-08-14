"""Данные session-кита: проекты, сессии и живые процессы из ~/.claude.

Читает каталог ~/.claude/projects (файлы сессий *.jsonl) и реестр
живых процессов ~/.claude/sessions/<pid>.json, разбирая их в
структуры для показа. Без зависимостей от TUI.
"""

import glob
import json
import os
import re
import subprocess


# Хранилище переносится переменной CLAUDE_CONFIG_DIR (docs:
# env-vars); иначе ~/.claude.
CONFIG_DIR = os.environ.get('CLAUDE_CONFIG_DIR') or os.path.expanduser('~/.claude')
PROJECTS_DIR = os.path.join(CONFIG_DIR, 'projects')
SESSIONS_DIR = os.path.join(CONFIG_DIR, 'sessions')

# ANSI-escape (CSI/OSC/прочие) + управляющие байты. В JSONL они
# лежат как \u001b и при json.loads становятся настоящими ESC — если
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


# kitty кладёт в окружение кита pid процесса окна, поверх которого
# открыт оверлей (Boss.run_kitten: KITTY_CHILD_PID) — то самое окно,
# что придёт в handle_result как target_window_id.
_WINDOW_PID_VAR = 'KITTY_CHILD_PID'

# Сколько ppid-шагов проходить от процесса claude до процесса окна:
# при запуске из кита шелл делает exec (0 шагов), при ручном `claude`
# в интерактивном шелле — 1-2; запас на обёртки вроде login.
_MAX_PARENT_HOPS = 8


def parent_pids() -> dict[int, int]:
    try:
        out = subprocess.run(('ps', '-Ao', 'pid=,ppid='), capture_output=True,
                             timeout=4, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return {}
    parents = {}
    for line in out.stdout.decode('utf-8', 'replace').splitlines():
        pid, _, ppid = line.strip().partition(' ')
        ppid = ppid.strip()
        if pid.isdigit() and ppid.isdigit():
            parents[int(pid)] = int(ppid)
    return parents


def session_id_for_pid(window_pid: int, running: 'dict[str, dict]',
                       parents: 'dict[int, int] | None' = None) -> 'str | None':
    """id сессии claude, идущей в окне с процессом window_pid.

    Процесс claude — это либо сам процесс окна (шелл сделал exec),
    либо его потомок; связь ищется по дереву ppid. Готовую таблицу
    родителей можно передать: при обходе многих окон (снимок
    состояния) один вызов ps на всех дешевле, чем на каждое окно.
    """
    by_pid = {info['pid']: sid for sid, info in running.items() if info.get('pid')}
    if not by_pid:
        return None
    if window_pid in by_pid:
        return by_pid[window_pid]
    if parents is None:
        parents = parent_pids()
    for pid, sid in by_pid.items():
        cur = parents.get(pid)
        for _ in range(_MAX_PARENT_HOPS):
            if cur is None or cur <= 1:
                break
            if cur == window_pid:
                return sid
            cur = parents.get(cur)
    return None


def window_session_id(running: 'dict[str, dict]') -> 'str | None':
    """id сессии, идущей в окне, поверх которого открыт кит."""
    raw = os.environ.get(_WINDOW_PID_VAR, '')
    if not raw.isdigit():
        return None
    return session_id_for_pid(int(raw), running)


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
    """Разобрать файл сессии: заголовки (custom/auto), число
    сообщений, cwd и ветка git.
    """
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
