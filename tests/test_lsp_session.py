import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

import kittymock  # noqa: F401
from modules.lsp import registry as R
from modules.lsp.session import NoServer, Session, SessionPool


FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lspfake.py')


class SessionTestBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='lspses_')
        self.repo = os.path.join(self.dir, 'repo')
        os.makedirs(self.repo)
        self._backup = {k: os.environ.get(k)
                        for k in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME')}
        os.environ['XDG_CONFIG_HOME'] = os.path.join(self.dir, 'config')
        os.environ['XDG_CACHE_HOME'] = os.path.join(self.dir, 'cache')
        R.reset_cache()
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            session.stop()
        shutil.rmtree(self.dir, ignore_errors=True)
        for key, value in self._backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        R.reset_cache()

    def wire(self, scenario: dict, extra: str = '') -> None:
        """Реестр, в котором .py обслуживает фейковый сервер."""
        path = os.path.join(self.dir, 'scenario.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(scenario, f)
        conf = R.user_path()
        os.makedirs(os.path.dirname(conf), exist_ok=True)
        with open(conf, 'w', encoding='utf-8') as f:
            f.write('server python\n'
                    '  extensions .py\n'
                    f'  command {sys.executable} {FAKE} {path}\n'
                    '  roots pyproject.toml\n'
                    '  shutdown-wait 0.1\n' + extra)
        R.reset_cache()

    def session(self, scenario: dict, extra: str = '', **kw) -> Session:
        self.wire(scenario, extra)
        spec = R.spec_for('python', self.repo)
        session = Session(spec, self.repo, **kw)
        session.start()
        self.sessions.append(session)
        return session

    def write(self, rel: str, text: str) -> str:
        path = os.path.join(self.repo, rel)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        return path


def _loc(path: str, line: int) -> dict:
    return {'uri': f'file://{path}',
            'range': {'start': {'line': line, 'character': 0}}}


class SessionTest(SessionTestBase):
    def test_handshake_reports_encoding(self):
        session = self.session({'capabilities': {'positionEncoding': 'utf-8'}})
        self.assertEqual(session.encoding, 'utf-8')

    def test_default_encoding_is_utf16(self):
        self.assertEqual(self.session({}).encoding, 'utf-16')

    def test_ready_without_any_notification(self):
        # сервер не шлёт ни indexingEnded, ни $/progress — готовность
        # обязана определиться пробным запросом (issue #1678)
        session = self.session({})
        self.assertTrue(session.wait_ready(5), 'готовность не определилась')
        self.assertEqual(session.status().state, 'ready')

    def test_ready_notification_is_honoured(self):
        session = self.session({'ready': 'indexingEnded'},
                               extra='  ready indexingEnded\n')
        self.assertTrue(session.wait_ready(5))

    def test_not_ready_while_server_says_so(self):
        session = self.session({'hang_on': 'workspace/symbol'})
        self.assertFalse(session.wait_ready(0.3))
        self.assertEqual(session.status().state, 'indexing')

    def test_definition_returns_location(self):
        target = os.path.join(self.repo, 'b.py')
        source = self.write('a.py', 'x = 1\n')
        session = self.session({'definitions': {'a.py:0:4': [_loc(target, 9)]}})
        result = session.definition(source, 1, 4)
        self.assertEqual(result[0]['range']['start']['line'], 9)

    def test_symbols_query(self):
        session = self.session({'symbols': {'shop': [_loc('/x/a.py', 1)]}})
        self.assertTrue(session.wait_ready(5))
        self.assertEqual(len(session.symbols('shop')), 1)

    def test_symbols_empty_without_capability(self):
        session = self.session({'capabilities': {'definitionProvider': True}})
        self.assertEqual(session.symbols('shop'), [])

    def test_open_doc_sends_our_text_not_disk(self):
        path = self.write('a.py', 'on disk\n')
        session = self.session({})
        session.open_doc(path, 'from snapshot\n')
        seen = [s['name'] for s in session.symbols('__opened__')]
        self.assertEqual(seen, ['from snapshot\n'])

    def test_open_doc_twice_sends_change(self):
        path = self.write('a.py', 'v1\n')
        session = self.session({})
        session.open_doc(path, 'v1\n')
        session.open_doc(path, 'v2\n')
        self.assertEqual([s['name'] for s in session.symbols('__opened__')], ['v2\n'])

    def test_missing_server_raises_with_hint(self):
        self.wire({})
        conf = R.user_path()
        with open(conf, 'w', encoding='utf-8') as f:
            f.write('server python\n  extensions .py\n'
                    '  command familiar-no-such-server\n')
        R.reset_cache()
        session = Session(R.spec_for('python', self.repo), self.repo)
        with self.assertRaises(NoServer) as ctx:
            session.start()
        self.assertIn('familiar lsp install python', str(ctx.exception))

    def test_stop_closes_documents_and_process(self):
        path = self.write('a.py', 'x\n')
        session = self.session({})
        session.open_doc(path, 'x\n')
        session.stop()
        deadline = time.time() + 3
        while session._proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        self.assertIsNotNone(session._proc.poll(), 'сервер пережил stop')

    def test_dead_server_marks_failed(self):
        session = self.session({'die_on': 'workspace/symbol'})
        session.wait_ready(3)
        self.assertIn(session.status().state, ('failed', 'ready'))


class RuntimeEnvTest(SessionTestBase):
    def test_server_gets_a_usable_path(self):
        # kitty из Dock приходит с системным PATH без homebrew, а
        # npm-серверы — скрипты с `#!/usr/bin/env node`
        os.makedirs(R.bin_dir(), exist_ok=True)
        self._path = os.environ.get('PATH')
        os.environ['PATH'] = '/usr/bin:/bin'
        try:
            session = self.session({})
            seen = session.symbols('__path__')[0]['name']
        finally:
            if self._path is None:
                os.environ.pop('PATH', None)
            else:
                os.environ['PATH'] = self._path
        self.assertIn(R.bin_dir(), seen.split(os.pathsep))

    def test_path_never_holds_an_empty_entry(self):
        # пустой элемент PATH — это «текущий каталог», а cwd сервера —
        # просматриваемый репозиторий
        self._path = os.environ.get('PATH')
        os.environ.pop('PATH', None)
        try:
            session = self.session({})
            seen = session.symbols('__path__')[0]['name']
        finally:
            if self._path is not None:
                os.environ['PATH'] = self._path
        self.assertNotIn('', seen.split(os.pathsep))


class ProgressTest(SessionTestBase):
    def test_percentage_is_reported(self):
        ticks = []
        session = self.session(
            {'progress': [['begin', 'indexing', 0], ['report', 'files', 62]],
             'hang_on': 'workspace/symbol'},
            on_progress=lambda: ticks.append(1))
        deadline = time.time() + 5
        while time.time() < deadline and session.status().percent != 62:
            time.sleep(0.05)
        self.assertEqual(session.status().percent, 62)
        self.assertTrue(ticks, 'о прогрессе никто не сообщил')

    def test_message_without_percentage(self):
        session = self.session({'progress': [['report', '3/25 files', None]],
                                'hang_on': 'workspace/symbol'})
        deadline = time.time() + 5
        while time.time() < deadline and session.status().message != '3/25 files':
            time.sleep(0.05)
        self.assertEqual(session.status().message, '3/25 files')
        self.assertEqual(session.status().percent, -1)

    def test_first_end_of_many_does_not_mean_ready(self):
        # gopls шлёт несколько работ разом («Setting up workspace»,
        # «Loading packages»); конец первой — ещё не готовность
        session = self.session({'progress': [['begin', 'setup', None, 'a'],
                                             ['begin', 'loading', None, 'b'],
                                             ['end', '', None, 'a']],
                                'hang_on': 'workspace/symbol'})
        self.assertFalse(session.wait_ready(0.3))
        self.assertEqual(session.status().state, 'indexing')

    def test_last_end_makes_it_ready(self):
        session = self.session({'progress': [['begin', 'setup', None, 'a'],
                                             ['begin', 'loading', None, 'b'],
                                             ['end', '', None, 'a'],
                                             ['end', '', None, 'b']],
                                'hang_on': 'workspace/symbol'})
        self.assertTrue(session.wait_ready(5))
        self.assertEqual(session.status().state, 'ready')

    def test_cache_size_is_measured(self):
        cache = R.cache_dir('python', self.repo)
        os.makedirs(cache, exist_ok=True)
        with open(os.path.join(cache, 'index.bin'), 'wb') as f:
            f.write(b'x' * 2048)
        session = self.session({}, extra='  initopt storagePath ${CACHE}\n')
        deadline = time.time() + 5
        while time.time() < deadline and session.status().cache == 0:
            time.sleep(0.05)
        self.assertGreaterEqual(session.status().cache, 2048)


class PoolTest(SessionTestBase):
    def test_same_file_reuses_session(self):
        self.wire({})
        pool = SessionPool(self.repo)
        self.addCleanup(pool.stop_all)
        first = pool.session_for('a.py')
        self.assertIs(pool.session_for('b.py'), first)

    def test_file_without_extension_uses_shebang(self):
        self.wire({}, extra='  shebang python3\n')
        pool = SessionPool(self.repo)
        self.addCleanup(pool.stop_all)
        self.assertEqual(pool.language('bin/tool', '#!/usr/bin/env python3'),
                         'python')

    def test_unknown_language_raises(self):
        self.wire({})
        pool = SessionPool(self.repo)
        self.addCleanup(pool.stop_all)
        with self.assertRaises(NoServer) as ctx:
            pool.session_for('notes.txt')
        self.assertIn('.txt', str(ctx.exception))

    def test_nearest_root_gives_separate_sessions(self):
        inner = os.path.join(self.repo, 'packages', 'api')
        os.makedirs(inner)
        open(os.path.join(inner, 'pyproject.toml'), 'w').close()
        self.wire({})
        pool = SessionPool(self.repo)
        self.addCleanup(pool.stop_all)
        outer = pool.session_for('a.py')
        nested = pool.session_for('packages/api/b.py')
        self.assertIsNot(outer, nested)
        self.assertEqual(nested.root, inner)

    def test_nested_root_gets_its_own_cache(self):
        # индекс у каждого сервера свой: общий storagePath два процесса
        # писали бы одновременно
        inner = os.path.join(self.repo, 'packages', 'api')
        os.makedirs(inner)
        open(os.path.join(inner, 'pyproject.toml'), 'w').close()
        self.wire({}, extra='  initopt storagePath ${CACHE}\n')
        pool = SessionPool(self.repo)
        self.addCleanup(pool.stop_all)
        outer = pool.session_for('a.py')
        nested = pool.session_for('packages/api/b.py')
        self.assertNotEqual(outer.spec.init_options['storagePath'],
                            nested.spec.init_options['storagePath'])

    def test_parallel_requests_start_one_server(self):
        # прогрев и ⌥+клик приходят из разных потоков: без замка оба
        # поднимут по серверу, и второй потеряется мимо stop_all()
        self.wire({})
        pool = SessionPool(self.repo)
        self.addCleanup(pool.stop_all)
        got = []
        threads = [threading.Thread(target=lambda: got.append(pool.session_for('a.py')))
                   for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(len(set(id(s) for s in got)), 1)
        self.assertEqual(len(pool.active()), 1)

    def test_git_mode_keeps_one_session(self):
        inner = os.path.join(self.repo, 'packages', 'api')
        os.makedirs(inner)
        open(os.path.join(inner, 'pyproject.toml'), 'w').close()
        self.wire({}, extra='  roots-mode git\n')
        pool = SessionPool(self.repo)
        self.addCleanup(pool.stop_all)
        self.assertIs(pool.session_for('packages/api/b.py'), pool.session_for('a.py'))

    def test_stop_all_kills_every_server(self):
        self.wire({})
        pool = SessionPool(self.repo)
        session = pool.session_for('a.py')
        pool.stop_all()
        deadline = time.time() + 3
        while session._proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        self.assertIsNotNone(session._proc.poll())
        self.assertEqual(pool.active(), [])


if __name__ == '__main__':
    unittest.main()
