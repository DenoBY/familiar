import os
import shutil
import tempfile
import subprocess
import unittest

import kittymock  # noqa: F401
import modules.review.git as G

_ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


class GitRepoTest(unittest.TestCase):
    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        self.repo = tempfile.mkdtemp(prefix='ccrev_git_')
        self._git('init', '-b', 'main')
        self.write('a.txt', 'a1\na2\na3\n')
        self.write('dir/b.txt', 'b1\n')
        self._git('add', '-A')
        self._git('commit', '-m', 'init')

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

    @staticmethod
    def by_path(items):
        return {it['path']: it for it in items}

    # --- примитивы ---

    def test_git_root(self):
        self.assertEqual(G.git_root(self.repo), os.path.realpath(self.repo))

    def test_git_root_outside_repo_is_none(self):
        d = tempfile.mkdtemp(prefix='notrepo_')
        try:
            self.assertIsNone(G.git_root(d))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_run_git_failure_returns_none(self):
        self.assertIsNone(G.run_git('/no/such/repo', 'status'))

    def test_has_head(self):
        self.assertTrue(G.has_head(self.repo))

    def test_read_text(self):
        self.assertEqual(G.read_text(os.path.join(self.repo, 'a.txt')), 'a1\na2\na3\n')
        self.assertEqual(G.read_text(os.path.join(self.repo, 'missing')), '')

    def test_detect_base_main(self):
        self.assertEqual(G.detect_base(self.repo), 'main')

    # --- working scope ---

    def test_working_modified_untracked_deleted(self):
        self.write('a.txt', 'a1\na2 mod\na3\n')       # 1 строка изменена
        self.write('c.txt', 'c1\nc2\n')               # новый untracked
        os.remove(os.path.join(self.repo, 'dir', 'b.txt'))
        items = self.by_path(G.scan_changes(self.repo, 'working', 'main'))

        self.assertEqual(set(items), {'a.txt', 'c.txt', 'dir/b.txt'})
        self.assertEqual(items['a.txt']['kind'], 'modified')
        self.assertEqual(items['a.txt']['stat'], (1, 1))
        self.assertEqual(items['c.txt']['kind'], 'untracked')
        self.assertTrue(items['c.txt']['untracked'])
        self.assertEqual(items['c.txt']['stat'], (2, 0))
        self.assertEqual(items['dir/b.txt']['kind'], 'deleted')
        self.assertEqual(items['dir/b.txt']['stat'], (0, 1))

    def test_working_sorted_by_path(self):
        self.write('z.txt', 'z\n')
        self.write('a2.txt', 'x\n')
        paths = [it['path'] for it in G.scan_changes(self.repo, 'working', 'main')]
        self.assertEqual(paths, sorted(paths))

    # --- staged scope ---

    def test_staged_modified(self):
        self.write('a.txt', 'a1\na2\na3\na4\n')       # +1 строка
        self._git('add', 'a.txt')
        items = self.by_path(G.scan_changes(self.repo, 'staged', 'main'))
        self.assertIn('a.txt', items)
        self.assertEqual(items['a.txt']['kind'], 'modified')
        self.assertEqual(items['a.txt']['stat'], (1, 0))

    def test_staged_rename(self):
        self._git('mv', 'a.txt', 'renamed.txt')
        items = self.by_path(G.scan_changes(self.repo, 'staged', 'main'))
        self.assertIn('renamed.txt', items)
        self.assertEqual(items['renamed.txt']['kind'], 'renamed')
        self.assertEqual(items['renamed.txt']['orig'], 'a.txt')

    # --- branch scope ---

    def test_branch_diff_vs_base(self):
        self._git('checkout', '-b', 'feature')
        self.write('a.txt', 'a1\nCHANGED\na3\n')
        self._git('add', '-A')
        self._git('commit', '-m', 'feature change')
        items = self.by_path(G.scan_changes(self.repo, 'branch', 'main'))
        self.assertIn('a.txt', items)
        self.assertEqual(items['a.txt']['kind'], 'modified')


if __name__ == '__main__':
    unittest.main()
