import os
import shutil
import subprocess
import tempfile
import unittest

import kittymock  # noqa: F401
import log as L
from kittymock import MouseEvent, draw_text, wire


_ENV = {
    'GIT_AUTHOR_NAME': 'Alice', 'GIT_AUTHOR_EMAIL': 'a@e',
    'GIT_COMMITTER_NAME': 'Alice', 'GIT_COMMITTER_EMAIL': 'a@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


class LogHandlerTest(unittest.TestCase):
    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        self.repo = tempfile.mkdtemp(prefix='cclog_h_')
        self._git('init', '-b', 'main')
        self._write('a.txt', 'a1\na2\na3\n')
        self._git('add', '-A')
        self._git('commit', '-m', 'first')
        self._write('a.txt', 'a1\na2 changed\na3\n')
        self._write('src/new.py', 'x = 1\n')
        self._git('add', '-A')
        self._git('commit', '-m', 'add feature')

        self.h = L.CommitLogHandler([], self.repo)
        wire(self.h, rows=30, cols=120)
        self.h.reload_commits()

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

    def _write(self, rel, content):
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w') as f:
            f.write(content)

    # --- список коммитов ---

    def test_reload_lists_commits_newest_first(self):
        self.assertEqual([c['subject'] for c in self.h.commits],
                         ['add feature', 'first'])

    def test_git_error_shown_when_log_fails(self):
        d = tempfile.mkdtemp(prefix='cclog_notrepo_')
        try:
            self.h.root = d
            self.h.reload_commits()
            self.assertEqual(self.h.commits, [])
            self.assertIn('not a git repository', self.h.status)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_draw_commits_smoke(self):
        self.h.draw_screen()
        text = draw_text(self.h)
        self.assertIn('current branch', text)
        self.assertIn('add feature', text)

    def test_filter_commits(self):
        self.h.filter_query = 'feature'
        self.h.rebuild_commits()
        self.assertEqual([c['subject'] for c in self.h.commits], ['add feature'])

    def test_toggle_mode_reloads(self):
        self.h.toggle_mode()
        self.assertTrue(self.h.all_branches)
        self.h.toggle_mode()
        self.assertFalse(self.h.all_branches)

    def test_detail_panel_shows_commit_info(self):
        self.h.sel = 0
        lines = self.h._detail_lines(50)
        text = '\n'.join(lines)
        self.assertIn('add feature', text)                 # сообщение коммита
        self.assertIn('a@e', text)                         # email автора
        self.assertIn('branches', text)                    # список веток

    def test_draw_commits_with_panel_smoke(self):
        wire(self.h, rows=30, cols=130)                    # широкий экран → панель видна
        self.h.draw_screen()
        self.assertIn('branches', draw_text(self.h))

    def test_copy_commit_writes_hash(self):
        self.h.sel = 0
        self.h.copy_commit()
        self.assertTrue(any('\x1b]52;c;' in str(x) for x in self.h.out))

    def test_commit_row_shows_ref(self):
        # HEAD-ветка (main) должна попасть в строку выбранного коммита
        row = self.h._commit_row(self.h.commits[0], 120, False)
        self.assertIn('main', row)

    def test_display_refs_collapses_remote(self):
        self.assertEqual(
            L.display_refs([('master', 'head'), ('origin/master', 'remote')]),
            [('origin & master', 'head')])                 # local+remote → один чип
        # ветка со слэшем + одноимённая удалёнка → не дублируется
        self.assertEqual(
            L.display_refs([('feature/x', 'branch'), ('origin/feature/x', 'remote')]),
            [('origin & feature/x', 'branch')])
        self.assertEqual(
            L.display_refs([('origin/PP-1', 'remote'), ('v1.0', 'tag')]),
            [('origin/PP-1', 'remote'), ('v1.0', 'tag')])  # без локальной — как есть

    def test_commit_row_columns_aligned(self):
        # автор/дата — фикс-колонки: у строк разной длины хвост начинается одинаково
        c1 = dict(self.h.commits[0], subject='x', refs=[])
        c2 = dict(self.h.commits[0], subject='y' * 60, refs=[])
        r1 = self.h._commit_row(c1, 100, False)
        r2 = self.h._commit_row(c2, 100, False)
        self.assertEqual(r1.index('Alice'), r2.index('Alice'))

    # --- открытие коммита → экран diff ---

    def test_enter_opens_diff(self):
        self.h.sel = 0
        self.h.open_commit()
        self.assertEqual(self.h.screen, 'diff')
        self.assertEqual(self.h.commit['subject'], 'add feature')
        self.assertEqual(self.h.n_files, 2)                # a.txt + src/new.py
        self.h.draw_screen()
        self.assertIn(self.h.commit['short'], draw_text(self.h))

    def test_diff_shows_changed_lines(self):
        self.h.sel = 0
        self.h.open_commit()
        # выбрать a.txt (модифицирован) — в диффе есть новая версия строки
        self.h.tsel = next(i for i, r in enumerate(self.h.rows)
                           if r['type'] == 'file' and r['name'] == 'a.txt')
        self.h.load_diff()
        self.assertTrue(any('a2 changed' in p for p in self.h.diff_plain))

    def test_escape_returns_to_commits(self):
        self.h.sel = 0
        self.h.open_commit()
        self.h._diff_key('ESCAPE')                         # tree → назад к списку
        self.assertEqual(self.h.screen, 'commits')

    # --- копирование ---

    def test_copy_path_writes_clipboard(self):
        self.h.sel = 0
        self.h.open_commit()
        self.h.copy_path()
        self.assertTrue(any('\x1b]52;c;' in str(x) for x in self.h.out))

    def test_yank_code_from_commit_version(self):
        self.h.sel = 0
        self.h.open_commit()
        self.h.tsel = next(i for i, r in enumerate(self.h.rows)
                           if r['type'] == 'file' and r['name'] == 'a.txt')
        self.h.load_diff()
        self.h.set_focus('diff')
        code, a, b = self.h._yank_code(*self.h._sel_range())
        self.assertIn(code, self.h.diff_after)             # код берётся из версии коммита

    # --- мышь ---

    def test_click_commit_twice_opens(self):
        self.h.draw_screen()
        self.h.on_click(MouseEvent(cell_x=2, cell_y=2))    # первая строка списка (head=2)
        self.assertEqual(self.h.sel, 0)
        self.h.on_click(MouseEvent(cell_x=2, cell_y=2))    # повтор — открыть
        self.assertEqual(self.h.screen, 'diff')


if __name__ == '__main__':
    unittest.main()
