import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import kittymock  # noqa: F401
from modules.review import grep as G
from modules.vcs.git import last_error


_ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


class SearchFilesTest(unittest.TestCase):
    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        self.repo = tempfile.mkdtemp(prefix='ccgrep_')
        self._git('init', '-b', 'main')
        self.write('a.py', 'def alpha():\n    return BETA\n')
        self.write('sub/b.txt', 'beta beta\nother\nbeta again\n')
        self.write('.gitignore', 'ignored.txt\n')
        self.write('ignored.txt', 'beta hidden\n')
        with open(os.path.join(self.repo, 'blob.bin'), 'wb') as f:
            f.write(b'beta\x00binary')
        self._git('add', '-A')
        self._git('commit', '-m', 'init')
        self.write('new.txt', 'beta untracked\n')

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

    def _rels(self, items):
        return sorted(it['rel'] for it in items)

    def test_smart_case_finds_all_registers(self):
        items, truncated = G.search_files(self.repo, 'beta')
        self.assertFalse(truncated)
        self.assertEqual(self._rels(items), ['a.py', 'new.txt', 'sub/b.txt'])

    def test_untracked_found_ignored_and_binary_skipped(self):
        rels = self._rels(G.search_files(self.repo, 'beta')[0])
        self.assertIn('new.txt', rels)
        self.assertNotIn('ignored.txt', rels)
        self.assertNotIn('blob.bin', rels)

    def test_uppercase_query_is_case_sensitive(self):
        items, _ = G.search_files(self.repo, 'BETA')
        self.assertEqual(self._rels(items), ['a.py'])

    def test_lines_and_stat(self):
        items, _ = G.search_files(self.repo, 'beta')
        by_rel = {it['rel']: it for it in items}
        b = by_rel['sub/b.txt']
        # совпадение — строка (не вхождение): двойное beta в первой
        # строке даёт одну запись
        self.assertEqual(b['lines'], [(1, 'beta beta'), (3, 'beta again')])
        self.assertEqual(b['stat'], (2, None))
        self.assertEqual(b['kind'], 'match')

    def test_regex_mode(self):
        items, _ = G.search_files(self.repo, 'be+ta', regex=True)
        self.assertEqual(self._rels(items), ['a.py', 'new.txt', 'sub/b.txt'])
        # literal-режим ищет этот же текст дословно — совпадений нет
        self.assertEqual(G.search_files(self.repo, 'be+ta')[0], [])

    def test_invalid_regex_reports_error(self):
        items, truncated = G.search_files(self.repo, '[', regex=True)
        self.assertEqual(items, [])
        self.assertFalse(truncated)
        self.assertTrue(last_error())

    def test_no_matches_is_not_an_error(self):
        items, truncated = G.search_files(self.repo, 'nothing-like-this')
        self.assertEqual(items, [])
        self.assertFalse(truncated)
        self.assertEqual(last_error(), '')

    def test_empty_query(self):
        self.assertEqual(G.search_files(self.repo, ''), ([], False))

    def test_truncated_at_cap(self):
        self.write('many.txt', 'beta\n' * 10)
        with patch.object(G, 'MAX_MATCHES', 3):
            items, truncated = G.search_files(self.repo, 'beta')
        self.assertTrue(truncated)
        self.assertEqual(sum(len(it['lines']) for it in items), 3)


if __name__ == '__main__':
    unittest.main()
