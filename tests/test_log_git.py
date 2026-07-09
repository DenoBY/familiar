import os
import shutil
import subprocess
import tempfile
import unittest

import kittymock  # noqa: F401
import modules.log.git as G


_ENV = {
    'GIT_AUTHOR_NAME': 'Alice', 'GIT_AUTHOR_EMAIL': 'a@e',
    'GIT_COMMITTER_NAME': 'Alice', 'GIT_COMMITTER_EMAIL': 'a@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


class LogGitTest(unittest.TestCase):
    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        self.repo = tempfile.mkdtemp(prefix='cclog_git_')
        self._git('init', '-b', 'main')
        # c1 (корневой): добавлены два файла
        self.write('a.txt', 'a1\na2\na3\n')
        self.write('dir/b.txt', 'b1\n')
        self._git('add', '-A')
        self._commit('first')
        # c2: правка a.txt, новый c.txt
        self.write('a.txt', 'a1\na2 changed\na3\na4\n')
        self.write('c.txt', 'c1\n')
        self._git('add', '-A')
        self._commit('second')
        # ветка feature с ещё одним коммитом (для --all)
        self._git('checkout', '-q', '-b', 'feature')
        self.write('d.txt', 'd1\n')
        self._git('add', '-A')
        self._commit('on feature')
        self._git('checkout', '-q', 'main')

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

    def _commit(self, msg):
        self._git('commit', '-m', msg)

    def write(self, rel, content):
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w') as f:
            f.write(content)

    @staticmethod
    def by_path(items):
        return {it['path']: it for it in items}

    # --- load_commits ---

    def test_load_commits_current_branch(self):
        cs = G.load_commits(self.repo)
        subjects = [c['subject'] for c in cs]
        self.assertEqual(subjects, ['second', 'first'])   # newest first, без feature
        self.assertEqual(len(cs[0]['sha']), 40)
        self.assertTrue(cs[0]['short'])
        self.assertEqual(cs[0]['author'], 'Alice')
        self.assertTrue(cs[0]['date'])

    def test_load_commits_all_branches(self):
        subjects = [c['subject'] for c in G.load_commits(self.repo, all_branches=True)]
        self.assertIn('on feature', subjects)             # ветка feature видна
        self.assertIn('second', subjects)

    def test_load_commits_excludes_stash(self):
        self.write('a.txt', 'dirty change\n')
        self._git('stash')                                # создаём stash (a.txt изменён)
        subjects = [c['subject'] for c in G.load_commits(self.repo, all_branches=True)]
        self.assertFalse([s for s in subjects
                          if s.startswith(('WIP on', 'On ', 'index on'))])

    def test_regular_commits_not_marked_merge(self):
        self.assertTrue(all(not c['merge'] for c in G.load_commits(self.repo)))

    def test_parse_refs(self):
        # --decorate=full: полные пути
        self.assertEqual(
            G.parse_refs('HEAD -> refs/heads/main, refs/remotes/origin/main, '
                         'tag: refs/tags/v1.0'),
            [('main', 'head'), ('origin/main', 'remote'), ('v1.0', 'tag')])
        # локальная ветка со слэшем НЕ путается с удалённой
        self.assertEqual(
            G.parse_refs('refs/heads/feature/x, refs/remotes/origin/feature/x'),
            [('feature/x', 'branch'), ('origin/feature/x', 'remote')])
        self.assertEqual(G.parse_refs('refs/remotes/origin/HEAD'), [])  # символическая
        self.assertEqual(G.parse_refs('HEAD'), [('HEAD', 'head')])
        self.assertEqual(G.parse_refs(''), [])

    def test_load_commits_refs_on_head(self):
        top = G.load_commits(self.repo)[0]
        self.assertIn(('main', 'head'), top['refs'])       # HEAD-ветка помечена
        older = G.load_commits(self.repo)[1]
        self.assertEqual(older['refs'], [])                # на старом коммите ссылок нет

    def test_load_commits_tag_ref(self):
        self._git('tag', 'v1.0')
        top = G.load_commits(self.repo)[0]
        self.assertIn(('v1.0', 'tag'), top['refs'])

    def test_merge_commit_marked(self):
        self._git('merge', '--no-ff', '-m', 'merge feature', 'feature')
        top = G.load_commits(self.repo)[0]
        self.assertEqual(top['subject'], 'merge feature')
        self.assertTrue(top['merge'])                     # два родителя → merge

    def test_load_commits_limit_and_skip(self):
        first_page = G.load_commits(self.repo, limit=1)
        self.assertEqual([c['subject'] for c in first_page], ['second'])
        second_page = G.load_commits(self.repo, limit=1, skip=1)
        self.assertEqual([c['subject'] for c in second_page], ['first'])

    def test_load_commits_not_a_repo(self):
        d = tempfile.mkdtemp()
        try:
            self.assertEqual(G.load_commits(d), [])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # --- commit_files ---

    def test_commit_files_root_commit(self):
        root_sha = G.load_commits(self.repo, limit=99)[-1]['sha']
        self.assertEqual(G.first_parent(self.repo, root_sha), G.EMPTY_TREE)
        files = self.by_path(G.commit_files(self.repo, root_sha))
        self.assertEqual(set(files), {'a.txt', 'dir/b.txt'})
        self.assertEqual(files['a.txt']['kind'], 'added')
        self.assertEqual(files['a.txt']['stat'], (3, 0))

    def test_commit_files_second_commit(self):
        head = G.load_commits(self.repo)[0]['sha']
        files = self.by_path(G.commit_files(self.repo, head))
        self.assertEqual(files['a.txt']['kind'], 'modified')
        self.assertEqual(files['c.txt']['kind'], 'added')
        self.assertEqual(files['a.txt']['rel'], 'a.txt')

    # --- commit_contents ---

    def test_commit_contents_modified(self):
        head = G.load_commits(self.repo)[0]['sha']
        it = self.by_path(G.commit_files(self.repo, head))['a.txt']
        before, after = G.commit_contents(self.repo, head, it)
        self.assertEqual(before, 'a1\na2\na3\n')
        self.assertEqual(after, 'a1\na2 changed\na3\na4\n')

    def test_commit_contents_added_has_empty_before(self):
        head = G.load_commits(self.repo)[0]['sha']
        it = self.by_path(G.commit_files(self.repo, head))['c.txt']
        before, after = G.commit_contents(self.repo, head, it)
        self.assertEqual(before, '')
        self.assertEqual(after, 'c1\n')

    # --- fetch ---

    def test_fetch_pulls_remote_commits(self):
        other = tempfile.mkdtemp(prefix='cclog_remote_')
        try:
            subprocess.run(['git', '-C', other, 'init', '-q', '-b', 'main'],
                           check=True, capture_output=True, env=os.environ)
            with open(os.path.join(other, 'r.txt'), 'w') as fh:
                fh.write('remote\n')
            subprocess.run(['git', '-C', other, 'add', '-A'], check=True,
                           capture_output=True, env=os.environ)
            subprocess.run(['git', '-C', other, 'commit', '-m', 'remote work'],
                           check=True, capture_output=True, env=os.environ)
            self._git('remote', 'add', 'origin', other)
            self.assertTrue(G.fetch(self.repo))
            subs = [c['subject'] for c in G.load_commits(self.repo, all_branches=True)]
            self.assertIn('remote work', subs)            # коммит с origin подтянут
        finally:
            shutil.rmtree(other, ignore_errors=True)

    # --- commit_detail ---

    def test_commit_detail(self):
        head = G.load_commits(self.repo)[0]['sha']
        d = G.commit_detail(self.repo, head)
        self.assertTrue(d['body'].startswith('second'))   # полное сообщение
        self.assertEqual(d['author_email'], 'a@e')
        self.assertIn('main', d['branches'])               # ветка, содержащая коммит
        self.assertNotIn('->', ' '.join(d['branches']))    # без символической origin/HEAD

    def test_commit_detail_body_with_separator_char(self):
        # \x1e в теле коммита не должен ломать разбор полей
        # (rsplit по трём последним)
        self.write('sep.txt', 's\n')
        self._git('add', '-A')
        self._commit('subject\n\nbody with \x1e inside')
        head = G.load_commits(self.repo)[0]['sha']
        d = G.commit_detail(self.repo, head)
        self.assertIn('\x1e inside', d['body'])
        self.assertEqual(d['author_email'], 'a@e')

    # --- unpushed_shas ---

    def test_unpushed_no_remote_is_empty(self):
        self.assertEqual(G.unpushed_shas(self.repo), set())

    def test_unpushed_after_push(self):
        remote = tempfile.mkdtemp(prefix='cclog_remote_')
        try:
            subprocess.run(['git', 'init', '--bare', '-q', remote], check=True,
                           capture_output=True, env=os.environ)
            self._git('remote', 'add', 'origin', remote)
            self._git('push', '-q', 'origin', '--all')   # main и feature
            pushed = {c['sha'] for c in G.load_commits(self.repo)}
            self.assertEqual(G.unpushed_shas(self.repo), set())

            self.write('e.txt', 'e1\n')
            self._git('add', '-A')
            self._commit('unpushed local')
            new_sha = G.load_commits(self.repo)[0]['sha']
            unpushed = G.unpushed_shas(self.repo)
            self.assertIn(new_sha, unpushed)
            self.assertTrue(unpushed.isdisjoint(pushed))
        finally:
            shutil.rmtree(remote, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
