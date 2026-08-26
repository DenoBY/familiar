"""Ревью коммита в log: тот же экран, что в review, но источник —
снимок коммита, а не рабочее дерево.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from lspmock import FakePool, FakeSession, loc, sym

import kittymock  # noqa: F401
import log as L
from kittymock import run_threads_inline, wire
from modules.vcs.source import CommitSource


_ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


class CommitReviewTest(unittest.TestCase):

    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        self.repo = tempfile.mkdtemp(prefix='cclog_rev_')
        self._git('init', '-b', 'main')
        self.write('lib.py', 'def helper():\n    return 1\n')
        self.write('app.py', 'from lib import helper\n\n\ndef main():\n    return helper()\n')
        self._git('add', '-A')
        self._git('commit', '-m', 'init')
        self.write('lib.py', 'def helper():\n    return 2\n')
        self.write('app.py',
                   'from lib import helper\n\n\ndef main():\n    return helper() + 1\n')
        self._git('add', '-A')
        self._git('commit', '-m', 'tweak helper')
        # рабочее дерево уезжает вперёд: ревью коммита его игнорирует
        self.write('lib.py', 'def helper():\n    return 999\n')

        self.h = L.CommitLogHandler([], self.repo)
        wire(self.h, rows=30, cols=140)
        self.h.load_state()

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

    def open_head(self, rel='lib.py'):
        self.h.sel = 0
        self.h.open_commit()
        self.h.tsel = next(i for i, r in enumerate(self.h.rows)
                           if r['type'] == 'file' and self.h.filtered[r['idx']]['path'] == rel)
        self.h.load_diff()
        self.h.set_focus('diff')

    # --- источник ---

    def test_open_commit_switches_source(self):
        self.h.sel = 0
        self.h.open_commit()
        self.assertEqual(self.h.screen, 'diff')
        self.assertIsInstance(self.h.source, CommitSource)
        self.assertEqual(sorted(it['path'] for it in self.h.items), ['app.py', 'lib.py'])

    def test_diff_shows_commit_content_not_working_tree(self):
        self.open_head()
        self.assertIn('return 2', self.h.diff_after)
        self.assertNotIn('999', self.h.diff_after)

    # --- комментарии к строкам ---

    def test_comment_on_commit_line(self):
        self.open_head()
        self.h.start_comment()
        self.h.input_text('why not a constant?')
        self.h.commit_input()
        self.assertEqual(list(self.h.annots.values())[0]['text'], 'why not a constant?')

    def test_markdown_names_the_commit(self):
        self.open_head()
        self.h.start_comment()
        self.h.input_text('note')
        self.h.commit_input()
        head = self.h._review_markdown().splitlines()[0]
        self.assertIn(self.h.commit['short'], head)
        self.assertIn('tweak helper', head)

    def test_comments_dropped_when_another_commit_opened(self):
        self.open_head()
        self.h.start_comment()
        self.h.input_text('note')
        self.h.commit_input()
        self.h.back_to_commits()
        self.h.sel = 1
        self.h.open_commit()
        self.assertEqual(self.h.annots, {})

    def test_comments_kept_when_reopening_the_same_commit(self):
        self.open_head()
        self.h.start_comment()
        self.h.input_text('note')
        self.h.commit_input()
        self.h.back_to_commits()
        self.h.open_commit()
        self.assertEqual(len(self.h.annots), 1)

    # --- go-to-definition и поиск идут по снимку коммита ---

    def test_goto_definition_lands_on_commit_version(self):
        run_threads_inline(self)
        self.open_head('app.py')
        di = next(i for i, p in enumerate(self.h.diff_plain) if 'helper()' in p)
        self.h.diff_cur = di
        col = self.h.diff_plain[di].index('helper')
        session = FakeSession(syms={'helper': [sym('helper',
                                                   os.path.join(self.repo, 'lib.py'),
                                                   0)]})
        self.h._lsp = FakePool(session)
        self.h.goto_definition(self.h._doc_ref(di, col))
        cur = self.h.current_item()
        self.assertEqual((cur or {}).get('path') or self.h._external, 'lib.py')
        # показанное содержимое — из коммита, а не из рабочего дерева
        self.assertIn('return 2', self.h.diff_after)

    def test_commit_view_feeds_the_working_tree_not_the_snapshot(self):
        # текст из истории не совпадает с индексом сервера: подсунуть
        # его — испортить кэш. Зато рабочая версия файла у сервера уже
        # разобрана, и по ней можно спросить точно
        run_threads_inline(self)
        self.open_head('app.py')
        di = next(i for i, p in enumerate(self.h.diff_plain) if 'helper()' in p)
        col = self.h.diff_plain[di].index('helper')
        session = FakeSession(defs={('app.py', 5): [loc(
            os.path.join(self.repo, 'lib.py'), 0)]})
        self.h._lsp = FakePool(session)
        self.assertEqual(self.h._doc_ref(di, col).side, 'symbol')
        self.h.goto_definition(self.h._doc_ref(di, col))
        self.assertEqual(session.opened[0][0], 'app.py')
        with open(os.path.join(self.repo, 'app.py')) as f:
            self.assertEqual(session.opened[0][1], f.read())
        shown = self.h._external or self.h.current_item()['path']
        self.assertEqual(shown, 'lib.py')

    def test_locate_prefers_the_occurrence_near_the_shown_line(self):
        # рабочее дерево ушло вперёд: строка сдвинулась, но искать
        # надо рядом с ней, а не в первой попавшейся строке импорта
        from modules.vcs.goto import DiffRef, _locate
        text = ('from lib import helper\n'      # 1 — импорт
                'x = 1\n'
                'y = helper()\n'                # 3
                'z = 2\n'
                'w = helper()\n')               # 5
        ref = DiffRef('app.py', 'symbol', 5, 0, 'helper', '', text)
        self.assertEqual(_locate(text, ref)[0], 5)
        self.assertEqual(_locate(text, ref._replace(line=3))[0], 3)
        self.assertEqual(_locate(text, ref._replace(line=99))[0], 5)

    def test_jump_into_a_gitignored_file_shows_it_from_disk(self):
        # vendor/ в снимок коммита не попадает: без чтения с диска
        # вместо кода показалось бы «(empty file)»
        run_threads_inline(self)
        os.makedirs(os.path.join(self.repo, 'vendor'), exist_ok=True)
        self.write('vendor/stub.py', 'class Stringable:\n    pass\n')
        self.write('.gitignore', 'vendor/\n')
        self.open_head('app.py')
        di = next(i for i, p in enumerate(self.h.diff_plain) if 'helper()' in p)
        col = self.h.diff_plain[di].index('helper')
        session = FakeSession(syms={'helper': [sym('helper', os.path.join(
            self.repo, 'vendor/stub.py'), 0)]})
        self.h._lsp = FakePool(session)
        self.h.goto_definition(self.h._doc_ref(di, col))
        self.assertEqual(self.h._external, 'vendor/stub.py')
        self.assertIn('class Stringable', self.h.diff_after)

    def test_commit_view_falls_back_to_symbols_for_a_vanished_file(self):
        # файла в рабочем дереве больше нет — спрашивать по позиции
        # не о чем, остаётся поиск по имени
        run_threads_inline(self)
        self.open_head('app.py')
        di = next(i for i, p in enumerate(self.h.diff_plain) if 'helper()' in p)
        col = self.h.diff_plain[di].index('helper')
        os.remove(os.path.join(self.repo, 'app.py'))
        session = FakeSession(syms={'helper': [sym('helper', os.path.join(
            self.repo, 'lib.py'), 0)]})
        self.h._lsp = FakePool(session)
        self.h.goto_definition(self.h._doc_ref(di, col))
        self.assertEqual(session.asked, [], 'по позиции спрашивать было нечего')
        shown = self.h._external or self.h.current_item()['path']
        self.assertEqual(shown, 'lib.py')

    def test_ctrl_d_does_not_close_the_commit_list(self):
        # раньше на списке коммитов ⌃d работал как EOF и закрывал кит
        self.h.on_eot()
        self.assertEqual(self.h.quits, [])

    def test_find_in_files_searches_the_commit(self):
        self.h.sel = 0
        self.h.open_commit()
        self.h.toggle_find()
        self.h.input_text('helper')
        self.h._run_find()
        self.assertEqual(sorted(it['path'] for it in self.h.items), ['app.py', 'lib.py'])
        self.assertTrue(self.h.find_mode)

    def test_find_ignores_working_tree_only_matches(self):
        self.write('scratch.py', 'helper marker\n')
        self.h.sel = 0
        self.h.open_commit()
        self.h.toggle_find()
        self.h.input_text('helper')
        self.h._run_find()
        self.assertNotIn('scratch.py', [it['path'] for it in self.h.items])

    # --- клавиши и футер ---

    def test_tree_footer_offers_review_actions(self):
        self.h.sel = 0
        self.h.open_commit()
        foot = self.h._footer()
        for hint in ('⌘⇧f find', 'f filter', 'e edit', 'mark', 'Esc commits'):
            self.assertIn(hint, foot)

    def test_diff_footer_offers_comments_and_definitions(self):
        self.open_head()
        foot = self.h._footer()
        for hint in ('⌥/d def', 'w export', 'Enter/c comment'):
            self.assertIn(hint, foot)

    def test_tree_filter_narrows_commit_files(self):
        self.h.sel = 0
        self.h.open_commit()
        self.h.start_filter()
        self.h.input_text('lib')
        self.h.commit_input()
        self.assertEqual([it['path'] for it in self.h.filtered], ['lib.py'])

    def test_escape_from_review_returns_to_commits(self):
        self.h.sel = 0
        self.h.open_commit()
        self.h._review_key('ESCAPE')
        self.assertEqual(self.h.screen, 'commits')

    def test_stage_and_revert_stay_out_of_commit_review(self):
        self.open_head()
        self.assertFalse(self.h.source.mutable)
        self.assertNotIn('+ stage', self.h._footer())
        self.assertNotIn('- revert', self.h._footer())


if __name__ == '__main__':
    unittest.main()
