import json
import os
import shutil
import tempfile
import unittest

import kittymock  # noqa: F401
import modules.session.data as Dt
import modules.session.util as Ut


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

    def test_result_text_caps_chars(self):
        long = 'x ' * 20_000
        self.assertLessEqual(len(Dt._result_text({'content': long})),
                             Dt.MAX_RESULT_CHARS)

    def test_result_text_trims_trailing_space(self):
        self.assertEqual(Dt._result_text(
            {'content': [{'type': 'text', 'text': 'a  \nb  '}]}), 'a\nb')

    def test_sanitize_strips_ansi_and_control(self):
        # сырой вывод TUI: очистка экрана, alt-screen, цвет
        dirty = '\x1b[2J\x1b[?1049h\x1b[H\x1b[39;1mHello\x1b[0m\x08 world\x1b7'
        self.assertEqual(Dt._sanitize(dirty), 'Hello world')
        # \n цел; \t раскрыт в пробелы (терминал раздул бы его до 8
        # колонок, а truncate/выделение считают символы)
        self.assertEqual(Dt._sanitize('plain\ttext\nok'), 'plain   text\nok')

    def test_result_strips_ansi(self):
        raw = '\x1b[?25l\x1b[2Jsome output\x1b[0m'
        r = Dt._result_text({'content': raw})
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
                {'type': 'text', 'text': 'hi'},
                {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'ls'}}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'content': [{'type': 'text', 'text': '  out  '}]}]}},
            {'type': 'system', 'message': {'content': 'ignored'}},
        ])
        self.assertEqual(Dt.load_conversation(p), [
            Dt.Entry('user', 'hello'),
            Dt.Entry('assistant', 'hi'),
            Dt.Entry('tool', name='Bash', tool_input={'command': 'ls'}),
            Dt.Entry('result', '  out'),   # отступ вывода сохраняем, хвост режем
        ])

    def test_user_wrappers_stripped(self):
        p = self.path('w.jsonl')
        blob = ('<system-reminder>внутреннее</system-reminder>вопрос'
                '<local-command-caveat>шум</local-command-caveat>')
        write_jsonl(p, [{'type': 'user', 'message': {'content': blob}}])
        self.assertEqual(Dt.load_conversation(p), [Dt.Entry('user', 'вопрос')])

    def test_task_notification_is_dropped(self):
        # отчёт фоновой задачи — килобайты JSON,
        # которые пользователь не писал
        p = self.path('tn.jsonl')
        blob = ('<task-notification>\n<task-id>a1</task-id>\n'
                '<result>{"findings": []}</result>\n</task-notification>\n'
                'продолжай')
        write_jsonl(p, [
            {'type': 'user', 'message': {'content': blob}},
            {'type': 'user', 'message': {'content':
                '<task-notification>\n<status>ok</status>\n</task-notification>'}},
        ])
        # от смешанной записи остаётся речь, чисто
        # служебная пропадает целиком
        self.assertEqual(Dt.load_conversation(p), [Dt.Entry('user', 'продолжай')])

    def test_image_meta_becomes_an_attachment(self):
        # isMeta-запись «[Image: source: …/13.png]» — не реплика,
        # а вложение предыдущей: Claude Code показывает её как
        # ⎿ [Image #13]
        p = self.path('img.jsonl')
        cache = '/Users/x/.claude/image-cache/abc'
        write_jsonl(p, [
            {'type': 'user', 'message': {'content': [
                {'type': 'text', 'text': 'смотри [Image #13]'},
                {'type': 'image', 'source': {'type': 'base64', 'data': '…'}}]}},
            {'type': 'user', 'isMeta': True, 'message': {'content': [
                {'type': 'text', 'text': f'[Image: source: {cache}/13.png]'},
                {'type': 'text', 'text': f'[Image: source: {cache}/14.png]'}]}},
        ])
        self.assertEqual(Dt.load_conversation(p), [
            Dt.Entry('user', 'смотри [Image #13]'),
            Dt.Entry('attach', '[Image #13]'),
            Dt.Entry('attach', '[Image #14]'),
        ])

    def test_abandoned_branch_is_dropped(self):
        # промпт, отменённый по Esc, остаётся в файле веткой-тупиком
        p = self.path('branch.jsonl')
        write_jsonl(p, [
            {'type': 'user', 'uuid': 'a', 'parentUuid': None,
             'message': {'content': 'черновик'}},
            {'type': 'user', 'uuid': 'b', 'parentUuid': None,
             'message': {'content': 'вопрос'}},
            {'type': 'assistant', 'uuid': 'c', 'parentUuid': 'b',
             'message': {'content': [{'type': 'text', 'text': 'ответ'}]}},
        ])
        self.assertEqual(Dt.load_conversation(p), [
            Dt.Entry('user', 'вопрос'),
            Dt.Entry('assistant', 'ответ'),
        ])

    def _ask_jsonl(self, name, result, tur):
        p = self.path(name)
        write_jsonl(p, [
            {'type': 'assistant', 'uuid': 'a', 'parentUuid': None,
             'message': {'content': [
                 {'type': 'tool_use', 'id': 'q1', 'name': 'AskUserQuestion',
                  'input': {'questions': [{'question': 'Порог?'}]}}]}},
            {'type': 'user', 'uuid': 'b', 'parentUuid': 'a',
             'toolUseResult': tur,
             'message': {'content': [dict(result, type='tool_result',
                                          tool_use_id='q1')]}},
        ])
        return p

    def test_ask_user_question_keeps_only_the_answers(self):
        p = self._ask_jsonl('ask.jsonl',
                            {'content': 'Your questions have been answered: …'},
                            {'answers': {'Порог?': 'От трёх'}})
        self.assertEqual(Dt.load_conversation(p)[1].text, '· Порог? → От трёх')

    def test_unknown_result_shape_is_not_a_rejection(self):
        # ответы не разобрались — показываем вывод, а не «отказ»
        p = self._ask_jsonl('shape.jsonl', {'content': 'Answered: От трёх'},
                            {'unexpected': 1})
        entries = Dt.load_conversation(p)
        self.assertEqual([e.name for e in entries], [Ut.ASK_TOOL, Ut.ASK_TOOL])
        self.assertEqual(entries[1].text, 'Answered: От трёх')

    def test_rejected_question_is_renamed(self):
        p = self._ask_jsonl('reject.jsonl',
                            {'content': "The user doesn't want to proceed"},
                            'User rejected tool use')
        entries = Dt.load_conversation(p)
        self.assertEqual([e.name for e in entries],
                         [Ut.ASK_REJECTED, Ut.ASK_REJECTED])

    def test_error_result_is_a_rejection(self):
        p = self._ask_jsonl('err.jsonl',
                            {'content': 'Interrupted', 'is_error': True}, None)
        entries = Dt.load_conversation(p)
        self.assertEqual([e.name for e in entries],
                         [Ut.ASK_REJECTED, Ut.ASK_REJECTED])
        self.assertEqual(entries[1].text, '')

    def test_non_image_meta_is_dropped(self):
        p = self.path('meta.jsonl')
        write_jsonl(p, [{'type': 'user', 'isMeta': True, 'message': {'content':
            '<local-command-caveat>шум</local-command-caveat>'}}])
        self.assertEqual(Dt.load_conversation(p), [])

    def test_tool_use_error_tags_stripped(self):
        p = self.path('te.jsonl')
        write_jsonl(p, [{'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'content': '<tool_use_error>bad</tool_use_error>',
             'is_error': True}]}}])
        self.assertEqual(Dt.load_conversation(p)[0].text, 'bad')

    def test_result_is_linked_to_its_call(self):
        p = self.path('link.jsonl')
        write_jsonl(p, [
            {'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 'tu_1', 'name': 'Edit',
                 'input': {'file_path': '/a/b.py'}}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'tu_1', 'content': 'ok'}]}},
        ])
        result = Dt.load_conversation(p)[1]
        self.assertEqual(result.kind, 'result')
        self.assertEqual(result.name, 'Edit')
        self.assertEqual(result.tool_input, {'file_path': '/a/b.py'})

    def test_structured_patch_becomes_numbered_rows(self):
        p = self.path('patch.jsonl')
        write_jsonl(p, [{
            'type': 'user',
            'toolUseResult': {'structuredPatch': [
                {'oldStart': 10, 'newStart': 10,
                 'lines': [' ctx', '-old', '+new', '+extra']},
            ]},
            'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'x', 'content': 'ok'}]},
        }])
        self.assertEqual(Dt.load_conversation(p)[0].patch, (
            (10, ' ', 'ctx'),
            (11, '-', 'old'),      # удалённая — номер старого файла
            (11, '+', 'new'),      # добавленные — номера нового
            (12, '+', 'extra'),
        ))

    def test_read_result_gets_summary(self):
        p = self.path('read.jsonl')
        write_jsonl(p, [
            {'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 'r1', 'name': 'Read',
                 'input': {'file_path': '/a.py'}}]}},
            {'type': 'user',
             'toolUseResult': {'file': {'numLines': 402, 'totalLines': 402}},
             'message': {'content': [
                 {'type': 'tool_result', 'tool_use_id': 'r1', 'content': 'body'}]}},
        ])
        self.assertEqual(Dt.load_conversation(p)[1].summary, 'Read 402 lines')

    def test_summary_is_empty_for_other_tools(self):
        p = self.path('bash.jsonl')
        write_jsonl(p, [{'type': 'user',
                         'toolUseResult': {'stdout': 'x'},
                         'message': {'content': [
                             {'type': 'tool_result', 'content': 'x'}]}}])
        self.assertEqual(Dt.load_conversation(p)[0].summary, '')

    def test_agent_result_gets_a_done_summary(self):
        # Claude Code сворачивает отчёт субагента в «Done (…)» — данные
        # из toolUseResult, а не из текста ответа
        p = self.path('agent.jsonl')
        write_jsonl(p, [
            {'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 'a1', 'name': 'Agent',
                 'input': {'description': 'Count files'}}]}},
            {'type': 'user',
             'toolUseResult': {'status': 'completed', 'totalToolUseCount': 1,
                               'totalTokens': 25532, 'totalDurationMs': 18049},
             'message': {'content': [
                 {'type': 'tool_result', 'tool_use_id': 'a1', 'content': '28'}]}},
        ])
        self.assertEqual(Dt.load_conversation(p)[1].summary,
                         'Done (1 tool use · 25.5k tokens · 18s)')

    def test_agent_summary_formats_and_degrades(self):
        self.assertEqual(
            Dt._agent_summary({'status': 'completed', 'totalToolUseCount': 9,
                               'totalTokens': 906, 'totalDurationMs': 95_400}),
            'Done (9 tool uses · 906 tokens · 1m 35s)')
        self.assertEqual(
            Dt._agent_summary({'status': 'failed', 'totalTokens': 1_250_000}),
            'Failed (1.2M tokens)')
        self.assertEqual(Dt._agent_summary({'status': 'completed'}), 'Done')

    def test_result_without_id_is_not_linked(self):
        p = self.path('nolink.jsonl')
        write_jsonl(p, [{'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'content': 'ok'}]}}])
        self.assertEqual(Dt.load_conversation(p), [Dt.Entry('result', 'ok')])

    def test_parallel_results_follow_their_calls(self):
        # батч из двух tool_use: результаты приходят пачкой после
        # всех вызовов, но каждый должен встать под своим, а не
        # по порядку файла
        p = self.path('par.jsonl')
        write_jsonl(p, [
            {'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 'a', 'name': 'Bash',
                 'input': {'command': 'ls'}},
                {'type': 'tool_use', 'id': 'b', 'name': 'Grep',
                 'input': {'pattern': 'x'}}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'a', 'content': 'bash out'}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'b', 'content': 'grep out'}]}},
        ])
        kinds = [(e.kind, e.name) for e in Dt.load_conversation(p)]
        self.assertEqual(kinds, [('tool', 'Bash'), ('result', 'Bash'),
                                 ('tool', 'Grep'), ('result', 'Grep')])

    def test_patch_stat_counts_beyond_cap(self):
        p = self.path('bigpatch.jsonl')
        n = Dt.MAX_RESULT_LINES + 50
        write_jsonl(p, [{
            'type': 'user',
            'toolUseResult': {'structuredPatch': [
                {'oldStart': 1, 'newStart': 1, 'lines': ['+x'] * n}]},
            'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'x', 'content': 'ok'}]},
        }])
        e = Dt.load_conversation(p)[0]
        self.assertEqual(len(e.patch), Dt.MAX_RESULT_LINES)   # строки обрезаны
        self.assertEqual(e.patch_stat, (n, 0))                # счётчики честные

    def test_result_keeps_newlines_and_error_flag(self):
        p = self.path('e.jsonl')
        write_jsonl(p, [{'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'content': 'a\nb', 'is_error': True}]}}])
        e = Dt.load_conversation(p)[0]
        self.assertEqual(e.text, 'a\nb')
        self.assertTrue(e.error)

    def test_huge_result_is_capped(self):
        p = self.path('big.jsonl')
        body = '\n'.join(f'line {i}' for i in range(Dt.MAX_RESULT_LINES + 50))
        write_jsonl(p, [{'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'content': body}]}}])
        e = Dt.load_conversation(p)[0]
        self.assertEqual(len(e.text.split('\n')), Dt.MAX_RESULT_LINES)


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
