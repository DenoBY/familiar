"""Автодополнение в режиме правки review: когда список открывается и
закрывается, клавиши, вставка пункта (auto-import, сниппет), поздние
ответы, слова файла без сервера, плашка на экране и подсказка
сигнатуры.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

from lspmock import FakePool, FakeSession

import kittymock  # noqa: F401
import review as R
from kittymock import (
    EventType,
    KeyEvent,
    MouseButton,
    MouseEvent,
    draw_text,
    run_threads_inline,
    wire,
)
from modules.lsp.session import NoServer
from modules.vcs import complete as C
from modules.vcs.workspace import Workspace


_ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}

SOURCE = 'import os\n\n\ndef greet(name):\n    return name\n\n\ngreeting = 1\n'


def _rng(line, a, b, end_line=None):
    return {'start': {'line': line, 'character': a},
            'end': {'line': line if end_line is None else end_line, 'character': b}}


def labels(*names, incomplete=False):
    """Ответ в духе pyright: без диапазонов, фильтрует клиент."""
    return {'isIncomplete': incomplete,
            'items': [{'label': n, 'kind': 3, 'sortText': f'{i:04}'}
                      for i, n in enumerate(names)]}


class CompletionTestBase(unittest.TestCase):

    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        run_threads_inline(self)
        self.repo = tempfile.mkdtemp(prefix='ccrev_cmp_')
        self._git('init', '-b', 'main')
        self.write('a.py', SOURCE)
        self._git('add', '-A')
        self._git('commit', '-m', 'init')
        self.write('a.py', SOURCE + '\n')
        self.session = FakeSession(completion=labels('greet', 'greeting', 'global'))
        self.pool = FakePool(self.session)
        self.h = R.ReviewHandler([], Workspace.single(self.repo))
        wire(self.h, rows=30, cols=120)
        self.h._copy_clipboard = lambda text: None
        self.h.load_source()
        self.h._lsp_pools = {self.h.root: self.pool}

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _git(self, *args):
        subprocess.run(['git', '-C', self.repo, *args], check=True,
                       capture_output=True, env=os.environ)

    def write(self, rel, content):
        with open(os.path.join(self.repo, rel), 'w') as f:
            f.write(content)

    def edit_at_end(self, line):
        """Правка в конце строки `line` (с 1)."""
        self.h.set_focus('diff')
        self.h.view_mode = 'final'
        self.h.build_diff_rows()
        self.h.diff_cur = self.h.diff_lineno.index(line)
        self.h.on_text('i')
        self.assertTrue(self.h.editing, self.h.flash)
        self.key('END')

    def key(self, name, **mods):
        self.h.on_key(KeyEvent(name, **mods))

    def type(self, text):
        for ch in text:
            self.h.on_text(ch)

    def screen(self):
        self.h.out = []
        self.h.draw_screen()
        return draw_text(self.h)

    @property
    def buf(self):
        return self.h.edit_buf

    def shown(self):
        popup = self.h._cmp
        return [item.label for item, _, _ in popup.shown] if popup is not None else []

    def visible(self):
        return self.h._cmp_visible()


class OpenCloseTest(CompletionTestBase):

    def test_two_letters_open_the_list(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('g')
        self.assertEqual(self.session.completions, [])
        self.type('r')
        self.assertEqual(len(self.session.completions), 1)
        self.assertEqual(self.session.completions[0][3], {'triggerKind': 1})
        self.assertEqual(self.shown(), ['greet', 'greeting'])
        self.assertIn('[complete]', self.screen())

    def test_same_case_prefix_goes_first(self):
        self.session.completion = labels('use_soap', 'user_error', 'User')
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('Use')
        self.assertEqual(self.shown()[0], 'User')

    def test_typing_on_refilters_without_asking_again(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('gre')
        self.assertEqual(len(self.session.completions), 1)
        self.type('eti')
        self.assertEqual(self.shown(), ['greeting'])
        self.assertEqual(len(self.session.completions), 1)

    def test_incomplete_list_asks_again_on_each_letter(self):
        self.session.completion = labels('greet', 'greeting', incomplete=True)
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('gre')
        kinds = [c[3]['triggerKind'] for c in self.session.completions]
        self.assertEqual(kinds, [1, 3])

    def test_trigger_character_asks_at_once(self):
        self.session.completion = labels('path', 'sep')
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('os.')
        self.assertEqual(self.session.completions[-1][3],
                         {'triggerKind': 2, 'triggerCharacter': '.'})
        self.assertEqual(self.shown(), ['path', 'sep'])

    def test_space_is_never_a_trigger(self):
        self.session.completion_triggers = (' ',)
        self.edit_at_end(9)
        self.type(' ')
        self.assertEqual(self.session.completions, [])

    def test_cyrillic_and_digits_do_not_open(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('жжж')
        self.type('12')
        self.assertEqual(self.session.completions, [])

    def test_paste_does_not_open(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.h.on_text('greet', in_bracketed_paste=True)
        self.assertEqual(self.session.completions, [])

    def test_entering_edit_with_i_is_not_typing(self):
        self.edit_at_end(9)
        self.assertEqual(self.session.completions, [])

    def test_non_word_character_closes(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('gr')
        self.assertTrue(self.visible())
        self.type(')')
        self.assertFalse(self.visible())

    def test_empty_answer_shows_nothing(self):
        self.session.completion = {'isIncomplete': False, 'items': []}
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('zz')
        self.assertFalse(self.visible())

    def test_escape_closes_list_then_leaves_edit(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('gr')
        self.key('ESCAPE')
        self.assertFalse(self.visible())
        self.assertTrue(self.h.editing)
        self.key('ESCAPE')
        self.assertFalse(self.h.editing)

    def test_moving_to_another_line_closes(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('gr')
        self.key('ESCAPE')
        self.type('e')
        self.assertTrue(self.visible())
        self.buf.set_caret(0, 0)
        self.h._edited()
        self.assertFalse(self.visible())

    def test_ctrl_space_opens_by_hand(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('g')
        self.key(' ', ctrl=True)
        self.assertEqual(self.session.completions[-1][3], {'triggerKind': 1})
        self.assertTrue(self.visible())

    def test_nul_byte_and_alt_escape_also_open(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.h.on_text('\x00')
        self.assertTrue(self.visible())
        self.key('ESCAPE')
        self.key('ESCAPE', alt=True)
        self.assertTrue(self.visible())
        self.assertTrue(self.h.editing)

    def test_find_closes_the_list(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('gr')
        self.key('f', super=True)
        self.assertIsNone(self.h._cmp)


class KeysTest(CompletionTestBase):

    def open(self, text='gr'):
        self.edit_at_end(9)
        self.key('ENTER')
        self.type(text)
        self.assertTrue(self.visible())

    def test_enter_inserts_instead_of_breaking_the_line(self):
        self.open()
        n = len(self.buf.lines)
        self.key('ENTER')
        self.assertEqual(len(self.buf.lines), n)
        self.assertEqual(self.buf.lines[self.buf.line], 'greet')
        self.assertEqual(self.buf.col, 5)
        self.assertFalse(self.visible())

    def test_tab_inserts_instead_of_indenting(self):
        self.open()
        self.key('DOWN')
        self.key('TAB')
        self.assertEqual(self.buf.lines[self.buf.line], 'greeting')

    def test_arrows_wrap_around(self):
        self.open()
        self.key('UP')
        self.assertEqual(self.h._cmp.sel, 1)
        self.key('DOWN')
        self.assertEqual(self.h._cmp.sel, 0)

    def test_undo_takes_back_the_insertion_at_once(self):
        self.open()
        self.key('ENTER')
        self.key('z', super=True)
        self.assertEqual(self.buf.lines[self.buf.line], 'gr')


class AcceptTest(CompletionTestBase):

    def test_auto_import_on_top_moves_the_caret_and_undoes_together(self):
        line = len(SOURCE.split('\n'))            # новая строка после ENTER, с 0
        self.session.completion = [{
            'label': 'OrderedDict', 'kind': 7,
            'textEdit': {'range': _rng(line, 0, 3), 'newText': 'OrderedDict'},
            'additionalTextEdits': [{'range': _rng(0, 9, 9),
                                     'newText': '\nfrom collections import OrderedDict'}]}]
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('Ord')
        self.key('ENTER')
        self.assertEqual(self.buf.lines[:2], ['import os', 'from collections import OrderedDict'])
        self.assertEqual(self.buf.lines[self.buf.line], 'OrderedDict')
        self.assertEqual(self.buf.caret, (line + 1, 11))
        self.key('z', super=True)
        self.assertEqual(self.buf.lines[0:2], ['import os', ''])
        self.assertEqual(self.buf.lines[-1], 'Ord')

    def test_snippet_placeholders_walk_with_tab(self):
        self.session.completion = [{'label': 'greet', 'kind': 3, 'insertTextFormat': 2,
                                    'insertText': 'greet(${1:name}, ${2:times})$0'}]
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('gre')
        self.key('ENTER')
        self.assertEqual(self.buf.selected_text(), 'name')
        self.assertIn('[snippet]', self.screen())
        self.type("'bob'")
        self.key('TAB')
        self.assertEqual(self.buf.selected_text(), 'times')
        self.type('2')
        self.key('TAB')
        self.assertEqual(self.buf.lines[self.buf.line], "greet('bob', 2)")
        self.assertEqual(self.buf.col, len("greet('bob', 2)"))
        self.assertIsNone(self.h._tabs)
        self.key('TAB')                                   # снова обычный отступ
        self.assertTrue(self.buf.lines[self.buf.line].endswith('    '))

    def test_escape_ends_the_snippet_without_leaving(self):
        self.session.completion = [{'label': 'greet', 'insertTextFormat': 2,
                                    'insertText': 'greet(${1:name})'}]
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('gre')
        self.key('ENTER')
        self.key('ESCAPE')
        self.assertIsNone(self.h._tabs)
        self.assertIsNone(self.buf.selection())
        self.assertTrue(self.h.editing)

    def test_text_after_the_caret_is_kept_unless_it_ends_the_insertion(self):
        line = len(SOURCE.split('\n'))
        self.session.completion = [{
            'label': 'greet', 'textEdit': {'newText': 'greet',
                                           'insert': _rng(line, 0, 2),
                                           'replace': _rng(line, 0, 4)}}]
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('et')
        self.key('LEFT')
        self.key('LEFT')
        self.type('gr')
        self.key(' ', ctrl=True)
        self.key('ENTER')
        self.assertEqual(self.buf.lines[line], 'greet')

    def test_command_asks_for_the_signature(self):
        self.session.completion = [{'label': 'greet', 'insertTextFormat': 2,
                                    'insertText': 'greet($0)',
                                    'command': {'command': C.TRIGGER_PARAMS}}]
        self.session.signature = {'signatures': [{'label': 'greet(name)',
                                                  'parameters': [{'label': 'name'}]}],
                                  'activeParameter': 0}
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('gre')
        self.key('ENTER')
        self.assertEqual(self.buf.lines[self.buf.line], 'greet()')
        self.assertEqual(len(self.session.signatures), 1)
        self.assertIn('greet(name)', self.screen())

    def test_resolve_adds_the_import_after_insertion(self):
        self.session.resolve_provider = True
        self.session.completion = [{'label': 'Path', 'kind': 7, 'data': 1}]
        self.session.resolve = {'Path': {'additionalTextEdits': [
            {'range': _rng(0, 0, 0), 'newText': 'from pathlib import Path\n'}]}}
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('Pat')
        self.session.resolved.clear()
        self.key('ENTER')
        self.assertEqual(self.buf.lines[0], 'from pathlib import Path')
        self.assertEqual(self.buf.lines[self.buf.line], 'Path')


class LateAnswerTest(CompletionTestBase):

    def defer(self):
        self.jobs = []
        real = self.h.run_background

        def queue(work, done):
            if getattr(done, '__name__', '') == '_cmp_done':
                self.jobs.append((work, done))
            else:
                real(work, done)

        self.h.run_background = queue

    def run_job(self):
        work, done = self.jobs.pop(0)
        done(work())

    def test_answer_after_more_typing_is_filtered_not_dropped(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.defer()
        self.type('gr')
        self.type('eeti')
        self.run_job()
        self.assertEqual(self.shown(), ['greeting'])

    def test_answer_for_another_line_is_dropped(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.defer()
        self.type('gr')
        self.key('ENTER')
        self.run_job()
        self.assertIsNone(self.h._cmp)

    def test_answer_after_escape_is_dropped(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.defer()
        self.type('gr')
        self.key(' ', ctrl=True)     # второй запрос ждёт первый
        self.h._cmp_close()
        self.run_job()
        self.assertEqual(self.jobs, [])
        self.assertIsNone(self.h._cmp)

    def test_only_one_request_in_flight_newest_goes_next(self):
        self.session.completion = labels('greet', incomplete=True)
        self.edit_at_end(9)
        self.key('ENTER')
        self.defer()
        self.type('gre')
        self.assertEqual(len(self.jobs), 1)
        self.run_job()
        self.assertEqual(len(self.jobs), 1)            # отложенный — за ним
        self.run_job()
        self.assertEqual(self.jobs, [])

    def test_crash_in_the_worker_does_not_disable_completion(self):
        self.session.completion = lambda params: 1 / 0
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('gr')
        self.assertFalse(self.h._cmp_busy)
        self.session.completion = labels('greet')
        self.type('e')
        self.assertEqual(self.shown(), ['greet'])


class DeferredRedrawTest(CompletionTestBase):
    """Порядок как в ките: строки пересобираются после нажатия."""

    def press(self, text=None, key=None, **mods):
        if text is not None:
            self.h.on_text(text)
        else:
            self.key(key, **mods)
        self.h.asyncio_loop.run_ready()

    def test_typing_accepting_and_snippet_with_deferred_sync(self):
        self.session.completion = [{'label': 'greet', 'kind': 3, 'insertTextFormat': 2,
                                    'insertText': 'greet(${1:name})$0'},
                                   {'label': 'greeting', 'kind': 6}]
        self.edit_at_end(9)
        self.key('ENTER')
        self.h.asyncio_loop = kittymock.DeferredLoop()
        for ch in 'gre':
            self.press(ch)
        self.assertEqual(self.shown(), ['greet', 'greeting'])
        self.press('e')
        self.press('t')
        self.assertEqual(self.shown(), ['greet', 'greeting'])
        self.press(key='ENTER')
        self.assertEqual(self.buf.lines[self.buf.line], 'greet(name)')
        self.assertEqual(self.buf.selected_text(), 'name')
        for ch in 'x':
            self.press(ch)
        self.press(key='TAB')
        self.assertEqual(self.buf.lines[self.buf.line], 'greet(x)')
        self.assertIsNone(self.h._tabs)
        self.assertIn('greet(x)', self.screen())


class WordsTest(CompletionTestBase):

    def test_file_words_without_a_server(self):
        self.pool.raises = NoServer('no language server configured for .py')
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('gr')
        self.assertIsNone(self.h._cmp)
        self.type('e')
        self.assertEqual(self.shown(), ['greet', 'greeting'])
        self.assertEqual(self.h._cmp.shown[0][0].kind, 'ab')

    def test_words_while_server_starts_then_its_answer(self):
        self.session._ready = False
        self.session.completion = labels('greenlet')
        self.edit_at_end(9)
        self.key('ENTER')
        self.defer_words = []
        jobs = []
        self.h.run_background = lambda work, done: jobs.append((work, done))
        self.type('gre')
        self.assertEqual(self.shown(), ['greet', 'greeting'])
        work, done = jobs.pop(-1)
        done(work())
        self.assertEqual(self.shown(), ['greenlet'])

    def test_no_server_completion_capability_means_words(self):
        self.session.has_completion = False
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('gre')
        self.assertEqual(self.session.completions, [])
        self.assertEqual(self.shown(), ['greet', 'greeting'])


class ScreenTest(CompletionTestBase):

    def cup(self, text):
        return [tuple(int(x) for x in m.groups())
                for m in re.finditer(r'\x1b\[(\d+);(\d+)H', text)]

    def open(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('gr')

    def test_list_under_the_word_with_matches_and_details(self):
        self.session.completion = {'items': [
            {'label': 'greet', 'kind': 3, 'labelDetails': {'detail': '(name)'},
             'detail': 'str'}]}
        self.open()
        out = self.screen()
        self.assertIn('ƒ ', out)
        self.assertIn('(name)', out)
        self.assertIn('str', out)
        caret = self.h._caret_cell()
        box = self.h._cmp_box
        self.assertEqual(box.row, caret[0] + 1)
        self.assertEqual(box.col + 4, caret[1] - 2)

    def test_list_flips_above_near_the_bottom(self):
        self.h.screen_size.rows = 14
        self.open()
        self.screen()
        box = self.h._cmp_box
        caret = self.h._caret_cell()
        self.assertLess(box.row, caret[0])

    def test_list_stays_inside_the_code_pane(self):
        self.session.completion = labels('gr' + 'x' * 80)
        self.open()
        self.screen()
        box = self.h._cmp_box
        self.assertLessEqual(box.col + box.width, self.h.screen_size.cols - 2)
        self.assertGreaterEqual(box.col, self.h.left_width() + 3)

    def test_click_on_an_item_accepts_it(self):
        self.open()
        self.screen()
        box = self.h._cmp_box
        for kind in (EventType.PRESS, EventType.RELEASE):
            self.h.on_mouse_event(MouseEvent(cell_x=box.col + 5, cell_y=box.row + 1,
                                             buttons=MouseButton.LEFT, type=kind))
        self.assertEqual(self.buf.lines[self.buf.line], 'greeting')

    def test_click_elsewhere_closes(self):
        self.open()
        self.screen()
        self.h.on_mouse_event(MouseEvent(cell_x=self.h.left_width() + 12, cell_y=3,
                                         buttons=MouseButton.LEFT, type=EventType.PRESS))
        self.assertIsNone(self.h._cmp)

    def test_documentation_of_the_selected_item(self):
        self.session.resolve_provider = True
        self.session.resolve = {'greet': {'documentation': {
            'kind': 'markdown', 'value': '```python\ndef greet(name)\n```\n---\nSay hello.'}}}
        self.open()
        self.assertNotIn('Say hello.', self.screen())
        self.assertEqual(self.session.resolved, [])
        self.key(' ', ctrl=True)
        out = self.screen()
        self.assertIn('Say hello.', out)
        self.assertIn('def greet(name)', out)
        self.key(' ', ctrl=True)
        self.assertNotIn('Say hello.', self.screen())

    def test_no_list_while_the_find_line_is_open(self):
        self.open()
        self.h.input_mode = 'search'
        self.assertNotIn('[complete]', self.screen())


class SignatureTest(CompletionTestBase):

    def setUp(self):
        super().setUp()
        self.session.signature = {
            'signatures': [{'label': '(name: str, times: int = 1) -> str',
                            'parameters': [{'label': [1, 10]}, {'label': [12, 26]}],
                            'activeParameter': 1}]}

    def test_paren_shows_the_signature_above_the_line(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('greet(')
        self.assertEqual(self.session.signatures[-1][2]['triggerCharacter'], '(')
        out = self.screen()
        self.assertIn('times: int = 1', out)

    def test_escape_closes_the_list_first_then_the_signature(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('greet(gr')
        self.assertTrue(self.visible())
        self.key('ESCAPE')
        self.assertFalse(self.visible())
        self.assertIsNotNone(self.h._sig)
        self.key('ESCAPE')
        self.assertIsNone(self.h._sig)
        self.assertTrue(self.h.editing)

    def test_empty_answer_hides_it(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('greet(')
        self.session.signature = None
        self.type(',')
        self.assertIsNone(self.h._sig)

    def test_leaving_the_line_hides_it(self):
        self.edit_at_end(9)
        self.key('ENTER')
        self.type('greet(')
        self.key('UP')
        self.assertIsNone(self.h._sig)


class ShiftThroughTest(unittest.TestCase):

    def test_lines_added_above(self):
        self.assertEqual(C.shift_through((5, 3), [((0, 9), (0, 9), '\nimport x')]), (6, 3))

    def test_replacement_on_the_same_line_before(self):
        self.assertEqual(C.shift_through((2, 10), [((2, 0), (2, 4), 'ab')]), (2, 8))

    def test_multiline_replacement_ending_on_the_line(self):
        edits = [((1, 5), (3, 2), 'X\nYY')]
        self.assertEqual(C.shift_through((3, 6), edits), (2, 6))

    def test_edits_after_are_ignored(self):
        self.assertEqual(C.shift_through((1, 1), [((4, 0), (4, 0), 'z\n')]), (1, 1))


if __name__ == '__main__':
    unittest.main()
