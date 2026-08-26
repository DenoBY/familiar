"""JSON-RPC поверх stdio: транспорт до language server.

Кадр — заголовок `Content-Length`, пустая строка, тело JSON (LSP 3.17).
Читает отдельный демон-поток: нотификации (`$/progress`, логи) идут
вперемешку с ответами и между запросами, и разбор их в вызывающем
потоке терял бы прогресс индексации.

Слой не знает ни про kitty, ни про vcs — его импортирует и CLI.
"""

import json
import subprocess
import threading
from collections import deque
from queue import Empty, Queue
from typing import Callable


class RpcError(Exception):
    """Сервер недоступен: не стартовал, упал или закрыл поток.

    `code` — код ошибки JSON-RPC, если отвечал сам сервер: по нему
    отличают «ещё не проинициализировался» от «не умею такой метод».
    """

    def __init__(self, message: str, code: 'int | None' = None) -> None:
        super().__init__(message)
        self.code = code


class RpcTimeout(RpcError):
    """Ответ не пришёл за отведённое время."""


# Запросы сервера, которым достаточно пустого ответа. Без ответа
# gopls и intelephense ждут его вечно, молча не индексируя.
_ACK: 'dict[str, object]' = {
    'window/workDoneProgress/create': None,
    'client/registerCapability': None,
    'client/unregisterCapability': None,
    'workspace/semanticTokens/refresh': None,
    'workspace/codeLens/refresh': None,
    'workspace/diagnostic/refresh': None,
    'workspace/inlayHint/refresh': None,
    'workspace/applyEdit': {'applied': False},
}

_METHOD_NOT_FOUND = -32601

STDERR_LINES = 20
# сколько фоновый уборщик ждёт ушедшего сервера, прежде чем добить
REAP_TIMEOUT = 5.0


def encode_frame(payload: dict) -> bytes:
    body = json.dumps(payload).encode('utf-8')
    return b'Content-Length: %d\r\n\r\n' % len(body) + body


def read_frame(stream) -> 'dict | None':
    """Следующий кадр из потока; None — сервер закрыл stdout."""
    length = _read_headers(stream)
    if length is None:
        return None
    body = _read_exactly(stream, length)
    if body is None:
        return None
    try:
        return json.loads(body)
    except ValueError as e:
        raise RpcError(f'malformed frame body: {e}') from e


def _read_headers(stream) -> 'int | None':
    length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.strip()
        if not line:                      # пустая строка — конец шапки
            if length is None:
                raise RpcError('frame without Content-Length')
            return length
        name, _, value = line.partition(b':')
        if name.strip().lower() == b'content-length':
            try:
                length = int(value)
            except ValueError:
                raise RpcError(f'bad Content-Length: {value!r}') from None


def _read_exactly(stream, n: int) -> 'bytes | None':
    # pipe отдаёт короткими кусками: read(n) не гарантирует n байт
    left, chunks = n, []
    while left > 0:
        chunk = stream.read(left)
        if not chunk:
            return None
        chunks.append(chunk)
        left -= len(chunk)
    return b''.join(chunks)


class LspProcess:
    """Процесс сервера и переписка с ним.

    `on_notify` зовётся в потоке-читателе — колбэк обязан быть
    быстрым и не трогать TUI напрямую (в ките он маршалит через
    `call_soon_threadsafe`). `on_request` отвечает на запросы сервера,
    которых нет в `_ACK`; вернул None — шлём MethodNotFound.
    """

    def __init__(self, argv: 'list[str]', cwd: str,
                 env: 'dict[str, str] | None' = None,
                 on_notify: 'Callable[[str, dict], None] | None' = None,
                 on_request: 'Callable[[str, dict], object] | None' = None) -> None:
        self.argv = list(argv)
        self.cwd = cwd
        self.env = env
        self._on_notify = on_notify
        self._on_request = on_request
        self._proc: 'subprocess.Popen | None' = None
        self._pending: 'dict[int, Queue]' = {}
        self._stderr: 'deque[str]' = deque(maxlen=STDERR_LINES)
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._id = 0
        self._dead = False
        self._error = ''

    # --- жизненный цикл ---

    def start(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self.argv, cwd=self.cwd, env=self.env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE)
        except OSError as e:
            self._dead = True
            self._error = str(e)
            raise RpcError(f'{self.argv[0]}: {e}') from e
        # start_new_session НЕ ставим (в отличие от запуска редактора):
        # сервер должен уходить вместе с китом, а не переживать его
        _spawn(self._reader)
        _spawn(self._drain_stderr)

    def alive(self) -> bool:
        return bool(self._proc and self._proc.poll() is None and not self._dead)

    def poll(self) -> 'int | None':
        """Код возврата процесса; None — ещё жив."""
        return self._proc.poll() if self._proc else None

    def stderr_tail(self) -> str:
        return '\n'.join(self._stderr).strip()

    def stop(self, wait: float = 0.15) -> None:
        """Погасить сервер, не задерживая закрытие оверлея.

        `shutdown`/`exit` по спеке, дальше `terminate` без ожидания:
        по `processId` сервер обязан уйти вместе с нами, а секунда
        вежливого прощания пользователю видна.
        """
        proc = self._proc
        if proc is None:
            return
        if self.alive():
            try:
                self.request('shutdown', {}, timeout=wait)
            except RpcError:
                pass
            try:
                self.notify('exit', {})
            except RpcError:
                pass
        self._dead = True
        # сначала terminate, потом пайпы: закрыть stdout, пока читатель
        # висит в readline(), значит ждать лока буфера — то есть ровно
        # той секунды, которой хотели избежать
        proc.terminate()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            _close(stream)
        _spawn(lambda: _reap(proc))

    # --- переписка ---

    def request(self, method: str, params: dict, timeout: float) -> object:
        with self._lock:
            if self._dead:
                raise RpcError(self._error or 'language server is not running')
            self._id += 1
            mid = self._id
            box: Queue = Queue(maxsize=1)
            self._pending[mid] = box
        self._send({'jsonrpc': '2.0', 'id': mid, 'method': method, 'params': params})
        try:
            msg = box.get(timeout=timeout)
        except Empty:
            with self._lock:
                self._pending.pop(mid, None)
            self._cancel(mid)
            raise RpcTimeout(f'{method} timed out after {timeout:g}s') from None
        if isinstance(msg, RpcError):
            raise msg
        if 'error' in msg:
            err = msg['error'] or {}
            raise RpcError(f"{method}: {err.get('message') or err.get('code')}",
                           err.get('code'))
        return msg.get('result')

    def notify(self, method: str, params: dict) -> None:
        self._send({'jsonrpc': '2.0', 'method': method, 'params': params})

    def _cancel(self, mid: int) -> None:
        try:
            self.notify('$/cancelRequest', {'id': mid})
        except RpcError:
            pass          # мёртвому серверу отменять уже нечего

    def _send(self, payload: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RpcError('language server is not running')
        data = encode_frame(payload)
        with self._wlock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (OSError, ValueError) as e:
                self._die(str(e))
                raise RpcError(f'write failed: {e}') from e

    # --- поток-читатель ---

    def _reader(self) -> None:
        stream = self._proc.stdout if self._proc else None
        if stream is None:
            return
        while True:
            try:
                msg = read_frame(stream)
            except (RpcError, OSError, ValueError) as e:
                self._die(str(e))
                return
            if msg is None:
                self._die(self.stderr_tail() or 'language server exited')
                return
            try:
                self._dispatch(msg)
            except Exception:            # noqa: BLE001
                # колбэк — чужой код (кит, CLI); его ошибка не должна
                # обрывать переписку с сервером
                continue

    def _dispatch(self, msg: dict) -> None:
        mid = msg.get('id')
        method = msg.get('method')
        if mid is not None and method is None:
            with self._lock:
                box = self._pending.pop(mid, None)
            if box is not None:
                box.put(msg)
            return
        if mid is not None:
            self._serve(mid, method or '', msg.get('params') or {})
            return
        if self._on_notify:
            self._on_notify(method or '', msg.get('params') or {})

    def _serve(self, mid: int, method: str, params: dict) -> None:
        if method in _ACK:
            self._reply(mid, _ACK[method])
            return
        result = self._on_request(method, params) if self._on_request else None
        if result is None:
            self._reply(mid, None, error={'code': _METHOD_NOT_FOUND,
                                          'message': f'{method} not supported'})
        else:
            self._reply(mid, result)

    def _reply(self, mid: int, result: object, error: 'dict | None' = None) -> None:
        payload = {'jsonrpc': '2.0', 'id': mid}
        payload['error' if error else 'result'] = error if error else result
        try:
            self._send(payload)
        except RpcError:
            pass          # некому отвечать — читатель уже всё объявил

    def _drain_stderr(self) -> None:
        stream = self._proc.stderr if self._proc else None
        if stream is None:
            return
        try:
            for line in iter(stream.readline, b''):
                self._stderr.append(line.decode('utf-8', 'replace').rstrip())
        except (OSError, ValueError):
            pass           # поток закрыли из stop() — читать больше нечего

    def _die(self, reason: str) -> None:
        with self._lock:
            if self._dead:
                return
            self._dead = True
            self._error = reason
            waiting = list(self._pending.values())
            self._pending.clear()
        err = RpcError(reason or 'language server exited')
        for box in waiting:
            box.put(err)


def _spawn(target: Callable) -> None:
    threading.Thread(target=target, daemon=True).start()


def _reap(proc) -> None:
    """Дождаться смерти сервера в фоне и добить, если не ушёл.

    Ждать в `stop()` нельзя — оверлей закрывается сейчас, — но и не
    ждать никому нельзя: непожатый процесс остаётся зомби, а сервер,
    проспавший SIGTERM, переживёт кит.
    """
    try:
        proc.wait(timeout=REAP_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=REAP_TIMEOUT)
        except subprocess.TimeoutExpired:
            pass
    except OSError:
        pass


def _close(stream) -> None:
    try:
        if stream is not None:
            stream.close()
    except OSError:
        pass
