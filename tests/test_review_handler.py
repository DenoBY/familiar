import os
import shutil
import tempfile
import subprocess
import unittest

import kittymock  # noqa: F401
from kittymock import wire, draw_text
import review as R

_ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


class ReviewHandlerTest(unittest.TestCase):
    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        self.repo = tempfile.mkdtemp(prefix='ccrev_h_')
        self._git('init', '-b', 'main')
        self.write('big.txt', ''.join(f'line {i}\n' for i in range(30)))
        self.write('dir/sub.txt', 'sub original\n')
        self._git('add', '-A')
        self._git('commit', '-m', 'init')
        # рабочие правки: модификация в середине большого файла, правка в подпапке, новый файл
        self.write('big.txt', ''.join((f'line {i}\n' if i != 15 else 'line CHANGED\n')
                                      for i in range(30)))
        self.write('dir/sub.txt', 'sub edited\n')
        self.write('new.txt', 'brand new\n')

        self.h = R.ReviewHandler([], self.repo, self.repo, 'main')
        wire(self.h, rows=40, cols=120)
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
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w') as f:
            f.write(content)

    def _select_file(self, basename):
        for i, r in enumerate(self.h.rows):
            if r['type'] == 'file' and r['name'] == basename:
                self.h.tsel = i
                self.h.load_diff()
                return
        self.fail(f'файл {basename} не найден в дереве')

    # --- дерево и источник ---

    def test_tree_built(self):
        names = [r['name'] for r in self.h.rows]
        self.assertIn('big.txt', names)
        self.assertIn('new.txt', names)
        self.assertIn('dir', names)                 # папка-узел
        self.assertEqual(self.h.n_files, 3)
        self.assertIsNotNone(self.h.current_item())

    def test_untracked_marked(self):
        self._select_file('new.txt')
        self.assertEqual(self.h.current_item()['kind'], 'untracked')

    # --- навигация ---

    def test_move_is_bounded(self):
        self.h.tsel = 0
        self.h.tree_move(-5)
        self.assertEqual(self.h.tsel, 0)
        self.h.tree_move(999)
        self.assertEqual(self.h.tsel, len(self.h.rows) - 1)

    def test_draw_screen_smoke(self):
        self.h.draw_screen()
        text = draw_text(self.h)
        self.assertTrue(self.h.out)
        self.assertIn('working', text)             # scope в шапке
        self.assertIn('[tree]', text)              # футер режима дерева

    # --- scope ---

    def test_cycle_scope(self):
        self.assertEqual(self.h.scope, 'working')
        self.h.cycle_scope()
        self.assertEqual(self.h.scope, 'staged')
        self.h.cycle_scope()
        self.assertEqual(self.h.scope, 'branch')
        self.h.cycle_scope()
        self.assertEqual(self.h.scope, 'working')

    # --- фильтр ---

    def test_filter_narrows_tree(self):
        self.h.filter_query = 'big'
        self.h.rebuild_tree()
        names = [r['name'] for r in self.h.rows if r['type'] == 'file']
        self.assertEqual(names, ['big.txt'])
        self.assertEqual(self.h.n_files, 1)

    # --- шум ---

    def test_toggle_noise(self):
        self.assertFalse(self.h.show_noise)
        self.h.toggle_noise()
        self.assertTrue(self.h.show_noise)

    # --- сворачивание папок ---

    def test_fold_dir(self):
        for i, r in enumerate(self.h.rows):
            if r['type'] == 'dir':
                self.h.tsel = i
                key = r['key']
                break
        else:
            self.fail('нет папки в дереве')
        self.h.set_fold(True)
        self.assertIn(key, self.h.collapsed)
        self.h.set_fold(False)
        self.assertNotIn(key, self.h.collapsed)

    # --- фокус и курсор по диффу ---

    def test_focus_toggle_and_cursor(self):
        self._select_file('big.txt')
        self.assertEqual(self.h.focus, 'tree')
        self.h.set_focus('diff')
        self.assertEqual(self.h.focus, 'diff')
        self.assertTrue(self.h._commentable(self.h.diff_cur))
        before = self.h.diff_cur
        self.h.move_cursor(1)
        self.assertNotEqual(self.h.diff_cur, before)
        self.h.toggle_focus()
        self.assertEqual(self.h.focus, 'tree')

    def test_cursor_skips_gap_padding(self):
        self._select_file('big.txt')
        self.h.set_focus('diff')
        for _ in range(len(self.h.diff_rows)):
            self.h.move_cursor(1)
            self.assertTrue(self.h._landable(self.h.diff_cur))

    def test_jump_edge_in_diff_stays_in_diff(self):
        self._select_file('big.txt')
        self.h.set_focus('diff')
        self.h.move_cursor(3)
        self.h.jump_edge(True)
        self.assertEqual(self.h.focus, 'diff')
        self.assertTrue(self.h._landable(self.h.diff_cur))
        last = self.h.diff_cur
        self.h.jump_edge(False)
        self.assertEqual(self.h.focus, 'diff')
        self.assertEqual(self.h.diff_cur, self.h._first_landable(0))
        self.assertLess(self.h.diff_cur, last)
        self.assertEqual(self.h.diff_offset, 0)

    def test_jump_edge_in_tree_moves_files(self):
        self._select_file('big.txt')
        self.assertEqual(self.h.focus, 'tree')
        self.h.jump_edge(True)
        self.assertEqual(self.h.tsel, len(self.h.rows) - 1)
        self.h.jump_edge(False)
        self.assertEqual(self.h.tsel, 0)

    # --- гэпы ---

    def test_expand_gap(self):
        self._select_file('big.txt')
        gaps_before = sum(1 for g in self.h.diff_gap if g is not None)
        self.assertTrue(gaps_before > 0)
        di = next(i for i, g in enumerate(self.h.diff_gap)
                  if g is not None and self.h.diff_plain[i])
        self.h.expand_gap(di)
        self.assertTrue(self.h.expanded)
        gaps_after = sum(1 for g in self.h.diff_gap if g is not None)
        self.assertLess(gaps_after, gaps_before)

    # --- поиск ---

    def test_search(self):
        self._select_file('big.txt')
        self.h.search_query = 'line'
        self.h._recompute_matches()
        self.assertTrue(len(self.h.search_matches) > 1)
        first = self.h.search_idx
        self.h.search_next(1)
        self.assertNotEqual(self.h.search_idx, first)

    def test_clear_search_resets(self):
        self._select_file('big.txt')
        self.h.search_query = 'line'
        self.h._recompute_matches()
        self.h.search_idx = 1
        self.assertTrue(self.h.search_matches)
        self.h.clear_search()
        self.assertEqual(self.h.search_query, '')
        self.assertEqual(self.h.search_matches, [])
        self.assertEqual(self.h.search_idx, 0)

    # --- аннотации ---

    def test_annotation_lifecycle(self):
        self._select_file('big.txt')
        self.h.set_focus('diff')
        self.h.diff_cur = self.h._first_commentable(0)
        self.h.start_comment()
        self.assertEqual(self.h.input_mode, 'comment')
        self.h.input_buffer = 'нужен рефактор'
        self.h.commit_input()
        self.assertEqual(len(self.h.annots), 1)
        (rel, line), v = next(iter(self.h.annots.items()))
        self.assertEqual(v['text'], 'нужен рефактор')

        self.h.out = []
        self.h.export_review()
        # flash гаснет к концу draw_screen, но текст и OSC52 уже попали в вывод
        self.assertIn('copied', draw_text(self.h))
        self.assertTrue(any('\x1b]52;c;' in str(x) for x in self.h.out))  # OSC52 в буфер

        self.h.clear_annotations()
        self.assertEqual(self.h.annots, {})

    def test_empty_comment_deletes(self):
        self._select_file('big.txt')
        self.h.set_focus('diff')
        self.h.diff_cur = self.h._first_commentable(0)
        self.h.start_comment()
        self.h.input_buffer = 'x'
        self.h.commit_input()
        self.assertEqual(len(self.h.annots), 1)
        # повторный пустой комментарий на той же строке — удаляет
        self.h.diff_cur = self.h._first_commentable(0)
        self.h.start_comment()
        self.h.input_buffer = ''
        self.h.commit_input()
        self.assertEqual(self.h.annots, {})


class EditorCommandTest(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp(prefix='proj_')  # без .idea/.vscode/.zed
        self._backup = {k: os.environ.get(k) for k in ('VISUAL', 'EDITOR')}

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_terminal_editor(self):
        os.environ.pop('VISUAL', None)
        os.environ['EDITOR'] = 'vim'
        cmd, gui = R._editor_command(self.proj, '/f.py', 10)
        self.assertFalse(gui)
        self.assertEqual(cmd, ['vim', '+10', '/f.py'])

    def test_gui_editor_code(self):
        os.environ.pop('VISUAL', None)
        os.environ['EDITOR'] = 'code'
        cmd, gui = R._editor_command(self.proj, '/f.py', 7)
        self.assertTrue(gui)
        self.assertEqual(cmd, ['code', '-g', '/f.py:7'])

    def test_gui_editor_subl_positional(self):
        os.environ.pop('VISUAL', None)
        os.environ['EDITOR'] = 'subl'
        cmd, gui = R._editor_command(self.proj, '/f.py', 3)
        self.assertTrue(gui)
        self.assertEqual(cmd, ['subl', '/f.py:3'])

    def test_visual_precedence(self):
        os.environ['VISUAL'] = 'code'
        os.environ['EDITOR'] = 'vim'
        cmd, gui = R._editor_command(self.proj, '/f.py', 1)
        self.assertTrue(gui)
        self.assertEqual(cmd[0], 'code')


class YankTest(unittest.TestCase):
    """Сборка payload для копирования кода из диффа — чистая логика без git."""

    def setUp(self):
        self.h = R.ReviewHandler([], '/repo', '/repo', 'main')
        wire(self.h, rows=40, cols=120)
        # минимально имитируем выбранный файл a/b.py и загруженный дифф
        self.h.filtered = [{'path': 'a/b.py', 'rel': 'a/b.py', 'kind': 'modified'}]
        self.h.rows = [{'type': 'file', 'idx': 0, 'depth': 0,
                        'name': 'b.py', 'kind': 'modified', 'stat': None}]
        self.h.tsel = 0
        self.h.diff_after = 'l1\nl2\nl3\nl4\n'
        self.h.diff_lineno = [1, 2, 0, 3, 4]      # индекс 2 — гэп/padding (lineno 0)
        self.h.diff_gap = [None, None, 5, None, None]
        self.h.diff_rows = ['l1', 'l2', '', 'l3', 'l4']
        self.h.diff_plain = ['l1', 'l2', '', 'l3', 'l4']
        self.h.diff_vis = ['l1', 'l2', '', 'l3', 'l4']
        self.h.diff_kind_bg = [None, None, None, None, None]

    def test_single_line(self):
        code, a, b = self.h._yank_code(1, 1)
        self.assertEqual((a, b), (2, 2))
        self.assertEqual(code, 'l2')

    def test_range_joins_after_lines(self):
        code, a, b = self.h._yank_code(0, 4)
        self.assertEqual((a, b), (1, 4))
        self.assertEqual(code, 'l1\nl2\nl3\nl4')

    def test_range_skips_gap_rows(self):
        # диапазон [1..3] включает гэп (индекс 2), номера строк 2 и 3 → код l2..l3
        code, a, b = self.h._yank_code(1, 3)
        self.assertEqual((a, b), (2, 3))
        self.assertEqual(code, 'l2\nl3')

    def test_no_real_lines_returns_none(self):
        self.assertIsNone(self.h._yank_code(2, 2))   # только гэп

    def test_hscroll_capped_at_right_edge(self):
        self.h.diff_before = 'a\n'
        self.h.diff_after = 'a' + 'Z' * 200 + '\n'   # одна очень длинная строка
        self.h.build_diff_rows()
        cap = self.h.hscroll_max
        self.assertGreater(cap, 0)
        self.h.hscroll_by(10_000)
        self.assertEqual(self.h.hscroll, cap)        # вправо не уезжает за предел
        self.h.hscroll_by(-10_000)
        self.assertEqual(self.h.hscroll, 0)          # влево — до нуля

    def test_is_code_row_skips_gap_and_padding(self):
        self.assertTrue(self.h._is_code_row(0))      # обычная строка
        self.assertTrue(self.h._is_code_row(4))
        self.assertFalse(self.h._is_code_row(2))     # padding/разделитель гэпа (lineno 0, gap set)

    def test_cursor_hidden_on_padding_during_selection(self):
        self.h.focus = 'diff'
        self.h.diff_sel = (0, 4)
        self.h.diff_cur = 2                          # курсор на padding гэпа
        out = self.h._diff_cell(2, 80, None, -1)
        self.assertNotIn('▎', out)                   # серый курсор на padding не рисуется

    def test_cursor_shown_on_code_row(self):
        self.h.focus = 'diff'
        self.h.diff_sel = None
        self.h.diff_cur = 0
        out = self.h._diff_cell(0, 80, None, -1)
        self.assertIn('▎', out)

    def test_no_file_returns_none(self):
        self.h.rows = [{'type': 'dir', 'depth': 0, 'name': 'a',
                        'key': 'a', 'count': 1, 'collapsed': False}]
        self.assertIsNone(self.h._yank_code(0, 4))   # current_item() → None

    def _copied(self):
        import base64
        blob = ''.join(self.h.out)
        self.assertIn('\x1b]52;c;', blob)
        b64 = blob.split('\x1b]52;c;', 1)[1].split('\x07', 1)[0]
        return base64.b64decode(b64).decode()

    def test_copy_selection_writes_code_only(self):
        self.h.diff_cur = 0
        self.h.copy_selection()
        self.assertEqual(self._copied(), 'l1')

    def test_copy_selection_range(self):
        self.h.focus = 'diff'
        self.h.diff_sel = (0, 4)
        self.h.copy_selection()
        self.assertEqual(self._copied(), 'l1\nl2\nl3\nl4')
        self.assertIsNone(self.h.diff_sel)     # копия снимает выделение
        self.assertEqual(self.h.focus, 'tree')  # и убирает курсор диффа

    def test_copy_single_line_keeps_focus(self):
        self.h.focus = 'diff'
        self.h.diff_cur = 0                     # без выделения — фокус остаётся
        self.h.copy_selection()
        self.assertEqual(self._copied(), 'l1')
        self.assertEqual(self.h.focus, 'diff')

    def test_copy_location_writes_path_line(self):
        self.h.diff_cur = 3                     # lineno 3
        self.h.copy_location()
        self.assertEqual(self._copied(), '/repo/a/b.py:3')

    def test_copy_char_selection_substring(self):
        # выделение куска внутри строки → копируется ровно подстрока
        self.h.diff_plain = ['  1   hello world code']
        self.h.diff_char_sel = (0, 6, 11)       # 'hello'
        self.h.copy_selection()
        self.assertEqual(self._copied(), 'hello')
        self.assertIsNone(self.h.diff_char_sel)  # копия снимает выделение

    def test_char_selection_renders_from_visible(self):
        self.h.focus = 'diff'
        self.h.diff_plain = ['plain-content-xyz']
        self.h.diff_vis = ['plain-content-xyz']
        self.h.diff_rows = ['ready-row']
        self.h.diff_lineno = [1]
        self.h.diff_gap = [None]
        self.h.diff_kind_bg = [None]
        self.h.diff_char_sel = (0, 0, 5)
        out = self.h._diff_cell(0, 80, None, -1)
        self.assertIn('plain-content-xyz', out)   # из vis, а не готовой строки rows[0]

    def test_copy_path_writes_abspath(self):
        self.h.copy_path()
        self.assertEqual(self._copied(), '/repo/a/b.py')

    def test_smart_copy_tree_copies_path(self):
        self.h.focus = 'tree'
        self.h.smart_copy()
        self.assertEqual(self._copied(), '/repo/a/b.py')

    def test_smart_copy_diff_copies_code(self):
        self.h.focus = 'diff'
        self.h.diff_cur = 0
        self.h.smart_copy()
        self.assertEqual(self._copied(), 'l1')

    def test_smart_copy_location_diff(self):
        self.h.focus = 'diff'
        self.h.diff_cur = 0
        self.h.smart_copy_location()
        self.assertEqual(self._copied(), '/repo/a/b.py:1')

    def test_smart_copy_location_tree_copies_path(self):
        self.h.focus = 'tree'
        self.h.smart_copy_location()
        self.assertEqual(self._copied(), '/repo/a/b.py')


if __name__ == '__main__':
    unittest.main()
