import os
import shutil
import subprocess
import tempfile
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

    def test_last_error_captured_and_cleared(self):
        self.assertIsNone(G.run_git(self.repo, 'rev-parse', 'no-such-ref'))
        self.assertTrue(G.last_error())
        self.assertIsNotNone(G.run_git(self.repo, 'status'))
        self.assertEqual(G.last_error(), '')

    def test_has_head(self):
        self.assertTrue(G.has_head(self.repo))

    def test_read_text(self):
        self.assertEqual(G.read_text(os.path.join(self.repo, 'a.txt')), 'a1\na2\na3\n')
        self.assertEqual(G.read_text(os.path.join(self.repo, 'missing')), '')

    # --- изменения рабочего дерева ---

    def test_working_modified_untracked_deleted(self):
        self.write('a.txt', 'a1\na2 mod\na3\n')       # 1 строка изменена
        self.write('c.txt', 'c1\nc2\n')               # новый untracked
        os.remove(os.path.join(self.repo, 'dir', 'b.txt'))
        items = self.by_path(G.scan_changes(self.repo))

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
        paths = [it['path'] for it in G.scan_changes(self.repo)]
        self.assertEqual(paths, sorted(paths))

    def test_rename(self):
        self._git('mv', 'a.txt', 'renamed.txt')
        items = self.by_path(G.scan_changes(self.repo))
        self.assertIn('renamed.txt', items)
        self.assertEqual(items['renamed.txt']['kind'], 'renamed')
        self.assertEqual(items['renamed.txt']['orig'], 'a.txt')

    def test_rename_in_subdir_keeps_stat(self):
        # numstat даёт "dir/{b.txt => c.txt}" — путь должен
        # совпасть с name-status
        self._git('mv', 'dir/b.txt', 'dir/c.txt')
        self.write('dir/c.txt', 'b1\nb2\n')
        self._git('add', 'dir/c.txt')
        items = self.by_path(G.scan_changes(self.repo))
        self.assertIn('dir/c.txt', items)
        self.assertEqual(items['dir/c.txt']['stat'], (1, 0))

    def test_repo_without_commits(self):
        bare = tempfile.mkdtemp(prefix='ccrev_nohead_')
        try:
            subprocess.run(['git', '-C', bare, 'init', '-b', 'main'], check=True,
                           capture_output=True, env=os.environ)
            with open(os.path.join(bare, 'n.txt'), 'w') as f:
                f.write('n1\n')
            subprocess.run(['git', '-C', bare, 'add', 'n.txt'], check=True,
                           capture_output=True, env=os.environ)
            items = self.by_path(G.scan_changes(bare))
            self.assertIn('n.txt', items)
            self.assertEqual(items['n.txt']['kind'], 'added')
        finally:
            shutil.rmtree(bare, ignore_errors=True)

    def test_staged_then_deleted_is_hidden(self):
        # AD: новый файл застейджен и удалён с диска — относительно
        # HEAD изменений нет, в дереве ему нечего делать
        self.write('ghost.txt', 'g1\n')
        self._git('add', 'ghost.txt')
        os.remove(os.path.join(self.repo, 'ghost.txt'))
        os.remove(os.path.join(self.repo, 'dir', 'b.txt'))
        items = self.by_path(G.scan_changes(self.repo))
        self.assertNotIn('ghost.txt', items)
        # настоящее удаление отслеживаемого файла остаётся видно
        self.assertEqual(items['dir/b.txt']['kind'], 'deleted')

    def test_untracked_noise_has_no_stat(self):
        self.write('venv/big.py', 'x\n' * 100)
        items = self.by_path(G.scan_changes(self.repo))
        self.assertIn('venv/big.py', items)
        self.assertIsNone(items['venv/big.py']['stat'])


if __name__ == '__main__':
    unittest.main()
