import os
import shutil
import tempfile
import unittest

import kittymock  # noqa: F401
from modules.lsp import registry as R


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='lspreg_')
        self.repo = os.path.join(self.dir, 'repo')
        os.makedirs(self.repo)
        self._backup = {k: os.environ.get(k)
                        for k in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME')}
        os.environ['XDG_CONFIG_HOME'] = os.path.join(self.dir, 'config')
        os.environ['XDG_CACHE_HOME'] = os.path.join(self.dir, 'cache')
        R.reset_cache()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        for key, value in self._backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        R.reset_cache()

    def write_user(self, text: str) -> None:
        path = R.user_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        R.reset_cache()

    def write_project(self, text: str) -> None:
        path = R.project_path(self.repo)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        R.reset_cache()

    # --- разбор ---

    def test_parse_blocks_and_fields(self):
        blocks = R.parse('server php\n  command x --stdio\n  extensions .php\n')
        self.assertEqual(blocks['php']['command'], [['x', '--stdio']])

    def test_parse_ignores_comments_and_blanks(self):
        blocks = R.parse('# note\n\nserver go\n  command gopls # inline\n')
        self.assertEqual(blocks['go']['command'], [['gopls']])

    def test_parse_field_outside_block_ignored(self):
        self.assertEqual(R.parse('command orphan\nserver go\n  command gopls\n'),
                         {'go': {'command': [['gopls']]}})

    def test_repeated_field_accumulates(self):
        blocks = R.parse('server go\n  initopt a.b 1\n  initopt c 2\n')
        self.assertEqual(len(blocks['go']['initopt']), 2)

    # --- встроенный реестр ---

    def test_builtin_registry_loads(self):
        self.assertIn('php', R.load())
        self.assertIn('go', R.load())

    def test_disabled_block_hidden(self):
        self.assertNotIn('php-phpactor', R.load())

    def test_extension_lookup(self):
        self.assertEqual(R.for_path('app/Models/Shop.php'), 'php')
        self.assertEqual(R.for_path('src/App.tsx'), 'typescript')
        self.assertEqual(R.for_path('main.go'), 'go')

    def test_compound_extension_wins(self):
        # .blade.php длиннее .php, и если бы победил .php, блок для
        # шаблонов было бы не подключить
        self.write_user('server blade\n  extensions .blade.php\n  command b\n')
        self.assertEqual(R.for_path('views/x.blade.php'), 'blade')
        self.assertEqual(R.for_path('views/x.php'), 'php')

    def test_unknown_extension(self):
        self.assertIsNone(R.for_path('notes.txt'))

    # --- файлы без расширения ---

    def test_shebang_names_the_language(self):
        # bin/familiar, git-хуки и прочие скрипты расширения не имеют
        self.assertEqual(R.for_path('bin/familiar', '', '#!/usr/bin/env python3'),
                         'python')

    def test_shebang_ignores_interpreter_flags(self):
        self.assertEqual(R.for_path('hooks/pre-commit', '', '#!/bin/bash -e'), 'bash')

    def test_shebang_matches_bare_interpreter(self):
        self.assertEqual(R.for_path('script', '', '#!/usr/bin/ruby'), 'ruby')

    def test_extension_beats_shebang(self):
        self.assertEqual(R.for_path('a.php', '', '#!/usr/bin/env python3'), 'php')

    def test_no_shebang_no_language(self):
        self.assertIsNone(R.for_path('bin/familiar', '', 'plain text'))

    def test_unknown_interpreter(self):
        self.assertIsNone(R.for_path('script', '', '#!/usr/bin/env tclsh'))

    def test_user_can_add_a_shebang(self):
        self.write_user('server tcl\n  extensions .tcl\n  shebang tclsh\n'
                        '  command tcl-ls\n')
        self.assertEqual(R.for_path('script', '', '#!/usr/bin/env tclsh'), 'tcl')

    # --- три уровня ---

    def test_user_overrides_field(self):
        self.write_user('server php\n  command my-php-ls\n')
        self.assertEqual(R.spec_for('php', self.repo).argv, ('my-php-ls',))

    def test_user_keeps_untouched_fields(self):
        self.write_user('server php\n  command my-php-ls\n')
        self.assertEqual(R.spec_for('php', self.repo).roots, ('composer.json',))

    def test_project_overrides_user(self):
        self.write_user('server php\n  command from-user\n')
        self.write_project('server php\n  command from-project\n')
        spec = R.spec_for('php', self.repo, project_root=self.repo)
        self.assertEqual(spec.argv, ('from-project',))

    def test_user_can_enable_disabled_block(self):
        self.write_user('server php-phpactor\n  disabled no\n')
        self.assertIn('php-phpactor', R.load())

    def test_user_can_disable_builtin(self):
        self.write_user('server go\n  disabled yes\n')
        self.assertNotIn('go', R.load())

    def test_edit_invalidates_cache(self):
        self.write_user('server php\n  command first\n')
        self.assertEqual(R.spec_for('php', self.repo).argv, ('first',))
        self.write_user('server php\n  command second\n')
        self.assertEqual(R.spec_for('php', self.repo).argv, ('second',))

    # --- подстановки ---

    def test_cache_substitution(self):
        opts = R.spec_for('php', self.repo).init_options
        self.assertEqual(opts['storagePath'], R.cache_dir('php', self.repo))

    def test_root_substitution(self):
        self.write_user('server php\n  command ls ${ROOT}\n')
        self.assertEqual(R.spec_for('php', self.repo).argv[1], self.repo)

    def test_env_substitution(self):
        os.environ['FAMILIAR_TEST_KEY'] = 'secret'
        try:
            self.write_user('server php\n  initopt licenceKey ${env:FAMILIAR_TEST_KEY}\n')
            self.assertEqual(R.spec_for('php', self.repo).init_options['licenceKey'],
                             'secret')
        finally:
            os.environ.pop('FAMILIAR_TEST_KEY', None)

    def test_empty_env_option_dropped(self):
        # пустой licenceKey слать серверу бессмысленно и вредно
        self.assertNotIn('licenceKey', R.spec_for('php', self.repo).init_options)

    def test_dotted_option_nests(self):
        self.write_user('server go\n  setting gopls.buildFlags -tags=x\n')
        settings = R.spec_for('go', self.repo).settings
        self.assertEqual(settings['gopls']['buildFlags'], '-tags=x')

    def test_repeated_option_key_accumulates(self):
        # длинный files.exclude пишут несколькими строками, и каждая
        # следующая обязана дополнять, а не затирать предыдущие
        self.write_user('server go\n  setting gopls.filters a b\n'
                        '  setting gopls.filters c\n')
        self.assertEqual(R.spec_for('go', self.repo).settings['gopls']['filters'],
                         ['a', 'b', 'c'])

    def test_every_builtin_block_is_runnable(self):
        # блок без команды или расширений молча ничего не обслуживает
        for lang in R.load():
            spec = R.spec_for(lang, self.repo)
            self.assertTrue(spec.argv, f'{lang}: нет команды')
            self.assertTrue(spec.exts, f'{lang}: нет расширений')
            self.assertTrue(spec.install, f'{lang}: нечем ставить')

    def test_dockerfile_matches_by_name(self):
        # у Dockerfile расширения нет вовсе
        self.assertEqual(R.for_path('Dockerfile'), 'dockerfile')
        self.assertEqual(R.for_path('deploy/Dockerfile'), 'dockerfile')

    def test_servers_substitution(self):
        tsdk = R.spec_for('vue', self.repo).init_options['typescript']['tsdk']
        self.assertTrue(tsdk.startswith(R.server_home()), tsdk)

    def test_builtin_php_excludes_are_complete(self):
        excl = R.spec_for('php', self.repo).settings['intelephense']['files']['exclude']
        self.assertIn('**/node_modules/**', excl)
        self.assertIn('**/storage/**', excl)

    def test_multi_value_option_is_list(self):
        self.write_user('server go\n  setting gopls.filters a b\n')
        self.assertEqual(R.spec_for('go', self.repo).settings['gopls']['filters'],
                         ['a', 'b'])

    def test_yes_no_become_bool(self):
        self.write_user('server go\n  setting gopls.staticcheck yes\n')
        self.assertIs(R.spec_for('go', self.repo).settings['gopls']['staticcheck'],
                      True)

    # --- корень проекта ---

    def test_nearest_root_wins(self):
        inner = os.path.join(self.repo, 'packages', 'api')
        os.makedirs(inner)
        open(os.path.join(inner, 'composer.json'), 'w').close()
        spec = R.spec_for('php', self.repo)
        self.assertEqual(R.find_root(os.path.join(inner, 'X.php'), spec, self.repo),
                         inner)

    def test_root_search_stops_at_git_root(self):
        inner = os.path.join(self.repo, 'app')
        os.makedirs(inner)
        open(os.path.join(self.dir, 'composer.json'), 'w').close()   # выше репо
        spec = R.spec_for('php', self.repo)
        self.assertEqual(R.find_root(os.path.join(inner, 'X.php'), spec, self.repo),
                         self.repo)

    def test_git_mode_ignores_markers(self):
        inner = os.path.join(self.repo, 'packages', 'api')
        os.makedirs(inner)
        open(os.path.join(inner, 'composer.json'), 'w').close()
        self.write_user('server php\n  roots-mode git\n')
        spec = R.spec_for('php', self.repo)
        self.assertEqual(R.find_root(os.path.join(inner, 'X.php'), spec, self.repo),
                         self.repo)

    def test_file_outside_repo_falls_back_to_git_root(self):
        # прыжок в stdlib или node_modules уводит за репозиторий:
        # подъём оттуда упёрся бы в маркер чужого проекта
        outside = os.path.join(self.dir, 'vendored', 'lib')
        os.makedirs(outside)
        open(os.path.join(outside, 'composer.json'), 'w').close()
        spec = R.spec_for('php', self.repo)
        self.assertEqual(R.find_root(os.path.join(outside, 'X.php'), spec, self.repo),
                         self.repo)

    def test_missing_marker_falls_back_to_git_root(self):
        spec = R.spec_for('php', self.repo)
        self.assertEqual(R.find_root(os.path.join(self.repo, 'X.php'), spec,
                                     self.repo), self.repo)

    # --- прочее ---

    def test_cache_dir_differs_for_same_name(self):
        a = R.cache_dir('php', '/a/shop')
        b = R.cache_dir('php', '/b/shop')
        self.assertNotEqual(a, b)
        self.assertTrue(os.path.basename(a).startswith('shop-'))

    def test_runtime_path_adds_only_existing_dirs(self):
        from modules.lsp.install import runtime_env
        os.makedirs(R.bin_dir(), exist_ok=True)
        env = runtime_env()
        parts = env['PATH'].split(os.pathsep)
        self.assertIn(R.bin_dir(), parts)
        self.assertNotIn('/no/such/dir', parts)

    def test_runtime_path_keeps_current_entries_once(self):
        from modules.lsp.install import runtime_env
        os.makedirs(R.bin_dir(), exist_ok=True)
        os.environ['PATH'] = os.pathsep.join(['/usr/bin', R.bin_dir()])
        parts = runtime_env()['PATH'].split(os.pathsep)
        self.assertEqual(parts.count(R.bin_dir()), 1)

    def test_runtime_path_honours_registry_env(self):
        from modules.lsp.install import runtime_env
        self.assertEqual(runtime_env({'FAMILIAR_X': '1'})['FAMILIAR_X'], '1')

    def test_unknown_language_gives_none(self):
        self.assertIsNone(R.spec_for('cobol', self.repo))


if __name__ == '__main__':
    unittest.main()
