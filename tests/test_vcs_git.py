"""Общий git-слой (modules.vcs.git): запуск git и разбор вывода.

Ниже review-специфики: этими примитивами пользуются и review, и log.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

import kittymock  # noqa: F401
import modules.vcs.git as G


_ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


class GitPrimitivesTest(unittest.TestCase):
    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        self.repo = tempfile.mkdtemp(prefix='vcsgit_')
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


    # --- потоковый вывод ---

    def test_git_lines_yields_lines(self):
        lines = list(G.git_lines(self.repo, 'ls-files'))
        self.assertIn('a.txt', lines)
        self.assertIn('dir/b.txt', lines)

    def test_git_lines_stops_early_without_error(self):
        # обрыв итерации убивает git; прерванный так процесс не
        # должен выглядеть сбоем — иначе Find in Files показал бы
        # ошибку вместо усечённого результата
        G.set_error('')
        gen = G.git_lines(self.repo, 'ls-files')
        first = next(gen)
        gen.close()
        self.assertTrue(first)
        self.assertEqual(G.last_error(), '')

    def test_git_lines_reports_failure(self):
        self.assertEqual(list(G.git_lines(self.repo, 'no-such-command')), [])
        self.assertTrue(G.last_error())


if __name__ == '__main__':
    unittest.main()
