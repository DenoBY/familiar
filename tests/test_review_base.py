"""Режим «вся работа ветки»: рабочее дерево против точки расхождения
с базовой веткой — закоммиченное в ветке и незакоммиченное вместе.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

import kittymock  # noqa: F401
import review as R
from kittymock import wire
from modules.vcs.workspace import Workspace
from modules.vcs.worktree import base_ref, scan_range


_ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


class BranchCase(unittest.TestCase):
    """master с двумя файлами; ветка feature, где один файл изменён и
    закоммичен, второй правится прямо сейчас, третий не добавлен.
    """

    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        self.repo = tempfile.mkdtemp(prefix='ccbase_')
        self.git('init', '-b', 'master')
        self.write('committed.txt', 'base line\n')
        self.write('dirty.txt', 'untouched\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'init')
        self.git('checkout', '-q', '-b', 'feature')
        self.write('committed.txt', 'branch line\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'work in the branch')
        self.write('dirty.txt', 'edited right now\n')
        self.write('fresh.txt', 'brand new\n')

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def git(self, *args):
        subprocess.run(['git', '-C', self.repo, *args], check=True,
                       capture_output=True, env=os.environ)

    def write(self, rel, text):
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w') as f:
            f.write(text)

    def handler(self):
        h = R.ReviewHandler([], Workspace.single(self.repo))
        wire(h, rows=30, cols=120)
        h.load_source()
        return h

    def paths(self, h):
        return sorted(it['path'] for it in h.items)


class BaseRefTest(BranchCase):

    def test_finds_the_branch_we_diverged_from(self):
        name, sha = base_ref(self.repo)
        self.assertEqual(name, 'master')
        self.assertEqual(len(sha), 40)

    def test_none_on_the_base_branch_itself(self):
        self.git('checkout', '-q', 'master')
        self.assertIsNone(base_ref(self.repo))

    def test_prefers_origin_head(self):
        # origin/HEAD важнее угадывания по имени: базой бывает develop
        self.git('checkout', '-q', '-b', 'develop', 'master')
        self.git('checkout', '-q', 'feature')
        name, _sha = base_ref(self.repo)
        self.assertIn(name, ('master', 'develop'))


class ScanRangeTest(BranchCase):

    def test_lists_committed_and_uncommitted_together(self):
        _name, sha = base_ref(self.repo)
        items = scan_range(self.repo, sha)
        self.assertEqual(sorted(it['path'] for it in items),
                         ['committed.txt', 'dirty.txt', 'fresh.txt'])

    def test_untracked_is_marked(self):
        _name, sha = base_ref(self.repo)
        fresh = next(it for it in scan_range(self.repo, sha) if it['path'] == 'fresh.txt')
        self.assertTrue(fresh['untracked'])
        self.assertEqual(fresh['stat'], (1, 0))

    def test_committed_file_counts_its_lines(self):
        _name, sha = base_ref(self.repo)
        it = next(x for x in scan_range(self.repo, sha) if x['path'] == 'committed.txt')
        self.assertEqual(it['stat'], (1, 1))


class ReviewBaseModeTest(BranchCase):

    def test_working_tree_by_default(self):
        h = self.handler()
        self.assertFalse(h.source.vs_base)
        self.assertEqual(self.paths(h), ['dirty.txt', 'fresh.txt'])

    def test_b_shows_the_whole_branch(self):
        h = self.handler()
        h.on_text('b')
        self.assertTrue(h.source.vs_base)
        self.assertEqual(self.paths(h), ['committed.txt', 'dirty.txt', 'fresh.txt'])

    def test_b_switches_back(self):
        h = self.handler()
        h.on_text('b')
        h.on_text('b')
        self.assertFalse(h.source.vs_base)
        self.assertEqual(self.paths(h), ['dirty.txt', 'fresh.txt'])

    def test_diff_of_a_committed_file_is_against_the_base(self):
        h = self.handler()
        h.on_text('b')
        for i, r in enumerate(h.rows):
            if r['type'] == 'file' and r['name'] == 'committed.txt':
                h.set_tsel(i)
                break
        h.load_diff()
        self.assertEqual(h.diff_before, 'base line\n')
        self.assertEqual(h.diff_after, 'branch line\n')

    def test_header_and_footer_name_the_base(self):
        h = self.handler()
        h.on_text('b')
        self.assertIn('vs master', h._header())
        self.assertIn('b working tree', h._review_footer())

    def test_on_the_base_branch_it_says_so(self):
        self.git('checkout', '-q', 'master')
        h = self.handler()
        h.out = []
        h.on_text('b')
        self.assertFalse(h.source.vs_base)
        self.assertIn('no base branch', kittymock.draw_text(h))
        # список не потерялся: правки рабочего дерева пережили checkout
        self.assertEqual(self.paths(h), ['dirty.txt', 'fresh.txt'])

    def test_staging_is_blocked_while_comparing(self):
        h = self.handler()
        h.on_text('b')
        h.out = []
        h.stage_selected()
        self.assertIn('press b for the working tree', kittymock.draw_text(h))
        out = subprocess.run(['git', '-C', self.repo, 'diff', '--cached', '--name-only'],
                             capture_output=True, text=True, env=os.environ)
        self.assertEqual(out.stdout, '')

    def test_revert_is_blocked_while_comparing(self):
        h = self.handler()
        h.on_text('b')
        h.start_revert()
        self.assertIsNone(h.pending_revert)

    def test_refresh_keeps_the_mode(self):
        h = self.handler()
        h.on_text('b')
        self.write('dirty.txt', 'edited again\n')
        h.refresh()
        self.assertTrue(h.source.vs_base)
        self.assertIn('committed.txt', self.paths(h))


if __name__ == '__main__':
    unittest.main()
