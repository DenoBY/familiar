"""Сессия с языковым сервером: старт, готовность, запросы, остановка.

Сервер живёт ровно столько, сколько открыт оверлей: между запусками
в фоне ничего не висит, а быстрый повторный старт даёт дисковый кэш
индекса самого сервера (`${CACHE}` в реестре).

Готов сервер или ещё индексирует, узнаём из его же прогресса
(`$/progress`), а если он молчит — пробным запросом. Верить одному
обещанию нельзя: intelephense умеет не прислать конец индексации
никогда (bmewburn/vscode-intelephense#1678), и ожидание висело бы вечно.
"""

import os
import threading
import time
from collections import OrderedDict
from typing import Callable, NamedTuple

from .install import install_hint, resolve, runtime_env
from .position import uri_from_path
from .registry import ServerSpec, find_root, for_path, spec_for
from .rpc import LspProcess, RpcError, RpcTimeout


INIT_TIMEOUT = 20.0
PROBE_INTERVAL = 1.0
# проба — про живость, а не про работу: ждать её столько же, сколько
# ответа по делу, значит замереть на секунды и не показывать прогресс
PROBE_TIMEOUT = 1.0
# сервер объявил прогресс, но конца так и не прислал: ждать вечно
# нельзя (bmewburn/vscode-intelephense#1678), поэтому после этого
# срока верим пробе, а не обещанию
STUCK_GRACE = 300.0
PROBE_QUERY = 'familiarProbe'
MAX_SESSIONS = 4

# «сервер ещё не готов отвечать» из спеки JSON-RPC для LSP
SERVER_NOT_INITIALIZED = -32002


class NoServer(Exception):
    """Для языка нет настроенного или установленного сервера."""


class Progress(NamedTuple):
    state: str          # 'starting' | 'indexing' | 'ready' | 'failed'
    percent: int        # -1 — сервер процентов не сообщает
    message: str
    elapsed: float
    cache: int          # байт в кэше индекса; растёт по ходу работы


_CLIENT_CAPS = {
    'general': {'positionEncodings': ['utf-16']},
    'window': {'workDoneProgress': True},
    'workspace': {'configuration': True, 'workspaceFolders': True,
                  'symbol': {'symbolKind': {}}},
    'textDocument': {
        'synchronization': {'didSave': False},
        # linkSupport не просим: Location проще, а LocationLink всё
        # равно разбираем — часть серверов шлёт его без спроса
        'definition': {'linkSupport': False},
    },
}


class Session:
    def __init__(self, spec: ServerSpec, root: str,
                 on_progress: 'Callable[[], None] | None' = None) -> None:
        self.spec = spec
        self.root = root
        self.encoding = 'utf-16'
        self._on_progress = on_progress
        self._proc: 'LspProcess | None' = None
        self._caps: dict = {}
        self._docs: 'dict[str, tuple[int, str]]' = {}
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._started = 0.0
        self._state = 'starting'
        self._percent = -1
        self._message = ''
        self._cache = 0
        self._indexing = False       # сервер сам сказал, что занят
        self._begun: 'set[str]' = set()   # незакрытые работы $/progress
        self._lock = threading.Lock()

    # --- старт ---

    def start(self) -> None:
        argv = resolve(self.spec)
        if argv is None:
            self._state = 'failed'
            raise NoServer(install_hint(self.spec))
        self._started = time.monotonic()
        self._proc = LspProcess(argv, cwd=self.root,
                                env=runtime_env(self.spec.env),
                                on_notify=self._on_notify,
                                on_request=self._on_request)
        self._proc.start()
        result = self._proc.request('initialize', self._init_params(),
                                    timeout=INIT_TIMEOUT)
        self._caps = (result or {}).get('capabilities') or {}
        self.encoding = self._caps.get('positionEncoding') or 'utf-16'
        self._proc.notify('initialized', {})
        if self.spec.settings:
            self._proc.notify('workspace/didChangeConfiguration',
                              {'settings': self.spec.settings})
        self._state = 'indexing'
        threading.Thread(target=self._watch, daemon=True).start()

    def _init_params(self) -> dict:
        uri = uri_from_path(self.root)
        return {
            # processId обязателен: по нему сервер обязан уйти вместе
            # с нами, даже если kitty убьёт кит без finalize
            'processId': os.getpid(),
            'clientInfo': {'name': 'familiar'},
            'rootUri': uri,
            'rootPath': self.root,
            'workspaceFolders': [{'uri': uri,
                                  'name': os.path.basename(self.root) or 'repo'}],
            'initializationOptions': self.spec.init_options or None,
            'capabilities': _CLIENT_CAPS,
        }

    # --- состояние ---

    def status(self) -> Progress:
        with self._lock:
            return Progress(self._state, self._percent, self._message,
                            time.monotonic() - self._started if self._started else 0.0,
                            self._cache)

    def ready(self) -> bool:
        return self._ready.is_set()

    def alive(self) -> bool:
        return bool(self._proc and self._proc.alive())

    def wait_ready(self, timeout: float) -> bool:
        return self._ready.wait(timeout)

    # --- запросы ---

    def definition(self, path: str, line: int, character: int) -> object:
        params = {'textDocument': {'uri': uri_from_path(path)},
                  'position': {'line': line - 1, 'character': character}}
        return self._request('textDocument/definition', params)

    def symbols(self, query: str) -> 'list[dict]':
        if not self._caps.get('workspaceSymbolProvider'):
            return []
        result = self._request('workspace/symbol', {'query': query})
        return [x for x in result or [] if isinstance(x, dict)]

    def open_doc(self, path: str, text: str) -> None:
        """Синхронизировать содержимое файла с сервером.

        Клиент — источник истины для открытых документов, поэтому
        сервер увидит именно то, что показано в диффе.
        """
        uri = uri_from_path(path)
        known = self._docs.get(uri)
        if known is None:
            self._docs[uri] = (1, text)
            self._notify('textDocument/didOpen', {'textDocument': {
                'uri': uri, 'languageId': self.spec.language_id,
                'version': 1, 'text': text}})
        elif known[1] != text:
            version = known[0] + 1
            self._docs[uri] = (version, text)
            self._notify('textDocument/didChange', {
                'textDocument': {'uri': uri, 'version': version},
                'contentChanges': [{'text': text}]})

    def _request(self, method: str, params: dict) -> object:
        if self._proc is None:
            raise RpcError('language server is not running')
        return self._proc.request(method, params, timeout=self.spec.timeout)

    def _notify(self, method: str, params: dict) -> None:
        if self._proc is not None:
            self._proc.notify(method, params)

    # --- нотификации сервера ---

    def _on_notify(self, method: str, params: dict) -> None:
        if method == '$/progress':
            self._progress(params.get('value') or {}, params.get('token'))
        elif method and method == self.spec.busy:
            self._set(state='indexing')
        elif method and method == self.spec.ready:
            self._set(state='ready')
            self._ready.set()
        else:
            return
        self._announce()

    def _progress(self, value: dict, token: object = None) -> None:
        kind = value.get('kind')
        key = repr(token)    # спека обещает str|int, но верить нечему
        if kind == 'begin':
            with self._lock:
                self._begun.add(key)
            self._set(indexing=True)
        elif kind == 'end':
            # конец работы — единственный надёжный признак готовности
            # у серверов, которые шлют прогресс (intelephense, gopls).
            # Работ бывает несколько (gopls: «Setting up workspace»,
            # «Loading packages»), и готовность — за последней из них,
            # иначе спросим определение у ещё грузящегося сервера
            with self._lock:
                self._begun.discard(key)
                left = bool(self._begun)
            if left:
                self._set(percent=-1, message='')
                return
            self._set(indexing=False, percent=-1, message='', state='ready')
            self._ready.set()
            return
        percent = value.get('percentage')
        self._set(state='indexing',
                  percent=int(percent) if isinstance(percent, (int, float)) else -1,
                  message=str(value.get('message') or ''))

    def _on_request(self, method: str, params: dict) -> object:
        if method == 'workspace/configuration':
            items = params.get('items') or [{}]
            return [self._setting(item.get('section') or '') for item in items]
        if method == 'workspace/workspaceFolders':
            return [{'uri': uri_from_path(self.root),
                     'name': os.path.basename(self.root) or 'repo'}]
        return None

    def _setting(self, section: str) -> object:
        node: object = self.spec.settings
        for key in filter(None, section.split('.')):
            if not isinstance(node, dict):
                return {}
            node = node.get(key, {})
        return node

    # --- фоновый надзор: готовность и прогресс ---

    def _watch(self) -> None:
        while not self._stopped.is_set():
            # пауза до первой пробы, а не после: серверу нужно время
            # объявить, что взялся за индексацию, иначе спросим раньше
            # и примем вежливый пустой ответ за готовность
            self._stopped.wait(PROBE_INTERVAL)
            if not self._ready.is_set():
                if not self.alive():
                    self._set(state='failed')
                    self._ready.set()          # ждать больше нечего
                    self._announce()
                    return
                if self._probe():
                    self._set(state='ready')
                    self._ready.set()
            self._measure_cache()
            self._announce()

    def _probe(self) -> bool:
        """Отвечает ли сервер на запросы.

        Пока он сам говорит, что индексирует, спрашивать бесполезно:
        intelephense отвечает на запросы задолго до конца работы, и
        ответы эти неполные. Но если конца нет слишком долго — верим
        пробе, иначе рискуем не дождаться никогда.
        """
        if self._indexing and time.monotonic() - self._started < STUCK_GRACE:
            return False
        if not self._caps.get('workspaceSymbolProvider') or self._proc is None:
            return True
        try:
            self._proc.request('workspace/symbol', {'query': PROBE_QUERY},
                               timeout=PROBE_TIMEOUT)
        except RpcTimeout:
            return False        # молчит — значит ещё занят собой
        except RpcError as e:
            # «не готов» — только этот код; на любой другой ответ
            # сервер уже разговаривает, значит работает
            return e.code != SERVER_NOT_INITIALIZED
        return True

    def _measure_cache(self) -> None:
        path = self.spec.init_options.get('storagePath')
        if not isinstance(path, str) or not path:
            return
        self._set(cache=_dir_size(path))

    def _set(self, **fields: object) -> None:
        with self._lock:
            for name, value in fields.items():
                setattr(self, f'_{name}', value)

    def _announce(self) -> None:
        if self._on_progress:
            self._on_progress()

    # --- остановка ---

    def stop(self) -> None:
        """Погасить сервер, не задерживая закрытие оверлея."""
        self._stopped.set()
        if self._proc is None:
            return
        for uri in list(self._docs):
            try:
                self._proc.notify('textDocument/didClose',
                                  {'textDocument': {'uri': uri}})
            except RpcError:
                break        # сервера уже нет — закрывать нечего
        self._docs.clear()
        self._proc.stop(wait=self.spec.shutdown_wait)


def _dir_size(path: str) -> int:
    total = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    total += _dir_size(entry.path)
                else:
                    total += entry.stat(follow_symlinks=False).st_size
    except OSError:
        return 0
    return total


class SessionPool:
    """Сессии по паре (язык, корень проекта).

    Ключ такой же, как в Neovim (`name`, `root_dir`): файлы одного
    проекта делят сервер, а монорепозиторий с двумя composer.json
    получает по серверу на подпроект — если реестр не сказал
    `roots-mode git`.
    """

    def __init__(self, git_root: str,
                 on_progress: 'Callable[[], None] | None' = None) -> None:
        self.git_root = git_root
        self._on_progress = on_progress
        self._live: 'OrderedDict[tuple[str, str], Session]' = OrderedDict()
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()

    def language(self, rel: str, first_line: str = '') -> 'str | None':
        return for_path(rel, self.git_root, first_line)

    def session_for(self, rel: str, first_line: str = '') -> Session:
        """Сессия для файла; поднимает сервер, если его ещё нет.

        `first_line` нужен файлам без расширения — язык у них виден
        только по shebang. NoServer — язык не в реестре или сервер не
        установлен.
        """
        lang = self.language(rel, first_line)
        if lang is None:
            raise NoServer(f'no language server configured for {_ext(rel)}')
        probe = spec_for(lang, self.git_root, self.git_root)
        if probe is None:
            raise NoServer(f'no language server configured for {_ext(rel)}')
        root = find_root(os.path.join(self.git_root, rel), probe, self.git_root)
        # spec перечитываем на найденном корне: ${CACHE} и ${ROOT} в нём
        # уже раскрыты, и с git-корнем два подпроекта монорепо делили бы
        # один storagePath на два процесса
        spec = probe if root == self.git_root else spec_for(lang, root, self.git_root)
        if spec is None:
            raise NoServer(f'no language server configured for {_ext(rel)}')
        return self._session(lang, root, spec)

    def _session(self, lang: str, root: str, spec: ServerSpec) -> Session:
        key = (lang, root)
        # старт под отдельным замком: прогрев и ⌥+клик приходят из
        # разных потоков, и без него оба поднимут по серверу на один
        # ключ — второй затрёт первый в _live, а тот останется жить
        # мимо stop_all() и писать в тот же индекс
        with self._start_lock:
            with self._lock:
                live = self._live.get(key)
                if live is not None and live.alive():
                    self._live.move_to_end(key)
                    return live
                if live is not None:
                    self._live.pop(key, None)
            session = Session(spec, root, self._on_progress)
            session.start()
            with self._lock:
                self._live[key] = session
                evicted = []
                while len(self._live) > MAX_SESSIONS:
                    _, oldest = self._live.popitem(last=False)
                    evicted.append(oldest)
        for oldest in evicted:
            oldest.stop()
        return session

    def active(self) -> 'list[Session]':
        with self._lock:
            return list(self._live.values())

    def stop_all(self) -> None:
        with self._lock:
            live = list(self._live.values())
            self._live.clear()
        for session in live:
            session.stop()


def _ext(rel: str) -> str:
    return os.path.splitext(rel)[1] or os.path.basename(rel)


def warm_up(root: str, lang: str, report: 'Callable[[Progress], None]',
            timeout: float = 900.0) -> Progress:
    """Прогнать индексацию заранее и рассказывать, как она идёт.

    Нужна `familiar lsp warm`: первая индексация большого проекта
    занимает минуты, и лучше потратить их до того, как понадобится
    прыжок, чем во время него.
    """
    spec = spec_for(lang, root, root)
    if spec is None:
        raise NoServer(f'no language server configured for {lang}')
    session = Session(spec, root)
    try:
        # сервер, который поднялся, но промолчал на initialize, иначе
        # остался бы жить без единой ссылки на него
        session.start()
        deadline = time.monotonic() + timeout
        while not session.ready() and time.monotonic() < deadline:
            report(session.status())
            session.wait_ready(0.2)
        report(session.status())
        return session.status()
    finally:
        session.stop()
