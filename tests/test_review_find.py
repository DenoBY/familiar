import os
import shutil
import subprocess
import tempfile
import unittest

import kittymock  # noqa: F401
import review as R
from kittymock import KeyEvent, draw_text, wire


_ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


class ReviewFindModeTest(unittest.TestCase):
    """Режим Find in Files внутри review (Cmd+Shift+F)."""

    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        self.repo = tempfile.mkdtemp(prefix='ccfind_')
        self._git('init', '-b', 'main')
        self.write('app.py', 'top\nneedle one\nmiddle\nplain\nneedle two\n')
        self.write('docs/readme.md', 'needle in docs\n')
        self.write('vendor/x.py', 'needle in vendor\n')
        self._git('add', '-A')
        self._git('commit', '-m', 'init')
        # незакоммиченная правка — дерево ревью не пустое (для
        # проверки возврата из поиска); строки needle не двигаем
        self.write('docs/readme.md', 'needle in docs\nedited\n')
        self.write('notes.txt', 'needle untracked\n')

        self.h = R.ReviewHandler([], self.repo, self.repo)
        wire(self.h, rows=40, cols=120)
        self.h.load_source()

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
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w') as f:
            f.write(content)

    def enter_find(self, query=None):
        self.h.toggle_find()
        if query is not None:
            # ImmediateLoop выполняет debounce-таймер сразу —
            # поиск синхронный
            self.h.input_text(query)

    def _select_file(self, basename):
        for i, r in enumerate(self.h.rows):
            if r['type'] == 'file' and r['name'] == basename:
                self.h.tsel = i
                self.h.load_diff()
                return
        self.fail(f'файл {basename} не найден в дереве')

    # --- вход/выход ---

    def test_toggle_enters_and_leaves_mode(self):
        before_names = [r['name'] for r in self.h.rows]
        self.enter_find('needle')
        self.assertTrue(self.h.find_mode)
        self.assertEqual(self.h.view_mode, 'final')
        self.h.input_key('ENTER')
        self.h.toggle_find()
        self.assertFalse(self.h.find_mode)
        self.assertEqual([r['name'] for r in self.h.rows], before_names)
        self.assertEqual(self.h.view_mode, 'diff')

    def test_chord_toggles_mode(self):
        self.h.on_key(KeyEvent(key='f', super=True, shift=True))
        self.assertTrue(self.h.find_mode)
        self.assertEqual(self.h.input_mode, 'find')
        self.h.on_key(KeyEvent(key='f', super=True, shift=True))
        self.assertFalse(self.h.find_mode)

    def test_escape_cascade_leaves_mode_not_overlay(self):
        self.enter_find('needle')
        self.h.input_key('ENTER')
        self.h.on_key(KeyEvent(key='ESCAPE'))
        self.assertFalse(self.h.find_mode)
        self.assertEqual(self.h.quits, [])

    def test_exit_restores_review_state(self):
        self.h.filter_query = 'read'
        self.h.rebuild_tree()
        self.enter_find('needle')
        self.assertEqual(self.h.filter_query, '')
        self.h.input_key('ENTER')
        self.h.toggle_find()
        self.assertEqual(self.h.filter_query, 'read')
        rels = {it['rel'] for it in self.h.items}
        self.assertIn('docs/readme.md', rels)   # снова правки ревью

    # --- живой поиск и дерево ---

    def test_live_search_builds_tree(self):
        self.enter_find('needle')
        rels = sorted(it['rel'] for it in self.h.items)
        self.assertEqual(rels, ['app.py', 'docs/readme.md', 'notes.txt',
                                'vendor/x.py'])
        names = [r['name'] for r in self.h.rows]
        self.assertIn('app.py', names)
        # noise-каталоги скрыты из дерева, но не из items
        self.assertNotIn('vendor', names)
        self.assertEqual(self.h.n_files, 3)
        by_rel = {it['rel']: it for it in self.h.items}
        self.assertEqual(by_rel['app.py']['stat'], (2, None))

    def test_short_query_does_not_search(self):
        self.enter_find('n')
        self.assertEqual(self.h.items, [])
        self.assertIn('type to search', self.h._empty_pane_msg())

    def test_header_shows_query_and_matches(self):
        self.enter_find('needle')
        self.h.draw_screen()
        self.assertIn('‘needle’', draw_text(self.h))
        self.assertIn('4 matches in 3 files', draw_text(self.h))

    # --- правая панель ---

    def test_final_view_with_matches(self):
        self.enter_find('needle')
        self._select_file('app.py')
        lines = [self.h.diff_lineno[m] for m in self.h.search_matches]
        self.assertEqual(lines, [2, 5])
        # курсор — на первом совпадении, прыжки [ ] — по совпадениям
        self.assertEqual(self.h.diff_lineno[self.h.diff_cur], 2)
        self.assertEqual(self.h.diff_hunks, self.h.search_matches)
        # и после перестройки модели (resize, h/l) — тоже
        self.h.build_diff_rows()
        self.assertEqual(self.h.diff_hunks, self.h.search_matches)

    def test_search_next_moves_cursor_and_wraps(self):
        self.enter_find('needle')
        self._select_file('app.py')
        self.h.search_next(1)
        self.assertEqual(self.h.diff_lineno[self.h.diff_cur], 5)
        self.h.search_next(1)
        self.assertEqual(self.h.diff_lineno[self.h.diff_cur], 2)

    def test_view_mode_locked_to_final(self):
        self.enter_find('needle')
        self.h.toggle_view_mode()
        self.assertEqual(self.h.view_mode, 'final')

    # --- строка запроса ---

    def test_commit_and_reedit(self):
        self.enter_find('needle')
        self.h.input_key('ENTER')
        self.assertIsNone(self.h.input_mode)
        self.h.start_search()   # ⌘f в поиске — снова к запросу
        self.assertEqual(self.h.input_mode, 'find')
        self.assertEqual(self.h.input_buffer, 'needle')

    def test_escape_reverts_unfinished_edit(self):

        class DeferredLoop:
            """call_later не срабатывает — правка висит в debounce."""

            class Timer:
                cancelled = False

                def cancel(self):
                    self.cancelled = True

            def call_later(self, delay, callback, *args):
                self.timer = self.Timer()
                return self.timer

        self.enter_find('needle')
        self.h.input_key('ENTER')
        self.h.asyncio_loop = DeferredLoop()
        self.h.start_search()
        self.h.input_text('-tail')
        self.assertEqual(self.h.find_query, 'needle-tail')
        self.h.input_key('ESCAPE')
        self.assertEqual(self.h.find_query, 'needle')
        self.assertTrue(self.h.asyncio_loop.timer.cancelled)
        self.assertEqual(sorted(it['rel'] for it in self.h.items),
                         ['app.py', 'docs/readme.md', 'notes.txt', 'vendor/x.py'])

    def test_cmd_f_in_review_mode_starts_file_search(self):
        self.h.on_key(KeyEvent(key='f', super=True))
        self.assertEqual(self.h.input_mode, 'search')

    # --- regex ---

    def test_regex_toggle_reruns_search(self):
        self.enter_find('ne.dle')
        self.assertEqual(self.h.items, [])
        self.h.input_key('ENTER')
        self.h.on_text('x')
        self.assertTrue(self.h.find_regex)
        self.assertTrue(self.h.items)
        # совпадения идут по строкам git grep, не по подстроке
        self._select_file('app.py')
        self.assertEqual([self.h.diff_lineno[m] for m in self.h.search_matches],
                         [2, 5])

    def test_invalid_regex_shows_git_error(self):
        self.h.toggle_find()
        self.h.find_regex = True
        self.h.input_text('needle[')
        self.assertEqual(self.h.items, [])
        self.assertTrue(self.h.status)

    # --- read-only и редактор ---

    def test_review_actions_blocked(self):
        self.enter_find('needle')
        self.h.input_key('ENTER')
        self._select_file('app.py')
        self.h.focus = 'diff'
        self.h.start_comment()
        self.assertEqual(self.h.annots, {})
        self.h.stage_selected()
        # flash гаснет сразу после кадра — проверяем нарисованное
        self.assertIn('read-only (find in files)', draw_text(self.h))

    def test_enter_opens_changed_file_in_its_review_diff(self):
        self.enter_find('needle')
        self.h.input_key('ENTER')
        self._select_file('readme.md')   # есть в правках ревью
        self.h.set_focus('diff')
        self.h.on_key(KeyEvent(key='ENTER'))
        self.assertFalse(self.h.find_mode)
        self.assertIsNone(self.h._external)
        self.assertEqual(self.h.current_item()['rel'], 'docs/readme.md')
        # полный функционал ревью: строка комментируема
        self.assertTrue(self.h._commentable(self.h.diff_cur))

    def test_enter_opens_unchanged_file_like_goto_definition(self):
        self.enter_find('needle')
        self.h.input_key('ENTER')
        self._select_file('app.py')      # в ревью не изменён
        self.h.set_focus('diff')
        self.h.search_next(1)            # второе совпадение — строка 5
        self.h.on_key(KeyEvent(key='ENTER'))
        self.assertFalse(self.h.find_mode)
        self.assertEqual(self.h._external, 'app.py')
        self.assertEqual(self.h.diff_lineno[self.h.diff_cur], 5)
        self.h.nav_back()                # ⌃o — назад в ревью
        self.assertIsNone(self.h._external)

    def test_open_editor_terminal_at_match_line(self):
        os.environ['EDITOR'] = 'vim'
        try:
            self.enter_find('needle')
            self.h.input_key('ENTER')
            self._select_file('app.py')
            self.h.search_next(1)
            self.h.open_editor()
        finally:
            os.environ.pop('EDITOR', None)
        self.assertEqual(self.h.quits, [0])
        self.assertEqual(self.h.action['action'], 'edit')
        self.assertEqual(self.h.action['line'], 5)
        self.assertTrue(self.h.action['path'].endswith('app.py'))


if __name__ == '__main__':
    unittest.main()
