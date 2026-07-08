import json
import os
import shutil
import tempfile
import unittest

import kittymock  # noqa: F401
import modules.session.data as Dt


def write_jsonl(path, records):
    with open(path, 'w') as f:
        for r in records:
            f.write((r if isinstance(r, str) else json.dumps(r)) + '\n')


class TestPureHelpers(unittest.TestCase):
    def test_encode_path(self):
        self.assertEqual(Dt.encode_path('/Users/d/Proj.x'), '-Users-d-Proj-x')

    def test_decode_dir_name(self):
        self.assertEqual(Dt.decode_dir_name('-Users-d-proj'), '/Users/d/proj')

    def test_is_interactive(self):
        self.assertTrue(Dt.is_interactive(None))
        self.assertTrue(Dt.is_interactive('cli'))
        self.assertFalse(Dt.is_interactive('sdk-cli'))

    def test_user_text_variants(self):
        self.assertEqual(Dt._user_text({'message': {'content': '  hi '}}), 'hi')
        self.assertEqual(Dt._user_text(
            {'message': {'content': [{'type': 'text', 'text': 'a'},
                                     {'type': 'image'},
                                     {'type': 'text', 'text': 'b'}]}}), 'a b')
        self.assertEqual(Dt._user_text({'message': {'content': 42}}), '')

    def test_tool_result_text_truncates(self):
        long = 'x ' * 300
        r = Dt._tool_result_text({'content': long})
        self.assertLessEqual(len(r), 200)
        self.assertEqual(Dt._tool_result_text(
            {'content': [{'type': 'text', 'text': '  a  b  '}]}), 'a b')

    def test_sanitize_strips_ansi_and_control(self):
        # сырой вывод TUI: очистка экрана, alt-screen, цвет
        dirty = '\x1b[2J\x1b[?1049h\x1b[H\x1b[39;1mHello\x1b[0m\x08 world\x1b7'
        self.assertEqual(Dt._sanitize(dirty), 'Hello world')
        self.assertEqual(Dt._sanitize('plain\ttext\nok'), 'plain\ttext\nok')  # \t\n целы

    def test_tool_result_strips_ansi(self):
        raw = '\x1b[?25l\x1b[2Jsome output\x1b[0m'
        r = Dt._tool_result_text({'content': raw})
        self.assertNotIn('\x1b', r)
        self.assertEqual(r, 'some output')


class TmpDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='ccsess_')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)


class TestProbeSession(TmpDirTest):
    def test_extracts_cwd_and_entrypoint(self):
        p = self.path('s.jsonl')
        write_jsonl(p, [
            {'type': 'summary'},
            {'type': 'user', 'cwd': '/proj', 'entrypoint': 'cli'},
        ])
        self.assertEqual(Dt._probe_session(p), ('/proj', 'cli'))

    def test_missing_returns_none(self):
        p = self.path('s.jsonl')
        write_jsonl(p, [{'type': 'user', 'message': {'content': 'x'}}])
        self.assertEqual(Dt._probe_session(p), (None, None))


class TestSessionMeta(TmpDirTest):
    def test_custom_title_wins(self):
        p = self.path('s.jsonl')
        write_jsonl(p, [
            {'type': 'user', 'cwd': '/p', 'message': {'content': 'hi there'}},
            {'type': 'assistant', 'message': {'content': 'ok'}},
            {'type': 'ai-title', 'aiTitle': 'AI T'},
            {'type': 'custom-title', 'customTitle': 'Custom T'},
        ])
        meta = Dt.load_session_meta(p)
        self.assertEqual(meta['title'], 'Custom T')
        self.assertEqual(meta['auto_title'], 'AI T')
        self.assertTrue(meta['custom'])
        self.assertEqual(meta['msg_count'], 2)
        self.assertEqual(meta['cwd'], '/p')

    def test_ai_title_then_first_human(self):
        p = self.path('a.jsonl')
        write_jsonl(p, [{'type': 'user', 'message': {'content': 'first human'}},
                        {'type': 'ai-title', 'aiTitle': 'The AI'}])
        self.assertEqual(Dt.load_session_meta(p)['title'], 'The AI')

        p2 = self.path('b.jsonl')
        write_jsonl(p2, [{'type': 'user', 'message': {'content': 'just human'}}])
        m = Dt.load_session_meta(p2)
        self.assertEqual(m['title'], 'just human')
        self.assertFalse(m['custom'])

    def test_untitled_when_empty(self):
        p = self.path('e.jsonl')
        write_jsonl(p, [{'type': 'assistant', 'message': {'content': 'x'}}])
        self.assertEqual(Dt.load_session_meta(p)['title'], '(untitled)')

    def test_branch_takes_last_seen(self):
        p = self.path('br.jsonl')
        write_jsonl(p, [
            {'type': 'user', 'gitBranch': 'main', 'message': {'content': 'a'}},
            {'type': 'assistant', 'gitBranch': 'feature/x', 'message': {'content': 'b'}},
        ])
        self.assertEqual(Dt.load_session_meta(p)['branch'], 'feature/x')

    def test_branch_none_when_absent(self):
        p = self.path('nb.jsonl')
        write_jsonl(p, [{'type': 'user', 'message': {'content': 'a'}}])
        self.assertIsNone(Dt.load_session_meta(p)['branch'])

    def test_slash_command_title_is_cmd_and_args(self):
        p = self.path('cmd.jsonl')
        blob = ('<command-message>tinkerwell-debug</command-message>\n'
                '<command-name>/tinkerwell-debug</command-name>\n'
                '<command-args>почему нет ндс</command-args>')
        write_jsonl(p, [{'type': 'user', 'message': {'content': blob}}])
        self.assertEqual(Dt.load_session_meta(p)['title'],
                         '/tinkerwell-debug почему нет ндс')

    def test_caveat_only_message_is_skipped(self):
        p = self.path('cav.jsonl')
        caveat = ('<local-command-caveat>Caveat: The messages below were generated '
                  'by the user while running local commands.</local-command-caveat>')
        write_jsonl(p, [
            {'type': 'user', 'message': {'content': caveat}},
            {'type': 'user', 'message': {'content': 'настоящий вопрос'}},
        ])
        self.assertEqual(Dt.load_session_meta(p)['title'], 'настоящий вопрос')


class TestConversation(TmpDirTest):
    def test_entries(self):
        p = self.path('c.jsonl')
        write_jsonl(p, [
            {'type': 'user', 'message': {'content': 'hello'}},
            {'type': 'assistant', 'message': {'content': [
                {'type': 'text', 'text': 'hi'}, {'type': 'tool_use', 'name': 'Bash'}]}},
            {'type': 'assistant', 'message': {'content': [
                {'type': 'tool_result', 'content': [{'type': 'text', 'text': '  out  '}]}]}},
            {'type': 'system', 'message': {'content': 'ignored'}},
        ])
        self.assertEqual(Dt.load_conversation(p), [
            ('user', 'hello'),
            ('assistant', 'hi'),
            ('tool', '→ Bash'),
            ('tool', '‹result› out'),
        ])


class TestAppendCustomTitle(TmpDirTest):
    def test_appends_and_meta_reads_it(self):
        p = self.path('s.jsonl')
        with open(p, 'w') as f:
            f.write('{"type":"user","message":{"content":"orig"}}')  # без \n в конце
        self.assertTrue(Dt.append_custom_title(p, 'SID', 'New Name'))
        self.assertEqual(Dt.load_session_meta(p)['title'], 'New Name')
        self.assertTrue(Dt.load_session_meta(p)['custom'])

    def test_missing_file_returns_false(self):
        self.assertFalse(Dt.append_custom_title(self.path('nope.jsonl'), 'S', 'N'))

    def test_appends_after_non_ascii_tail_without_newline(self):
        p = self.path('s.jsonl')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('{"type": "user", "message": {"content": "привет"}}')
        self.assertTrue(Dt.append_custom_title(p, 'SID', 'Имя'))
        meta = Dt.load_session_meta(p)
        self.assertEqual(meta['title'], 'Имя')
        self.assertEqual(meta['msg_count'], 1)


class TestLoadSessions(TmpDirTest):
    def test_sorted_by_mtime_desc(self):
        old = self.path('old.jsonl')
        new = self.path('new.jsonl')
        write_jsonl(old, [{'type': 'user', 'message': {'content': 'old'}}])
        write_jsonl(new, [{'type': 'user', 'message': {'content': 'new'}}])
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        project = {'files': [old, new], 'path': '/proj'}
        sessions = Dt.load_sessions(project)
        self.assertEqual([s['id'] for s in sessions], ['new', 'old'])
        self.assertEqual(sessions[0]['title'], 'new')
        self.assertEqual(sessions[0]['cwd'], '/proj')      # fallback на project path

    def test_meta_cached_until_file_changes(self):
        p = self.path('s.jsonl')
        write_jsonl(p, [{'type': 'user', 'message': {'content': 'first'}}])
        project = {'files': [p], 'path': '/proj'}
        self.assertEqual(Dt.load_sessions(project)[0]['title'], 'first')

        # без изменения файла — из кэша, parse не зовётся
        orig = Dt.load_session_meta
        Dt.load_session_meta = lambda path: self.fail('parse must be cached')
        try:
            self.assertEqual(Dt.load_sessions(project)[0]['title'], 'first')
        finally:
            Dt.load_session_meta = orig

        # изменение файла инвалидирует кэш (append меняет size)
        self.assertTrue(Dt.append_custom_title(p, 'SID', 'renamed'))
        self.assertEqual(Dt.load_sessions(project)[0]['title'], 'renamed')


class TestRunningSessions(TmpDirTest):
    def setUp(self):
        super().setUp()
        self._orig = Dt.SESSIONS_DIR
        Dt.SESSIONS_DIR = self.tmp

    def tearDown(self):
        Dt.SESSIONS_DIR = self._orig
        super().tearDown()

    def test_only_alive_pids(self):
        with open(self.path('alive.json'), 'w') as f:
            json.dump({'pid': os.getpid(), 'sessionId': 'S1',
                       'status': 'busy', 'cwd': '/c', 'name': 'n'}, f)
        with open(self.path('dead.json'), 'w') as f:
            json.dump({'pid': 2 ** 31 - 1, 'sessionId': 'S2'}, f)
        with open(self.path('nopid.json'), 'w') as f:
            json.dump({'sessionId': 'S3'}, f)
        res = Dt.running_sessions()
        self.assertEqual(set(res), {'S1'})
        self.assertEqual(res['S1']['status'], 'busy')
        self.assertEqual(res['S1']['cwd'], '/c')


class TestScanProjects(TmpDirTest):
    def setUp(self):
        super().setUp()
        self._orig = Dt.PROJECTS_DIR
        Dt.PROJECTS_DIR = self.tmp

    def tearDown(self):
        Dt.PROJECTS_DIR = self._orig
        super().tearDown()

    def _proj(self, name, cwd, entrypoint='cli'):
        d = self.path(name)
        os.makedirs(d)
        write_jsonl(os.path.join(d, 'x.jsonl'),
                    [{'type': 'user', 'cwd': cwd, 'entrypoint': entrypoint,
                      'message': {'content': 'hi'}}])
        return d

    def test_scans_and_filters(self):
        self._proj('proj1', '/real/proj1')
        os.makedirs(self.path('empty'))  # без jsonl — пропускается
        self._proj('internal', os.path.expanduser('~/.claude/inside'))  # внутренняя папка Claude
        projects = {p['dir_name']: p for p in Dt.scan_projects()}
        self.assertIn('proj1', projects)
        self.assertEqual(projects['proj1']['path'], '/real/proj1')
        self.assertEqual(projects['proj1']['name'], 'proj1')
        self.assertEqual(projects['proj1']['probes'][0]['entrypoint'], 'cli')
        self.assertNotIn('empty', projects)
        self.assertNotIn('internal', projects)


if __name__ == '__main__':
    unittest.main()
