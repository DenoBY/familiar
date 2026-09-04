"""Рабочая область: поиск репозиториев в папке над ними и веер по ним.

Здесь же общая фикстура MultiRepoCase — её берут остальные
мультирепо-тесты (как kittymock, импортом соседнего модуля).
"""

import os
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock

import kittymock  # noqa: F401
from modules.vcs.git import run_git
from modules.vcs.workspace import (
    Repo,
    Workspace,
    by_repo,
    discover_repos,
    map_repos,
    open_workspace,
)


_ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


class MultiRepoCase(unittest.TestCase):
    """Папка base с двумя настоящими репозиториями (api, web) и всем,
    что искать не надо: шум, скрытые папки, слишком глубокое и
    вложенное в уже найденный репозиторий.
    """

    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        self.base = tempfile.mkdtemp(prefix='ccws_')
        self.api = self.make_repo('api', 'main')
        self.web = self.make_repo('web', 'feat')
        self.write(os.path.join(self.base, 'README.md'), 'top\n')
        for junk in ('node_modules/pkg', 'vendor/lib', '.hidden',
                     'deep/nested/repo', 'api/sub'):
            self.make_repo(junk)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def make_repo(self, rel: str, branch: str = 'main') -> str:
        root = os.path.join(self.base, rel)
        os.makedirs(root, exist_ok=True)
        self.git(root, 'init', '-b', branch)
        self.write(os.path.join(root, 'a.txt'), 'a1\na2\n')
        self.git(root, 'add', '-A')
        self.git(root, 'commit', '-m', 'init')
        return root

    @staticmethod
    def git(root: str, *args: str) -> None:
        subprocess.run(['git', '-C', root, *args], check=True,
                       capture_output=True, env=os.environ)

    @staticmethod
    def write(path: str, text: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(text)

    def workspace(self) -> Workspace:
        return open_workspace(self.base)


class DiscoverTest(MultiRepoCase):

    def test_finds_repos_in_subfolders(self):
        self.assertEqual([r.name for r in discover_repos(self.base)], ['api', 'web'])

    def test_skips_noise_and_hidden(self):
        names = [r.name for r in discover_repos(self.base)]
        for junk in ('node_modules/pkg', 'vendor/lib', '.hidden'):
            self.assertNotIn(junk, names)

    def test_does_not_descend_into_found_repo(self):
        self.assertNotIn('api/sub', [r.name for r in discover_repos(self.base)])

    def test_respects_depth(self):
        # deep/nested/repo лежит на третьем уровне: глубина 2
        # его не берёт
        self.assertNotIn('deep/nested/repo', [r.name for r in discover_repos(self.base)])
        deep = discover_repos(self.base, depth=3)
        self.assertIn('deep/nested/repo', [r.name for r in deep])

    def test_respects_limit(self):
        self.assertEqual(len(discover_repos(self.base, limit=1)), 1)

    def test_workspace_reports_truncation(self):
        from modules.vcs import workspace as W
        with unittest.mock.patch.object(W, 'MAX_REPOS', 1):
            ws = open_workspace(self.base)
        self.assertEqual(len(ws.repos), 1)
        self.assertTrue(ws.truncated)

    def test_workspace_without_truncation(self):
        self.assertFalse(self.workspace().truncated)

    def test_symlink_loop_does_not_hang(self):
        os.symlink(self.base, os.path.join(self.base, 'loop'))
        self.assertEqual([r.name for r in discover_repos(self.base)], ['api', 'web'])

    def test_missing_folder_is_empty(self):
        self.assertEqual(discover_repos(os.path.join(self.base, 'nope')), [])


class WorkspaceTest(MultiRepoCase):

    def test_inside_repo_is_single(self):
        ws = open_workspace(os.path.join(self.api, 'sub'))
        self.assertFalse(ws.multi)
        # git отдаёт корень развёрнутым (на macOS /var →
        # /private/var)
        self.assertEqual(ws.single_root, os.path.realpath(os.path.join(self.api, 'sub')))

    def test_folder_over_repos_is_multi(self):
        ws = self.workspace()
        self.assertTrue(ws.multi)
        self.assertEqual(ws.base, self.base)
        self.assertIsNone(ws.single_root)

    def test_rel_prefix_points_from_base(self):
        ws = self.workspace()
        self.assertEqual(ws.rel_prefix(self.api), 'api/')
        self.assertEqual(ws.name_of(self.web), 'web')

    def test_single_repo_has_no_prefix(self):
        ws = Workspace.single(self.api)
        self.assertEqual(ws.rel_prefix(self.api), '')
        self.assertEqual(ws.rel_prefix(None), '')

    def test_lone_repo_in_subfolder_keeps_prefix(self):
        # репозиторий один, но лежит не в самой базе: @path без
        # префикса Claude Code из базы не открыл бы
        ws = Workspace(self.base, [Repo(self.api, 'api')])
        self.assertFalse(ws.multi)
        self.assertEqual(ws.rel_prefix(self.api), 'api/')

    def test_sub_keeps_the_name_and_the_base(self):
        # экран одного репозитория (ревью коммита) не должен терять
        # имя: по нему собирается @path от базы
        sub = self.workspace().sub(self.api)
        self.assertFalse(sub.multi)
        self.assertEqual(sub.base, self.base)
        self.assertEqual(sub.rel_prefix(None), 'api/')

    def test_empty_workspace(self):
        empty = tempfile.mkdtemp(prefix='ccws_empty_')
        try:
            ws = open_workspace(empty)
            self.assertEqual(ws.repos, [])
            self.assertIsNone(ws.single_root)
        finally:
            shutil.rmtree(empty, ignore_errors=True)


class MapReposTest(MultiRepoCase):

    def test_keeps_order_of_repos(self):
        repos = self.workspace().repos
        out = map_repos(repos, lambda r: r.name)
        self.assertEqual([res for _repo, res, _err in out], ['api', 'web'])

    def test_failing_worker_does_not_break_others(self):
        repos = self.workspace().repos

        def work(repo):
            if repo.name == 'api':
                raise OSError('boom')
            return repo.name

        out = map_repos(repos, work)
        self.assertEqual([(res, bool(err)) for _repo, res, err in out],
                         [(None, True), ('web', False)])

    def test_worker_failing_with_any_exception_still_returns_a_triple(self):
        # результат распаковывают все потребители: дыра вместо
        # кортежа уронила бы кит целиком
        out = map_repos(self.workspace().repos,
                        lambda r: 1 / 0 if r.name == 'api' else r.name)
        self.assertEqual([(res, bool(err)) for _repo, res, err in out],
                         [(None, True), ('web', False)])

    def test_git_error_of_a_worker_reaches_the_caller(self):
        # last_error() принадлежит потоку воркера: вызвавший веер
        # иначе увидел бы «просто пусто»
        out = map_repos(self.workspace().repos,
                        lambda r: run_git(r.root, 'cat-file', '-p', 'deadbeef'))
        self.assertEqual([res for _repo, res, _err in out], [None, None])
        self.assertTrue(all(err for _repo, _res, err in out))

    def test_empty_list(self):
        self.assertEqual(map_repos([], lambda r: r), [])

    def test_workers_are_daemons(self):
        # закрытие кита посреди сетевого fetch не должно ждать веер
        import threading
        seen = []
        map_repos(self.workspace().repos,
                  lambda r: seen.append(threading.current_thread().daemon))
        self.assertEqual(seen, [True, True])


class ByRepoTest(unittest.TestCase):

    def test_groups_items(self):
        items = [{'repo': '/a', 'path': 'x'}, {'repo': '/b', 'path': 'y'},
                 {'repo': '/a', 'path': 'z'}]
        self.assertEqual({k: [it['path'] for it in v] for k, v in by_repo(items).items()},
                         {'/a': ['x', 'z'], '/b': ['y']})

    def test_items_without_repo_share_one_group(self):
        self.assertEqual(list(by_repo([{'path': 'x'}, {'path': 'y'}])), [''])


if __name__ == '__main__':
    unittest.main()
