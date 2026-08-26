import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest

import kittymock  # noqa: F401
from modules.lsp.rpc import (
    LspProcess,
    RpcError,
    RpcTimeout,
    encode_frame,
    read_frame,
)


FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lspfake.py')


class DripStream:
    """Поток, отдающий по байту за раз: pipe так и делает, и read(n)
    не обязан вернуть n байт.
    """

    def __init__(self, data: bytes) -> None:
        self.buf = io.BytesIO(data)

    def readline(self) -> bytes:
        return self.buf.readline()

    def read(self, n: int) -> bytes:
        return self.buf.read(1 if n else 0)


class FrameTest(unittest.TestCase):
    def test_roundtrip(self):
        raw = encode_frame({'id': 1, 'method': 'x'})
        self.assertEqual(read_frame(io.BytesIO(raw)), {'id': 1, 'method': 'x'})

    def test_non_ascii_body_counts_bytes_not_chars(self):
        raw = encode_frame({'msg': 'привет 🙂'})
        self.assertEqual(read_frame(io.BytesIO(raw))['msg'], 'привет 🙂')

    def test_body_arriving_in_pieces(self):
        raw = encode_frame({'ok': True})
        self.assertEqual(read_frame(DripStream(raw)), {'ok': True})

    def test_bare_newline_headers_accepted(self):
        body = json.dumps({'a': 1}).encode()
        raw = b'Content-Length: %d\n\n' % len(body) + body
        self.assertEqual(read_frame(io.BytesIO(raw)), {'a': 1})

    def test_extra_headers_ignored(self):
        body = json.dumps({'a': 1}).encode()
        raw = (b'Content-Type: application/vscode-jsonrpc\r\n'
               b'Content-Length: %d\r\n\r\n' % len(body)) + body
        self.assertEqual(read_frame(io.BytesIO(raw)), {'a': 1})

    def test_eof_returns_none(self):
        self.assertIsNone(read_frame(io.BytesIO(b'')))

    def test_truncated_body_is_eof(self):
        self.assertIsNone(read_frame(io.BytesIO(b'Content-Length: 99\r\n\r\n{}')))

    def test_missing_length_raises(self):
        with self.assertRaises(RpcError):
            read_frame(io.BytesIO(b'X-Foo: 1\r\n\r\n{}'))

    def test_bad_length_raises(self):
        with self.assertRaises(RpcError):
            read_frame(io.BytesIO(b'Content-Length: many\r\n\r\n{}'))

    def test_malformed_json_raises(self):
        raw = b'Content-Length: 3\r\n\r\n{,}'
        with self.assertRaises(RpcError):
            read_frame(io.BytesIO(raw))


class ProcessTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='lsprpc_')
        self.procs = []

    def tearDown(self):
        for proc in self.procs:
            proc.stop(wait=0.05)

    def start(self, scenario: dict, **kw) -> LspProcess:
        path = os.path.join(self.dir, 'scenario.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(scenario, f)
        proc = LspProcess([sys.executable, FAKE, path], cwd=self.dir, **kw)
        proc.start()
        self.procs.append(proc)
        return proc

    def test_request_returns_result(self):
        proc = self.start({})
        result = proc.request('initialize', {}, timeout=5)
        self.assertTrue(result['capabilities']['definitionProvider'])

    def test_notifications_reach_callback(self):
        seen = []
        done = threading.Event()

        def on_notify(method, params):
            seen.append((method, params))
            if method == 'indexingEnded':
                done.set()

        proc = self.start({'ready': 'indexingEnded',
                           'progress': [['begin', 'indexing', 0]]},
                          on_notify=on_notify)
        proc.request('initialize', {}, timeout=5)
        proc.notify('initialized', {})
        self.assertTrue(done.wait(5), 'нотификация не пришла')
        self.assertIn('$/progress', [m for m, _ in seen])

    def test_notification_between_request_and_reply(self):
        # ответ не должен потеряться из-за нотификаций в том же потоке
        proc = self.start({'progress': [['report', 'x', 10]] * 5,
                           'ready': 'indexingEnded'})
        proc.request('initialize', {}, timeout=5)
        proc.notify('initialized', {})
        time.sleep(0.2)
        self.assertEqual(proc.request('workspace/symbol', {'query': 'nope'},
                                      timeout=5), [])

    def test_server_request_gets_answer(self):
        # сервер ждёт ответа на workspace/configuration; не ответим —
        # он не двинется дальше и nотификация ready не придёт
        done = threading.Event()
        answered = []

        def on_request(method, params):
            answered.append(method)
            return [{}]

        proc = self.start({'want_config': True, 'ready': 'indexingEnded'},
                          on_notify=lambda m, p: done.set() if m == 'indexingEnded' else None,
                          on_request=on_request)
        proc.request('initialize', {}, timeout=5)
        proc.notify('initialized', {})
        self.assertTrue(done.wait(5))
        self.assertIn('workspace/configuration', answered)

    def test_timeout_raises_and_keeps_process(self):
        proc = self.start({'hang_on': 'workspace/symbol'})
        proc.request('initialize', {}, timeout=5)
        with self.assertRaises(RpcTimeout):
            proc.request('workspace/symbol', {'query': 'x'}, timeout=0.2)
        self.assertTrue(proc.alive(), 'таймаут не должен убивать сервер')

    def test_death_wakes_pending_request(self):
        proc = self.start({'die_on': 'workspace/symbol'})
        proc.request('initialize', {}, timeout=5)
        with self.assertRaises(RpcError):
            proc.request('workspace/symbol', {'query': 'x'}, timeout=5)

    def test_request_after_death_raises(self):
        proc = self.start({'die_on': 'workspace/symbol'})
        proc.request('initialize', {}, timeout=5)
        with self.assertRaises(RpcError):
            proc.request('workspace/symbol', {'query': 'x'}, timeout=5)
        with self.assertRaises(RpcError):
            proc.request('initialize', {}, timeout=1)

    def test_missing_binary_raises_at_start(self):
        proc = LspProcess(['definitely-not-a-real-server-xyz'], cwd=self.dir)
        with self.assertRaises(RpcError):
            proc.start()
        self.assertFalse(proc.alive())

    def test_stop_leaves_no_orphan(self):
        proc = self.start({})
        proc.request('initialize', {}, timeout=5)
        proc.stop(wait=0.5)
        deadline = time.time() + 3
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        self.assertIsNotNone(proc.poll(), 'сервер пережил stop')

    def test_stderr_tail_reports_reason(self):
        script = os.path.join(self.dir, 'noisy.py')
        with open(script, 'w', encoding='utf-8') as f:
            f.write('import sys\nsys.stderr.write("boom\\n")\n')
        proc = LspProcess([sys.executable, script], cwd=self.dir)
        proc.start()
        self.procs.append(proc)
        with self.assertRaises(RpcError):
            proc.request('initialize', {}, timeout=5)
        self.assertIn('boom', proc.stderr_tail())


if __name__ == '__main__':
    unittest.main()
