"""Правка файла в final-view review: вход и доступность, ввод,
автосохранение, конфликт с диском и то, что после записи экран
остаётся на том же файле.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

import kittymock  # noqa: F401
import review as R
from kittymock import (
    EventType,
    KeyEvent,
    MouseButton,
    MouseEvent,
    draw_text,
    run_threads_inline,
    wire,
)
from modules.lsp.position import Target
from modules.vcs.editmode import CARET_BEAM, CARET_RESET, EXACT_DELAY
from modules.vcs.view import SEP
from modules.vcs.workspace import Workspace


_ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}

BIG = ''.join(f'line {i}\n' for i in range(30))


class EditModeTest(unittest.TestCase):

    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        run_threads_inline(self)
        self.repo = tempfile.mkdtemp(prefix='ccrev_edit_')
        self._git('init', '-b', 'main')
        self.write('a.txt', 'alpha\n')
        self.write('big.txt', BIG)
        self.write('c.txt', 'gamma\n')
        self.write('same.txt', 'same\n')
        self._git('add', '-A')
        self._git('commit', '-m', 'init')
        self.write('a.txt', 'alpha edited\n')
        self.write('big.txt', BIG.replace('line 15\n', 'line CHANGED\n'))
        self.write('c.txt', 'gamma edited\n')

        self.h = R.ReviewHandler([], Workspace.single(self.repo))
        wire(self.h, rows=30, cols=120)
        self.clip = []
        self.h._copy_clipboard = self.clip.append
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

    def write(self, rel, content, binary=False):
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb' if binary else 'w') as f:
            f.write(content)

    def read(self, rel):
        with open(os.path.join(self.repo, rel), 'rb') as f:
            return f.read().decode()

    def select(self, rel):
        self.h.tsel = next(i for i, r in enumerate(self.h.rows)
                           if r['type'] == 'file'
                           and self.h.filtered[r['idx']]['path'] == rel)
        self.h.load_diff()

    def edit(self, rel, line=1):
        self.select(rel)
        self.h.set_focus('diff')
        # в unified нужная строка может быть свёрнута в гэп
        self.h.view_mode = 'final'
        self.h.build_diff_rows()
        self.h.diff_cur = next(i for i, ln in enumerate(self.h.diff_lineno) if ln == line)
        self.h.on_text('i')
        self.assertTrue(self.h.editing, self.h.flash)

    def key(self, name, **mods):
        self.h.on_key(KeyEvent(name, **mods))

    def screen(self):
        self.h.out = []
        self.h.draw_screen()
        return draw_text(self.h)

    def mouse(self, x, y, *kinds):
        for kind in kinds:
            self.h.on_mouse_event(MouseEvent(cell_x=x, cell_y=y,
                                             buttons=MouseButton.LEFT, type=kind))

    def click_tree(self, name):
        li = next(i for i, r in enumerate(self.h.rows) if r['name'] == name)
        self.mouse(1, li - self.h.left_offset + 2, EventType.PRESS, EventType.RELEASE)

    def commit_file(self, rel, content):
        self.write(rel, content)
        self._git('add', rel)
        self._git('commit', '-m', rel)

    # --- вход ---

    def test_i_from_unified_diff_switches_to_final_on_the_same_line(self):
        self.select('big.txt')
        self.h.set_focus('diff')
        self.assertEqual(self.h.view_mode, 'diff')
        self.h.diff_cur = self.h.diff_lineno.index(16)
        self.h.on_text('ш')                  # та же клавиша на ЙЦУКЕН
        self.assertTrue(self.h.editing)
        self.assertEqual(self.h.view_mode, 'final')
        self.assertEqual(self.h.edit_buf.line, 15)
        self.assertEqual(self.h.diff_lineno[self.h.diff_cur], 16)

    def test_footer_offers_edit_and_shows_edit_keys(self):
        self.select('a.txt')
        self.h.set_focus('diff')
        self.assertIn('i edit', self.screen())
        self.h.on_text('i')
        text = self.screen()
        self.assertIn('[edit]', text)
        self.assertIn('editing', text)

    def test_find_mode_does_not_edit(self):
        self.h.toggle_find()
        self.h.cancel_input()
        self.h.on_text('i')
        self.assertFalse(self.h.editing)

    def test_crlf_file_is_refused(self):
        self.write('a.txt', b'x\r\ny\r\n', binary=True)
        self.h.refresh()
        self.select('a.txt')
        self.h.on_text('i')
        self.assertFalse(self.h.editing)
        self.assertEqual(self.read('a.txt'), 'x\r\ny\r\n')

    def test_external_file_in_repo_is_editable_absolute_is_not(self):
        self.select('a.txt')
        self.h._navigate(Target('same.txt', 1, 'def', ''))
        self.assertEqual(self.h._external, 'same.txt')
        self.h.on_text('i')
        self.assertTrue(self.h.editing)
        self.h.on_text('X')
        self.key('ESCAPE')
        self.assertEqual(self.read('same.txt'), 'Xsame\n')
        # теперь файл изменён — экран встаёт на него в дереве
        self.assertIsNone(self.h._external)
        self.assertEqual(self.h.current_item()['path'], 'same.txt')

        outside = tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False)
        self.addCleanup(os.unlink, outside.name)
        outside.write('stdlib\n')
        outside.close()
        self.h._show_file(outside.name, 1, None)
        self.assertIn('[read-only]', self.screen())
        self.h.on_text('i')
        self.assertFalse(self.h.editing)
        self.assertIn('outside the repository', draw_text(self.h))

    # --- ввод ---

    def test_cyrillic_is_text_not_commands(self):
        self.edit('a.txt')
        self.h.on_text('йцук q')
        self.assertEqual(self.h.edit_buf.lines[0], 'йцук qalpha edited')
        self.assertEqual(self.h.quits, [])

    def test_enter_keeps_indent_and_undo_redo_by_keys(self):
        self.write('a.txt', '    alpha\n')
        self.h.refresh()
        self.edit('a.txt')
        self.key('END')
        self.key('ENTER')
        self.h.on_text('x')
        self.assertEqual(self.h.edit_buf.lines, ['    alpha', '    x'])
        self.key('z', super=True)
        self.key('z', super=True)
        self.assertEqual(self.h.edit_buf.lines, ['    alpha'])
        self.key('z', super=True, shift=True)
        self.assertEqual(self.h.edit_buf.lines, ['    alpha', '    '])

    def test_typing_updates_gutter_marks_without_moving_the_code(self):
        self.edit('big.txt', line=3)
        gutter = self.h._gutter_cols()
        self.key('END')
        self.h.on_text(' new')
        self.key('ENTER')
        self.assertEqual(self.h._gutter_cols(), gutter)
        self.assertEqual(self.h.diff_marks[2], 'mod')
        self.assertEqual(self.h.diff_marks[3], 'add')
        self.assertEqual(self.h.diff_rows and len(self.h.diff_rows), 31)

    def test_caret_is_drawn_at_the_edit_position(self):
        self.edit('a.txt')
        self.key('END')
        self.screen()
        row, col = self.h._caret
        self.assertEqual(row, 2)
        code_x = self.h.left_width() + 3 + self.h._gutter_cols() + 2
        self.assertEqual(col, code_x + len('alpha edited'))

    def test_caret_is_a_beam_while_editing_and_restored_after(self):
        self.h.out = []
        self.edit('a.txt')
        self.assertIn(CARET_BEAM, draw_text(self.h))
        # форма — состояние терминала: на каждом кадре её не шлём
        self.assertNotIn(CARET_BEAM, self.screen())
        self.h.out = []
        self.key('ESCAPE')
        self.assertIn(CARET_RESET, draw_text(self.h))

    def test_quitting_mid_edit_restores_the_cursor_shape(self):
        self.edit('a.txt')
        self.h.out = []
        self.h.finalize()
        self.assertIn(CARET_RESET, draw_text(self.h))

    def test_paste_goes_in_as_text(self):
        self.edit('a.txt')
        self.h.on_text('one\r', in_bracketed_paste=True)
        self.h.on_text('\ntwo ', in_bracketed_paste=True)
        self.assertEqual(self.h.edit_buf.lines, ['one', 'two alpha edited'])

    def test_paste_ending_in_cr_breaks_the_line_right_away(self):
        self.edit('a.txt')
        self.h.on_text('one\r', in_bracketed_paste=True)
        self.key('ESCAPE')
        self.assertEqual(self.read('a.txt'), 'one\nalpha edited\n')

    def test_cmd_up_and_down_go_to_the_file_edges(self):
        self.edit('big.txt', line=10)
        self.key('DOWN', super=True)
        self.assertEqual(self.h.edit_buf.caret, (29, 7))
        self.key('UP', super=True, shift=True)
        self.assertEqual(self.h.edit_buf.selection(), ((0, 0), (29, 7)))

    def test_copy_and_cut_line_without_selection(self):
        self.edit('big.txt', line=2)
        self.key('x', super=True)
        self.assertEqual(self.clip, ['line 1\n'])
        self.assertEqual(self.h.edit_buf.lines[1], 'line 2')

    # --- сохранение ---

    def test_escape_saves_and_leaves(self):
        self.edit('a.txt')
        self.h.on_text('>')
        self.key('ESCAPE')
        self.assertFalse(self.h.editing)
        self.assertEqual(self.read('a.txt'), '>alpha edited\n')
        self.assertEqual(self.h.current_item()['path'], 'a.txt')
        self.assertEqual(self.h.view_mode, 'final')

    def test_cmd_s_saves_and_stays_in_edit(self):
        self.edit('a.txt')
        self.h.on_text('>')
        self.key('s', super=True)
        self.assertTrue(self.h.editing)
        self.assertEqual(self.read('a.txt'), '>alpha edited\n')
        self.assertNotIn('modified', self.screen())

    def test_file_without_trailing_newline_saved_byte_for_byte(self):
        self.write('a.txt', 'no newline')
        self.h.refresh()
        self.edit('a.txt')
        self.key('END')
        self.h.on_text('!')
        self.key('ESCAPE')
        self.assertEqual(self.read('a.txt'), 'no newline!')

    def test_empty_file_can_be_filled(self):
        self.write('a.txt', '')
        self.h.refresh()
        self.select('a.txt')
        self.h.on_text('i')
        self.assertTrue(self.h.editing)
        self.h.on_text('hi')
        self.key('ESCAPE')
        self.assertEqual(self.read('a.txt'), 'hi')

    def test_cutting_everything_leaves_one_line_marked_as_cut(self):
        self.edit('big.txt')
        self.key('a', super=True)
        self.key('x', super=True)
        self.assertEqual(self.h.edit_buf.lines, [''])
        self.assertEqual(len(self.h.diff_rows), 1)
        # строка под кареткой несёт метку «здесь вырезано», а не пустоту
        self.assertIsNotNone(self.h.diff_marks[0])
        self.assertIn('\n'.join(BIG.replace('line 15\n', 'line CHANGED\n').splitlines()),
                      self.clip[-1])
        self.key('ESCAPE')
        self.assertEqual(self.read('big.txt'), '')

    def test_tree_click_saves_then_opens_the_clicked_file(self):
        self.edit('a.txt')
        self.h.on_text('>')
        self.click_tree('c.txt')
        self.assertFalse(self.h.editing)
        self.assertEqual(self.read('a.txt'), '>alpha edited\n')
        self.assertEqual(self.h.current_item()['path'], 'c.txt')

    def test_tree_click_opens_the_clicked_file_when_saving_shifts_the_tree(self):
        self.commit_file('ab.txt', 'clean\n')
        self.select('a.txt')
        self.h._navigate(Target('ab.txt', 1, 'def', ''))
        self.h.on_text('i')
        self.h.on_text('>')
        # сохранение впервые делает ab.txt изменённым: он встаёт в
        # дерево выше c.txt, и ячейка клика уже чужая
        self.click_tree('c.txt')
        self.assertEqual(self.read('ab.txt'), '>clean\n')
        self.assertEqual(self.h.current_item()['path'], 'c.txt')

    def test_ctrl_c_saves_before_quitting(self):
        self.edit('a.txt')
        self.h.on_text('>')
        self.key('c', ctrl=True)
        self.assertEqual(self.h.quits, [0])
        self.assertEqual(self.read('a.txt'), '>alpha edited\n')

    def test_refresh_path_saves_without_asking(self):
        self.edit('a.txt')
        self.h.on_text('>')
        self.h.refresh()
        self.assertFalse(self.h.editing)
        self.assertEqual(self.read('a.txt'), '>alpha edited\n')

    def test_edit_back_to_original_keeps_the_file_on_screen(self):
        self.edit('c.txt')
        self.key('END')
        for _ in ' edited':
            self.key('BACKSPACE')
        self.key('ESCAPE')
        self.assertEqual(self.read('c.txt'), 'gamma\n')
        # файл стал чистым и ушёл из дерева, но экран не уехал на соседа
        self.assertEqual(self.h._external, 'c.txt')
        self.assertIn('gamma', self.h.diff_after)

    def test_leaving_with_nowhere_to_go_back_keeps_the_saved_file_on_screen(self):
        self.edit('c.txt')
        self.key('END')
        for _ in ' edited':
            self.key('BACKSPACE')
        self.key('o', ctrl=True)
        self.assertFalse(self.h.editing)
        # дерево пересканировано, но панель не показывает c.txt под
        # чужим путём: файл открыт как есть
        self.assertEqual(self.h._external, 'c.txt')
        self.assertEqual(self.h.diff_after, 'gamma\n')

    def test_file_that_became_the_last_change_stays_visible_and_editable(self):
        self._git('checkout', 'a.txt', 'big.txt')
        self.h.refresh()
        self.edit('c.txt')
        self.key('END')
        for _ in ' edited':
            self.key('BACKSPACE')
        self.key('ESCAPE')
        self.assertEqual(self.h.rows, [])
        text = self.screen()
        self.assertIn('no changes', text)
        self.assertIn('gamma', text)
        self.h.on_text('i')
        self.h.on_text('Z')
        self.assertIn('Zgamma', self.screen())
        self.key('ESCAPE')
        self.assertEqual(self.read('c.txt'), 'Zgamma\n')

    # --- конфликт с диском ---

    def _conflict(self):
        self.edit('a.txt')
        self.h.on_text('>')
        self.write('a.txt', 'agent wrote this\n')
        self.key('ESCAPE')
        self.assertTrue(self.h._pending_active())
        self.assertIn('changed on disk', self.screen())

    def test_conflict_y_overwrites(self):
        self._conflict()
        self.h.on_text('y')
        self.assertFalse(self.h.editing)
        self.assertEqual(self.read('a.txt'), '>alpha edited\n')

    def test_conflict_d_discards_mine_into_clipboard(self):
        self._conflict()
        self.h.on_text('d')
        self.assertFalse(self.h.editing)
        self.assertEqual(self.read('a.txt'), 'agent wrote this\n')
        self.assertEqual(self.clip[-1], '>alpha edited\n')
        self.assertIn('agent wrote this', self.h.diff_after)

    def test_conflict_other_key_keeps_editing(self):
        self._conflict()
        self.h.on_text('n')
        self.assertTrue(self.h.editing)
        self.assertEqual(self.read('a.txt'), 'agent wrote this\n')

    def test_paste_does_not_confirm_overwrite(self):
        self._conflict()
        self.h.on_text('yes please', in_bracketed_paste=True)
        self.assertTrue(self.h.editing)
        self.assertEqual(self.read('a.txt'), 'agent wrote this\n')

    def test_closed_from_outside_mid_conflict_keeps_the_edit_in_clipboard(self):
        self._conflict()
        self.h.finalize()
        self.assertEqual(self.read('a.txt'), 'agent wrote this\n')
        self.assertEqual(self.clip[-1], '>alpha edited\n')

    def test_discarding_the_edit_puts_comments_back(self):
        self.select('big.txt')
        self.h.set_focus('diff')
        self.h.diff_cur = self.h.diff_lineno.index(16)
        self.h.start_comment()
        self.h.input_text('note')
        self.h.commit_input()
        self.edit('big.txt', line=2)
        self.key('ENTER')
        self.key('ENTER')
        self.assertEqual(list(self.h.annots), [(None, 'big.txt', 18)])
        self.write('big.txt', 'agent\n' + self.read('big.txt'))
        self.key('ESCAPE')
        self.h.on_text('d')
        self.assertEqual(list(self.h.annots), [(None, 'big.txt', 16)])

    # --- взаимодействие с остальным экраном ---

    def test_hunk_marker_reverts_into_buffer_not_disk(self):
        self.edit('big.txt', line=16)
        di = self.h.diff_hunks[0]
        self.h._revert_hunk(di)
        self.assertEqual(self.h.edit_buf.lines[15], 'line 15')
        self.assertIn('line CHANGED', self.read('big.txt'))
        self.key('z', super=True)
        self.assertEqual(self.h.edit_buf.lines[15], 'line CHANGED')

    def test_comment_follows_its_line_when_lines_inserted_above(self):
        self.select('big.txt')
        self.h.set_focus('diff')
        self.h.diff_cur = self.h.diff_lineno.index(16)
        self.h.start_comment()
        self.h.input_text('note')
        self.h.commit_input()
        self.edit('big.txt', line=2)
        self.key('ENTER')
        self.assertEqual(list(self.h.annots), [(None, 'big.txt', 17)])

    def test_edits_in_two_places_patch_the_diff_in_two_places(self):
        # auto-import сверху и вставка внизу: быстрая модель не должна
        # пометить изменённым всё между ними
        self.select('big.txt')
        self.h.set_focus('diff')
        self.h.view_mode = 'final'
        self.h.build_diff_rows()
        self.h.diff_cur = self.h.diff_lineno.index(21)
        self.h.start_comment()
        self.h.input_text('note')
        self.h.commit_input()
        self.edit('big.txt', line=25)
        self._defer_exact()
        self.h.apply_edits([((0, 0), (0, 0), 'import x\n'), ((24, 7), (24, 7), '!')],
                           caret=(25, 8))
        marked = [i for i, m in enumerate(self.h.diff_marks) if m]
        self.assertEqual(marked, [0, 1, 16, 25])
        self.assertEqual(self.h.edit_buf.caret, (25, 8))
        self.assertEqual(list(self.h.annots), [(None, 'big.txt', 22)])
        self.key('z', super=True)
        self.assertEqual(self.h.edit_buf.lines[24], 'line 24')
        self.assertEqual(self.h.edit_buf.lines[0], 'line 0')

    def test_definition_in_the_same_file_moves_the_caret(self):
        self.edit('big.txt', line=1)
        self.h._navigate(Target('big.txt', 20, 'def', ''))
        self.assertTrue(self.h.editing)
        self.assertEqual(self.h.edit_buf.line, 19)

    def test_definition_elsewhere_saves_first(self):
        self.edit('a.txt')
        self.h.on_text('>')
        self.h._navigate(Target('same.txt', 1, 'def', ''))
        self.assertFalse(self.h.editing)
        self.assertEqual(self.read('a.txt'), '>alpha edited\n')
        self.assertEqual(self.h._external, 'same.txt')

    def test_click_in_code_moves_caret(self):
        self.edit('big.txt', line=1)
        code_x = self.h.left_width() + 3 + self.h._gutter_cols() + 2
        self.mouse(code_x + 3, 2 + 4, EventType.PRESS, EventType.RELEASE)
        self.assertTrue(self.h.editing)
        self.assertEqual(self.h.edit_buf.caret, (4, 3))

    def _marker_cell(self):
        di = self.h.diff_hunks[0]
        return self.h.left_width() + len(SEP), 2 + di - self.h.diff_offset

    def test_revert_marker_click_with_a_jitter_still_reverts(self):
        self.edit('big.txt', line=16)
        x, y = self._marker_cell()
        self.mouse(x, y, EventType.PRESS, EventType.MOVE, EventType.RELEASE)
        self.assertEqual(self.h.edit_buf.lines[15], 'line 15')

    def test_drag_from_the_revert_marker_does_not_select_in_the_viewer(self):
        self.edit('big.txt', line=16)
        caret = self.h.edit_buf.caret
        x, y = self._marker_cell()
        self.mouse(x, y, EventType.PRESS)
        self.mouse(x + 8, y, EventType.MOVE, EventType.RELEASE)
        self.assertIsNone(self.h.diff_char_sel)
        self.assertIsNone(self.h.diff_sel)
        self.assertEqual(self.h.edit_buf.caret, caret)
        self.assertEqual(self.h.edit_buf.lines[15], 'line CHANGED')

    def test_back_returns_to_the_same_file_after_the_tree_changed(self):
        self.select('c.txt')
        self.h._navigate(Target('same.txt', 1, 'def', ''))
        self._git('checkout', 'a.txt')     # файл выше по дереву пропал
        self.h._reload_items()
        self.h.rebuild_tree()
        self.h.nav_back()
        self.assertEqual(self.h.current_item()['path'], 'c.txt')

    # --- пересчёт диффа на паузе ---

    def _defer_exact(self):
        # таймер паузы копится до run_pending, фоновая работа — в jobs:
        # иначе точный пересчёт подменял бы быструю модель в том же
        # нажатии, и её никто бы не проверял
        self.h.asyncio_loop.SYNC_MAX = EXACT_DELAY / 2
        self.jobs = []
        self.h.run_background = lambda work, done: self.jobs.append((work, done))

    def _run_job(self):
        work, done = self.jobs.pop(0)
        done(work())

    def test_typing_marks_come_from_the_fast_model_and_match_the_exact_one(self):
        self.edit('big.txt', line=3)
        self._defer_exact()
        self.key('END')
        self.h.on_text(' new')
        self.key('ENTER')
        self.assertEqual(self.jobs, [])
        fast = list(self.h.diff_marks)
        self.assertEqual(fast[2:4], ['mod', 'add'])
        self.h.asyncio_loop.run_pending()
        self._run_job()
        self.assertEqual(self.h.diff_marks, fast)

    def test_one_recompute_at_a_time_and_a_stale_one_is_dropped(self):
        self.edit('big.txt', line=3)
        self._defer_exact()
        self.h.on_text('a')
        self.h.asyncio_loop.run_pending()
        self.h.on_text('b')
        self.h.asyncio_loop.run_pending()
        self.assertEqual(len(self.jobs), 1)      # второй ждёт первого
        self._run_job()
        # результат по «a» устарел: модель не откатилась, а пересчёт
        # запущен заново по «ab»
        self.assertEqual(self.h.diff_src.b[2], 'abline 2')
        self.assertEqual(len(self.jobs), 1)
        self._run_job()
        self.assertEqual(self.h.diff_src.b[2], 'abline 2')
        self.assertEqual(self.jobs, [])


class TreeReselectTest(unittest.TestCase):
    """Пересборка дерева держит курсор на том же файле, даже когда
    список файлов перед ним поменялся.
    """

    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        self.repo = tempfile.mkdtemp(prefix='ccrev_tree_')
        subprocess.run(['git', '-C', self.repo, 'init', '-b', 'main'], check=True,
                       capture_output=True, env=os.environ)
        for name in ('a.txt', 'b.txt', 'c.txt'):
            with open(os.path.join(self.repo, name), 'w') as f:
                f.write(name)
        self.h = R.ReviewHandler([], Workspace.single(self.repo))
        wire(self.h, rows=30, cols=120)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_selection_survives_removal_of_an_earlier_file(self):
        subprocess.run(['git', '-C', self.repo, 'add', '-A'], check=True,
                       capture_output=True, env=os.environ)
        subprocess.run(['git', '-C', self.repo, 'commit', '-m', 'i'], check=True,
                       capture_output=True, env=os.environ)
        for name in ('a.txt', 'b.txt', 'c.txt'):
            with open(os.path.join(self.repo, name), 'a') as f:
                f.write('!')
        self.h.load_source()
        self.h.tsel = next(i for i, r in enumerate(self.h.rows) if r.get('name') == 'b.txt')
        subprocess.run(['git', '-C', self.repo, 'checkout', 'a.txt'], check=True,
                       capture_output=True, env=os.environ)
        self.h._reload_items()
        self.h.rebuild_tree()
        self.assertEqual(self.h.current_item()['path'], 'b.txt')

    def test_removed_file_hands_the_cursor_to_the_next_file_not_a_folder(self):
        for name in ('a.txt', 'b.txt', 'c.txt'):
            os.remove(os.path.join(self.repo, name))
        for rel in ('src/a1.txt', 'src/a2.txt', 'tests/b1.txt'):
            os.makedirs(os.path.dirname(os.path.join(self.repo, rel)), exist_ok=True)
            with open(os.path.join(self.repo, rel), 'w') as f:
                f.write(rel)
        subprocess.run(['git', '-C', self.repo, 'add', '-A'], check=True,
                       capture_output=True, env=os.environ)
        subprocess.run(['git', '-C', self.repo, 'commit', '-m', 'i'], check=True,
                       capture_output=True, env=os.environ)
        for rel in ('src/a1.txt', 'src/a2.txt', 'tests/b1.txt'):
            with open(os.path.join(self.repo, rel), 'a') as f:
                f.write('!')
        self.h.load_source()
        self.h.tsel = next(i for i, r in enumerate(self.h.rows) if r.get('name') == 'a2.txt')
        subprocess.run(['git', '-C', self.repo, 'checkout', 'src/a2.txt'], check=True,
                       capture_output=True, env=os.environ)
        self.h._reload_items()
        self.h.rebuild_tree()
        self.assertEqual(self.h.current_item()['path'], 'tests/b1.txt')


if __name__ == '__main__':
    unittest.main()
