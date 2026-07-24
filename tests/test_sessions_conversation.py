import json
import os
import shutil
import tempfile
import unittest

import kittymock  # noqa: F401
import modules.session.conversation as Cv
import modules.session.util as Ut


def write_jsonl(path, records):
    with open(path, 'w') as f:
        for r in records:
            f.write((r if isinstance(r, str) else json.dumps(r)) + '\n')


class TmpDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='ccsess_')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)


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
        self.assertEqual(Cv.load_conversation(p), [
            Cv.Entry('user', 'hello'),
            Cv.Entry('assistant', 'hi'),
            Cv.Entry('tool', name='Bash', tool_input={'command': 'ls'}),
            Cv.Entry('result', '  out'),   # отступ вывода сохраняем, хвост режем
        ])

    def test_user_wrappers_stripped(self):
        p = self.path('w.jsonl')
        blob = ('<system-reminder>внутреннее</system-reminder>вопрос'
                '<local-command-caveat>шум</local-command-caveat>')
        write_jsonl(p, [{'type': 'user', 'message': {'content': blob}}])
        self.assertEqual(Cv.load_conversation(p), [Cv.Entry('user', 'вопрос')])

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
        self.assertEqual(Cv.load_conversation(p), [Cv.Entry('user', 'продолжай')])

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
        self.assertEqual(Cv.load_conversation(p), [
            Cv.Entry('user', 'смотри [Image #13]'),
            Cv.Entry('attach', '[Image #13]'),
            Cv.Entry('attach', '[Image #14]'),
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
        self.assertEqual(Cv.load_conversation(p), [
            Cv.Entry('user', 'вопрос'),
            Cv.Entry('assistant', 'ответ'),
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
        self.assertEqual(Cv.load_conversation(p)[1].text, '· Порог? → От трёх')

    def test_unknown_result_shape_is_not_a_rejection(self):
        # ответы не разобрались — показываем вывод, а не «отказ»
        p = self._ask_jsonl('shape.jsonl', {'content': 'Answered: От трёх'},
                            {'unexpected': 1})
        entries = Cv.load_conversation(p)
        self.assertEqual([e.name for e in entries], [Ut.ASK_TOOL, Ut.ASK_TOOL])
        self.assertEqual(entries[1].text, 'Answered: От трёх')

    def test_rejected_question_is_renamed(self):
        p = self._ask_jsonl('reject.jsonl',
                            {'content': "The user doesn't want to proceed"},
                            'User rejected tool use')
        entries = Cv.load_conversation(p)
        self.assertEqual([e.name for e in entries],
                         [Ut.ASK_REJECTED, Ut.ASK_REJECTED])

    def test_error_result_is_a_rejection(self):
        p = self._ask_jsonl('err.jsonl',
                            {'content': 'Interrupted', 'is_error': True}, None)
        entries = Cv.load_conversation(p)
        self.assertEqual([e.name for e in entries],
                         [Ut.ASK_REJECTED, Ut.ASK_REJECTED])
        self.assertEqual(entries[1].text, '')

    def test_non_image_meta_is_dropped(self):
        p = self.path('meta.jsonl')
        write_jsonl(p, [{'type': 'user', 'isMeta': True, 'message': {'content':
            '<local-command-caveat>шум</local-command-caveat>'}}])
        self.assertEqual(Cv.load_conversation(p), [])

    def test_tool_use_error_tags_stripped(self):
        p = self.path('te.jsonl')
        write_jsonl(p, [{'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'content': '<tool_use_error>bad</tool_use_error>',
             'is_error': True}]}}])
        self.assertEqual(Cv.load_conversation(p)[0].text, 'bad')

    def test_result_is_linked_to_its_call(self):
        p = self.path('link.jsonl')
        write_jsonl(p, [
            {'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 'tu_1', 'name': 'Edit',
                 'input': {'file_path': '/a/b.py'}}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'tu_1', 'content': 'ok'}]}},
        ])
        result = Cv.load_conversation(p)[1]
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
        self.assertEqual(Cv.load_conversation(p)[0].patch, (
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
        self.assertEqual(Cv.load_conversation(p)[1].summary, 'Read 402 lines')

    def test_summary_is_empty_for_other_tools(self):
        p = self.path('bash.jsonl')
        write_jsonl(p, [{'type': 'user',
                         'toolUseResult': {'stdout': 'x'},
                         'message': {'content': [
                             {'type': 'tool_result', 'content': 'x'}]}}])
        self.assertEqual(Cv.load_conversation(p)[0].summary, '')

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
        self.assertEqual(Cv.load_conversation(p)[1].summary,
                         'Done (1 tool use · 25.5k tokens · 18s)')

    def test_agent_summary_formats_and_degrades(self):
        self.assertEqual(
            Cv._agent_summary({'status': 'completed', 'totalToolUseCount': 9,
                               'totalTokens': 906, 'totalDurationMs': 95_400}),
            'Done (9 tool uses · 906 tokens · 1m 35s)')
        self.assertEqual(
            Cv._agent_summary({'status': 'failed', 'totalTokens': 1_250_000}),
            'Failed (1.2M tokens)')
        self.assertEqual(Cv._agent_summary({'status': 'completed'}), 'Done')

    def test_result_without_id_is_not_linked(self):
        p = self.path('nolink.jsonl')
        write_jsonl(p, [{'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'content': 'ok'}]}}])
        self.assertEqual(Cv.load_conversation(p), [Cv.Entry('result', 'ok')])

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
        kinds = [(e.kind, e.name) for e in Cv.load_conversation(p)]
        self.assertEqual(kinds, [('tool', 'Bash'), ('result', 'Bash'),
                                 ('tool', 'Grep'), ('result', 'Grep')])

    def test_patch_stat_counts_beyond_cap(self):
        p = self.path('bigpatch.jsonl')
        n = Cv.MAX_RESULT_LINES + 50
        write_jsonl(p, [{
            'type': 'user',
            'toolUseResult': {'structuredPatch': [
                {'oldStart': 1, 'newStart': 1, 'lines': ['+x'] * n}]},
            'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'x', 'content': 'ok'}]},
        }])
        e = Cv.load_conversation(p)[0]
        self.assertEqual(len(e.patch), Cv.MAX_RESULT_LINES)   # строки обрезаны
        self.assertEqual(e.patch_stat, (n, 0))                # счётчики честные

    def test_result_keeps_newlines_and_error_flag(self):
        p = self.path('e.jsonl')
        write_jsonl(p, [{'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'content': 'a\nb', 'is_error': True}]}}])
        e = Cv.load_conversation(p)[0]
        self.assertEqual(e.text, 'a\nb')
        self.assertTrue(e.error)

    def test_huge_result_is_capped(self):
        p = self.path('big.jsonl')
        body = '\n'.join(f'line {i}' for i in range(Cv.MAX_RESULT_LINES + 50))
        write_jsonl(p, [{'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'content': body}]}}])
        e = Cv.load_conversation(p)[0]
        self.assertEqual(len(e.text.split('\n')), Cv.MAX_RESULT_LINES)


if __name__ == '__main__':
    unittest.main()
