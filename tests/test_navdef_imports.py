import os
import shutil
import subprocess
import tempfile
import unittest

import kittymock  # noqa: F401
import modules.vcs.navdef as N


_ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


class ImportResolveTest(unittest.TestCase):
    """Резолв импортов: импортнутое имя ведёт в точный файл, несмотря
    на файл-обманку с тем же определением в другом месте.
    """

    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        self.repo = tempfile.mkdtemp(prefix='ccnav_imp_')
        subprocess.run(['git', '-C', self.repo, 'init', '-b', 'main'],
                       check=True, capture_output=True, env=os.environ)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def w(self, rel, content):
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p) or self.repo, exist_ok=True)
        with open(p, 'w') as f:
            f.write(content)

    def resolve(self, cur_rel, ext, symbol, **kw):
        with open(os.path.join(self.repo, cur_rel)) as f:
            src = f.read()
        return N.resolve_definition(self.repo, cur_rel, ext, symbol,
                                    cur_source=src, **kw)

    # --- Python ---

    def test_python_absolute_import(self):
        self.w('pkg/util.py', 'def helper():\n    return 1\n')
        self.w('decoy/util.py', 'def helper():\n    return 2\n')   # обманка
        self.w('main.py', 'from pkg.util import helper\nhelper()\n')
        out = self.resolve('main.py', '.py', 'helper', is_call=True)
        self.assertEqual(out[0].path, 'pkg/util.py')
        self.assertEqual(out[0].line, 1)

    def test_python_relative_import(self):
        self.w('pkg/__init__.py', '')
        self.w('pkg/util.py', 'def helper():\n    return 1\n')
        self.w('pkg/a.py', 'from .util import helper\nhelper()\n')
        out = self.resolve('pkg/a.py', '.py', 'helper', is_call=True)
        self.assertEqual(out[0].path, 'pkg/util.py')

    def test_python_import_module(self):
        self.w('pkg/util.py', 'def helper():\n    return 1\n')
        self.w('main.py', 'import pkg.util as u\nu.helper()\n')
        out = self.resolve('main.py', '.py', 'u', is_attr=False)
        self.assertEqual(out[0].path, 'pkg/util.py')
        self.assertEqual(out[0].line, 1)

    # --- JS/TS ---

    def test_ts_named_import(self):
        self.w('src/util.ts', 'export function helper() {}\n')
        self.w('lib/util.ts', 'export function helper() {}\n')      # обманка
        self.w('src/main.ts', "import { helper } from './util'\nhelper()\n")
        out = self.resolve('src/main.ts', '.ts', 'helper', is_call=True)
        self.assertEqual(out[0].path, 'src/util.ts')

    def test_ts_alias_import(self):
        self.w('src/util.ts', 'export function realName() {}\n')
        self.w('src/main.ts', "import { realName as h } from './util'\nh()\n")
        out = self.resolve('src/main.ts', '.ts', 'h', is_call=True)
        self.assertEqual(out[0].path, 'src/util.ts')

    def test_js_index_resolution(self):
        self.w('src/mod/index.js', 'export function helper() {}\n')
        self.w('src/main.js', "import { helper } from './mod'\n")
        out = self.resolve('src/main.js', '.js', 'helper')
        self.assertEqual(out[0].path, 'src/mod/index.js')

    # --- PHP ---

    def test_php_psr4_use(self):
        self.w('composer.json', '{"autoload":{"psr-4":{"App\\\\":"src/"}}}')
        self.w('src/Models/User.php', '<?php\nclass User {}\n')
        self.w('src/other/User.php', '<?php\nclass User {}\n')       # обманка
        self.w('src/main.php', '<?php\nuse App\\Models\\User;\nnew User();\n')
        out = self.resolve('src/main.php', '.php', 'User', is_call=True)
        self.assertEqual(out[0].path, 'src/Models/User.php')

    # --- Go ---

    def test_go_package_symbol(self):
        self.w('go.mod', 'module example.com/proj\n\ngo 1.21\n')
        self.w('pkg/thing/thing.go', 'package thing\n\nfunc Do() {}\n')
        self.w('other/thing.go', 'package thing\n\nfunc Do() {}\n')  # обманка
        self.w('main.go', ('package main\n\nimport "example.com/proj/pkg/thing"\n\n'
                           'func main() { thing.Do() }\n'))
        out = self.resolve('main.go', '.go', 'Do', is_attr=True, qualifier='thing')
        self.assertEqual(out[0].path, 'pkg/thing/thing.go')

    # --- fallback ---

    def test_unresolved_import_falls_back_to_grep(self):
        # bare-спецификатор (node_modules) не резолвим → repo-wide grep
        self.w('a.ts', 'export function loner() {}\n')
        self.w('main.ts', "import { loner } from 'external-pkg'\nloner()\n")
        out = self.resolve('main.ts', '.ts', 'loner', is_call=True)
        self.assertEqual(out[0].path, 'a.ts')


if __name__ == '__main__':
    unittest.main()
