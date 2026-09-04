"""Вертикальная геометрия правой панели: липкий заголовок скоупа
занимает строку, и вся навигация обязана это учитывать.
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


_ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


def drawn_range(h) -> 'tuple[int, int]':
    """Индексы строк диффа, которые реально попадают на экран.

    Правило отрисовки продублировано намеренно: тест должен ловить
    расхождение геометрии с `_draw_pane_body`, а не повторять её
    расчёт через тот же метод.
    """
    sticky = bool(0 < h.diff_offset < len(h.diff_scope) and h.diff_scope[h.diff_offset])
    return h.diff_offset, h.diff_offset + h.visible_rows() - 1 - (1 if sticky else 0)


class StickyGeometryTest(unittest.TestCase):
    """Файл с def'ами: скоуп непустой, значит при прокрутке над
    диффом висит заголовок.
    """

    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        self.repo = tempfile.mkdtemp(prefix='ccrev_view_')
        self._git('init', '-b', 'main')
        lines = []
        for i in range(20):
            lines.append(f'def func_{i}():\n')
            lines += [f'    x{j} = {i} * {j}\n' for j in range(10)]
            lines.append('\n')
        self.base = lines
        self.write('a.py', ''.join(lines))
        self._git('add', '-A')
        self._git('commit', '-m', 'init')
        edited = list(lines)
        for idx in (5, 60, 130, 200):
            edited[idx] = '    CHANGED\n'
        self.write('a.py', ''.join(edited))

        self.h = R.ReviewHandler([], Workspace.single(self.repo))
        wire(self.h, rows=20, cols=120)
        self.h.load_source()

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
        with open(os.path.join(self.repo, rel), 'w') as f:
            f.write(content)

    def test_hunk_jump_keeps_cursor_on_screen(self):
        self.h.set_focus('diff')
        for _ in range(len(self.h.diff_hunks)):
            self.h.jump_hunk(1)
            lo, hi = drawn_range(self.h)
            self.assertTrue(lo <= self.h.diff_cur <= hi,
                            f'курсор {self.h.diff_cur} вне кадра [{lo}..{hi}]')

    def test_hunk_jump_leaves_context_above(self):
        self.h.set_focus('diff')
        self.h.jump_hunk(1)
        self.h.jump_hunk(1)
        self.assertGreaterEqual(self.h.diff_cur - self.h.diff_offset, 3)

    def test_hunk_jump_from_tree_moves_focus_to_diff(self):
        self.assertEqual(self.h.focus, 'tree')
        self.h.jump_hunk(1)
        self.assertEqual(self.h.focus, 'diff')
        self.assertIn(self.h.diff_cur, self.h.diff_hunks)

    def test_arrow_down_never_hides_the_cursor(self):
        self.h.set_focus('diff')
        for _ in range(len(self.h.diff_rows)):
            self.h.move_cursor(1)
            lo, hi = drawn_range(self.h)
            self.assertTrue(lo <= self.h.diff_cur <= hi,
                            f'курсор {self.h.diff_cur} вне кадра [{lo}..{hi}]')

    def test_scrolling_down_reaches_the_last_row(self):
        for _ in range(len(self.h.diff_rows)):
            self.h.diff_scroll(3)
        _, hi = drawn_range(self.h)
        self.assertGreaterEqual(hi, len(self.h.diff_rows) - 1)

    def test_scroll_keeps_cursor_within_the_drawn_rows(self):
        self.h.set_focus('diff')
        for _ in range(len(self.h.diff_rows)):
            self.h.diff_scroll(3)
            lo, hi = drawn_range(self.h)
            self.assertTrue(lo <= self.h.diff_cur <= hi,
                            f'курсор {self.h.diff_cur} вне кадра [{lo}..{hi}]')

    def test_no_sticky_header_when_scrolled_to_top(self):
        self.assertEqual(self.h.diff_offset, 0)
        self.assertEqual(self.h.sticky_line(), '')
        self.assertEqual(self.h.diff_visible_rows(), self.h.visible_rows())


if __name__ == '__main__':
    unittest.main()
