import base64
import os
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

import kittymock  # noqa: F401
import review as R
from kittymock import EventType, MouseButton, MouseEvent, draw_text, wire
from modules.vcs.diff import DiffSource, group_key, is_code_row


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
        # рабочие правки: модификация в середине большого файла,
        # правка в подпапке, новый файл
        self.write('big.txt', ''.join((f'line {i}\n' if i != 15 else 'line CHANGED\n')
                                      for i in range(30)))
        self.write('dir/sub.txt', 'sub edited\n')
        self.write('new.txt', 'brand new\n')

        self.h = R.ReviewHandler([], self.repo, self.repo)
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

    def _expand_unversioned(self):
        self.h.collapsed.discard(group_key(R.UNVERSIONED))
        self.h.rebuild_tree()

    def _select_file(self, basename):
        for _ in range(2):
            for i, r in enumerate(self.h.rows):
                if r['type'] == 'file' and r['name'] == basename:
                    self.h.tsel = i
                    self.h.load_diff()
                    return
            self._expand_unversioned()
        self.fail(f'файл {basename} не найден в дереве')

    # --- дерево и источник ---

    def test_tree_built(self):
        names = [r['name'] for r in self.h.rows]
        self.assertIn('big.txt', names)
        self.assertIn('dir', names)                 # папка-узел
        self.assertIsNotNone(self.h.current_item())

    def test_file_count_includes_files_hidden_in_collapsed_group(self):
        visible_files = sum(1 for r in self.h.rows if r['type'] == 'file')
        self.assertEqual(visible_files, 2)          # new.txt свёрнут в группе
        self.assertEqual(self.h.n_files, 3)

    def test_untracked_grouped_and_collapsed(self):
        grp = [r for r in self.h.rows if r.get('group')]
        self.assertEqual(len(grp), 1)
        self.assertEqual(grp[0]['name'], R.UNVERSIONED)
        self.assertTrue(grp[0]['collapsed'])
        self.assertEqual(grp[0]['count'], 1)
        self.assertEqual(grp[0]['depth'], 0)
        self.assertNotIn('new.txt', [r['name'] for r in self.h.rows])
        self._expand_unversioned()
        self.assertIn('new.txt', [r['name'] for r in self.h.rows])

    def test_unversioned_group_node_is_not_a_path(self):
        self.h.tsel = next(i for i, r in enumerate(self.h.rows) if r.get('group_root'))
        self.assertIsNone(self.h._current_rel())

    def test_untracked_marked(self):
        self._select_file('new.txt')
        self.assertEqual(self.h.current_item()['kind'], 'untracked')

    # --- git add из дерева ---

    def _status(self):
        out = subprocess.run(['git', '-C', self.repo, 'status', '--porcelain', '-uall'],
                             capture_output=True, text=True, env=os.environ).stdout
        return {ln[3:]: ln[:2] for ln in out.splitlines()}

    def _select_row(self, pred):
        self.h.tsel = next(i for i, r in enumerate(self.h.rows) if pred(r))

    def test_stage_file(self):
        self._select_file('new.txt')
        self.h.stage_selected()
        self.assertEqual(self._status()['new.txt'], 'A ')

    def test_stage_folder_takes_all_its_files(self):
        self.write('dir/second.txt', 'исходный\n')
        self._git('add', 'dir/second.txt')     # только он, правки setUp остаются рабочими
        self._git('commit', '-m', 'second')
        self.write('dir/second.txt', 'правка\n')
        self.h.refresh()
        self._select_row(lambda r: r['type'] == 'dir' and r['name'] == 'dir')
        self.assertEqual(sorted(self.h._selected_paths()),
                         ['dir/second.txt', 'dir/sub.txt'])
        self.h.stage_selected()
        st = self._status()
        self.assertEqual((st['dir/sub.txt'], st['dir/second.txt']), ('M ', 'M '))

    def test_stage_unversioned_group_node(self):
        self.write('another.txt', 'ещё\n')
        self.h.refresh()
        self._select_row(lambda r: r.get('group_root'))
        self.assertEqual(sorted(self.h._selected_paths()), ['another.txt', 'new.txt'])
        self.h.stage_selected()
        st = self._status()
        self.assertEqual((st['new.txt'], st['another.txt']), ('A ', 'A '))

    def test_stage_skips_hidden_noise(self):
        self.write('node_modules/junk.js', 'x\n')
        self.h.refresh()
        self._select_row(lambda r: r.get('group_root'))
        self.assertNotIn('node_modules/junk.js', self.h._selected_paths())

    def test_already_staged_file_offers_nothing(self):
        self._select_file('new.txt')
        self.h.stage_selected()
        self._select_file('new.txt')
        self.assertEqual(self.h._selected_paths(), [])
        self.h.out = []
        self.h.draw_screen()
        self.assertNotIn('+ stage', draw_text(self.h))

    def test_stage_hint_shown_when_there_is_something_to_add(self):
        self._select_file('big.txt')      # изменён в setUp
        self.h.out = []
        self.h.draw_screen()
        self.assertIn('+ stage', draw_text(self.h))

    def test_folder_offers_stage_only_while_it_holds_unstaged_files(self):
        self._select_row(lambda r: r['type'] == 'dir' and r['name'] == 'dir')
        self.assertEqual(self.h._selected_paths(), ['dir/sub.txt'])
        self.h.stage_selected()
        self._select_row(lambda r: r['type'] == 'dir' and r['name'] == 'dir')
        self.assertEqual(self.h._selected_paths(), [])

    def test_folder_inside_group_does_not_grab_namesake_outside(self):
        self.write('dir/fresh.txt', 'новый в отслеживаемой папке\n')
        self.h.refresh()
        self._expand_unversioned()
        inside = [r for r in self.h.rows
                  if r['type'] == 'dir' and r['name'] == 'dir' and r.get('group')]
        self.assertEqual(len(inside), 1)
        self.h.tsel = self.h.rows.index(inside[0])
        self.assertEqual(self.h._selected_paths(), ['dir/fresh.txt'])

    # --- метки / множественный выбор файлов ---

    def _file_row(self, basename):
        for i, r in enumerate(self.h.rows):
            if r['type'] == 'file' and r['name'] == basename:
                return i
        self.fail(f'файл {basename} не найден в дереве')

    def _mod_click(self, li, mods):
        ev = MouseEvent(cell_x=1, cell_y=li - self.h.left_offset + 2,
                        buttons=MouseButton.LEFT, mods=mods)
        self.h.on_mouse_event(ev)

    def _alt_click(self, li):
        self._mod_click(li, mods=2)   # ⌥ = 2 в схеме kitty

    def _shift_click(self, li):
        self._mod_click(li, mods=1)   # ⇧ = 1 в схеме kitty

    def _plain_click(self, li):
        self.h.on_click(MouseEvent(cell_x=1, cell_y=li - self.h.left_offset + 2))

    def test_alt_click_marks_files_and_copy_joins_paths(self):
        self.h.tsel = self._file_row('big.txt')
        self.h.load_diff()
        self._alt_click(self._file_row('sub.txt'))
        copied = []
        self.h._copy_clipboard = copied.append
        self.h.on_key(kittymock.KeyEvent('c', super=True))   # ⌘c
        self.assertEqual(len(copied), 1)
        self.assertEqual(set(copied[0].split('\n')), {'@big.txt', '@dir/sub.txt'})

    def test_alt_click_toggles_a_file_off_while_others_marked(self):
        big, sub = self._file_row('big.txt'), self._file_row('sub.txt')
        self.h.tsel = big
        self.h.load_diff()
        self._alt_click(sub)
        self.assertEqual(self.h.marked_paths, {'big.txt', 'dir/sub.txt'})
        self._alt_click(big)
        self.assertEqual(self.h.marked_paths, {'dir/sub.txt'})

    def test_alt_click_adds_to_active_selection_without_moving_cursor(self):
        # ⌥+клик по другому файлу даёт ДВА выделения: активный файл
        # остаётся в выборке, курсор не двигается
        big = self._file_row('big.txt')
        self.h.tsel = big
        self.h.load_diff()
        sub = self._file_row('sub.txt')
        self._alt_click(sub)
        self.assertEqual(self.h.marked_paths, {'big.txt', 'dir/sub.txt'})
        self.assertEqual(self.h.tsel, big)
        self.assertTrue(self.h._row_highlight(big))
        self.assertTrue(self.h._row_highlight(sub))

    def test_alt_click_unhighlights_the_cursor_row(self):
        big = self._file_row('big.txt')
        self.h.tsel = big
        self.h.load_diff()
        self._alt_click(self._file_row('sub.txt'))
        self._alt_click(big)   # снять активный файл из выборки
        self.assertEqual(self.h.marked_paths, {'dir/sub.txt'})
        self.assertFalse(self.h._row_highlight(big))

    def test_alt_click_on_last_mark_is_a_noop(self):
        big = self._file_row('big.txt')
        self.h.tsel = big
        self.h.load_diff()
        sub = self._file_row('sub.txt')
        self._alt_click(sub)
        self._alt_click(big)   # осталась одна метка — sub
        self._alt_click(sub)   # последнюю метку клик не снимает
        self.assertEqual(self.h.marked_paths, {'dir/sub.txt'})
        self.assertEqual(self.h.tsel, big)
        self.assertTrue(self.h._row_highlight(sub))

    def test_shift_click_paints_range_and_keeps_first_file(self):
        # курсор на big.txt, ⇧+клик по sub.txt — выделены оба
        self.h.tsel = self._file_row('big.txt')
        self.h.load_diff()
        self._shift_click(self._file_row('sub.txt'))
        self.assertEqual(self.h.marked_paths, {'big.txt', 'dir/sub.txt'})
        self.assertEqual(self.h.tsel, self._file_row('sub.txt'))

    def test_second_shift_click_chains_the_range(self):
        self.h.tsel = self._file_row('sub.txt')
        self.h.load_diff()
        self._shift_click(self._file_row('big.txt'))
        self._expand_unversioned()
        self._shift_click(self._file_row('new.txt'))
        self.assertEqual(self.h.marked_paths,
                         {'dir/sub.txt', 'big.txt', 'new.txt'})

    def test_alt_click_on_folder_marks_all_its_files(self):
        di = next(i for i, r in enumerate(self.h.rows)
                  if r['type'] == 'dir' and r['name'] == 'dir')
        self._alt_click(di)
        self.assertIn('dir/sub.txt', self.h.marked_paths)

    def test_shift_click_back_toward_anchor_drops_the_tail(self):
        self._expand_unversioned()
        self.h.tsel = self._file_row('sub.txt')
        self.h.load_diff()
        self._shift_click(self._file_row('new.txt'))
        self.assertEqual(self.h.marked_paths,
                         {'dir/sub.txt', 'big.txt', 'new.txt'})
        self._shift_click(self._file_row('big.txt'))   # ближе к якорю
        self.assertEqual(self.h.marked_paths, {'dir/sub.txt', 'big.txt'})
        self._shift_click(self._file_row('sub.txt'))   # в сам якорь
        self.assertEqual(self.h.marked_paths, {'dir/sub.txt'})

    def test_shift_click_below_after_ranging_up_drops_the_upper_range(self):
        self._expand_unversioned()
        self.h.tsel = self._file_row('big.txt')
        self.h.load_diff()
        self._shift_click(self._file_row('sub.txt'))   # вверх от якоря
        self.assertEqual(self.h.marked_paths, {'big.txt', 'dir/sub.txt'})
        self._shift_click(self._file_row('new.txt'))   # вниз через якорь
        self.assertEqual(self.h.marked_paths, {'big.txt', 'new.txt'})

    def test_shift_up_after_shift_down_unmarks_the_left_row(self):
        self.h.tsel = self._file_row('sub.txt')
        self.h.load_diff()
        self.h.on_key(kittymock.KeyEvent('DOWN', shift=True))
        self.assertEqual(self.h.marked_paths, {'dir/sub.txt', 'big.txt'})
        self.h.on_key(kittymock.KeyEvent('UP', shift=True))
        self.assertEqual(self.h.marked_paths, {'dir/sub.txt'})

    def test_shift_down_paints_a_range_of_two_files(self):
        self.h.tsel = self.h._first_file()
        self.h.load_diff()
        self.h.on_key(kittymock.KeyEvent('DOWN', shift=True))
        self.assertEqual(len(self.h.marked_paths), 2)
        self.assertIn(self.h.current_item()['rel'], self.h.marked_paths)

    def test_shift_up_skips_the_expanded_dir_and_stops_at_the_edge(self):
        # выше sub только развёрнутая папка — шаг вверх упирается в
        # край и ничего не красит, курсор на месте
        sub = self._file_row('sub.txt')
        self.h.tsel = sub
        self.h.load_diff()
        self.h.on_key(kittymock.KeyEvent('UP', shift=True))
        self.assertEqual(self.h.tsel, sub)
        self.assertEqual(self.h.marked_paths, set())

    def test_cursor_on_dir_row_is_highlighted_without_marks(self):
        d = next(i for i, r in enumerate(self.h.rows)
                 if r['type'] == 'dir' and r['name'] == 'dir')
        self.h.tsel = d
        self.assertTrue(self.h._row_highlight(d))

    def test_shift_down_jumps_over_the_expanded_group_row(self):
        # развёрнутая группа файлов не вносит — шаг через неё не пустой
        self._expand_unversioned()
        self.h.tsel = self._file_row('big.txt')
        self.h.load_diff()
        self.h.on_key(kittymock.KeyEvent('DOWN', shift=True))
        self.assertEqual(self.h.marked_paths, {'big.txt', 'new.txt'})
        self.assertEqual(self.h.rows[self.h.tsel]['name'], 'new.txt')

    def test_shift_range_over_collapsed_group_marks_its_files(self):
        # свёрнутая группа видна одной строкой — диапазон через неё
        # забирает всё её содержимое
        self.h.tsel = self._file_row('big.txt')
        self.h.load_diff()
        self.h.on_key(kittymock.KeyEvent('DOWN', shift=True))
        self.assertIn('new.txt', self.h.marked_paths)

    def test_shift_click_on_marked_file_keeps_marks(self):
        self.h.tsel = self._file_row('big.txt')
        self.h.load_diff()
        self._shift_click(self._file_row('sub.txt'))   # помечены оба
        self._shift_click(self._file_row('sub.txt'))   # ⇧ не снимает
        self.assertEqual(self.h.marked_paths, {'big.txt', 'dir/sub.txt'})

    def test_alt_click_unmarks_from_afar_without_moving_cursor(self):
        self.h.tsel = self._file_row('big.txt')
        self.h.load_diff()
        self._shift_click(self._file_row('sub.txt'))   # курсор на sub
        self._alt_click(self._file_row('big.txt'))     # снять big издалека
        self.assertEqual(self.h.marked_paths, {'dir/sub.txt'})
        self.assertEqual(self.h.tsel, self._file_row('sub.txt'))

    def test_plain_click_clears_marks(self):
        self._alt_click(self._file_row('big.txt'))
        self.assertTrue(self.h.marked_paths)
        self._plain_click(self._file_row('sub.txt'))   # без ⌥ — навигация
        self.assertEqual(self.h.marked_paths, set())

    def test_plain_arrow_navigation_clears_marks(self):
        self._alt_click(self._file_row('big.txt'))
        self.assertTrue(self.h.marked_paths)
        self.h.on_key(kittymock.KeyEvent('DOWN'))   # без Shift — навигация
        self.assertEqual(self.h.marked_paths, set())

    def test_escape_clears_marks_before_offering_to_quit(self):
        self._alt_click(self._file_row('big.txt'))
        self.h.on_key(kittymock.KeyEvent('ESCAPE'))
        self.assertEqual(self.h.marked_paths, set())

    def test_copy_without_marks_falls_back_to_single_path(self):
        self._select_file('big.txt')
        copied = []
        self.h._copy_clipboard = copied.append
        self.h.on_key(kittymock.KeyEvent('c', super=True))
        self.assertEqual(copied, ['@big.txt'])

    # --- откат изменений ---

    def test_revert_asks_before_touching_anything(self):
        self._select_file('big.txt')
        self.h.start_revert()
        self.assertIsNotNone(self.h.pending_revert)
        self.assertIn('big.txt', self._status())
        self.h.out = []
        self.h.draw_screen()
        self.assertIn('revert 1 file', draw_text(self.h))

    def test_revert_confirmed_with_y_restores_file(self):
        self._select_file('big.txt')
        self.h.start_revert()
        self.h.on_text('y')
        self.assertNotIn('big.txt', self._status())
        self.assertIsNone(self.h.pending_revert)

    def test_revert_cancelled_by_any_other_key(self):
        self._select_file('big.txt')
        self.h.start_revert()
        self.h.on_text('n')
        self.assertIsNone(self.h.pending_revert)
        self.assertIn('big.txt', self._status())

    def test_enter_does_not_confirm_revert(self):
        self._select_file('big.txt')
        self.h.start_revert()
        self.h.on_key(kittymock.KeyEvent('ENTER'))
        self.assertIsNone(self.h.pending_revert)
        self.assertIn('big.txt', self._status())

    def test_revert_undoes_staged_changes_too(self):
        self._select_file('big.txt')
        self.h.stage_selected()
        self.assertEqual(self._status()['big.txt'], 'M ')
        self._select_file('big.txt')
        self.h.start_revert()
        self.h.on_text('y')
        self.assertNotIn('big.txt', self._status())

    def test_revert_deletes_untracked_file(self):
        self._select_file('new.txt')
        self.h.start_revert()
        self.h.on_text('y')
        self.assertFalse(os.path.exists(os.path.join(self.repo, 'new.txt')))

    def test_revert_prompt_warns_about_deletion(self):
        self._select_row(lambda r: r.get('group_root'))
        self.h.start_revert()
        self.h.out = []
        self.h.draw_screen()
        self.assertIn('will be deleted for good', draw_text(self.h))

    def test_revert_of_folder_takes_all_its_files(self):
        self._select_row(lambda r: r['type'] == 'dir' and r['name'] == 'dir')
        tracked, untracked = self.h._revert_targets()
        self.assertEqual((tracked, untracked), (['dir/sub.txt'], []))

    # --- навигация ---

    def test_move_is_bounded(self):
        self.h.tsel = 0
        self.h.tree_move(-5)
        self.assertEqual(self.h.tsel, 0)
        self.h.tree_move(999)
        self.assertEqual(self.h.tsel, len(self.h.rows) - 1)

    # --- скролл дерева (колесо) и полоса прокрутки ---

    def _cramped(self):
        """Окно ниже дерева: видимых строк меньше, чем строк дерева
        (иначе скроллить нечего и полосы нет).
        """
        wire(self.h, rows=2 + 3, cols=120)   # 3 строки съедают шапка и футер
        self._expand_unversioned()
        self.h.tsel = 0

    def test_no_scrollbar_when_tree_fits(self):
        self.assertIsNone(self.h._scrollbar())

    def test_wheel_scrolls_tree_without_moving_selection_or_reloading_diff(self):
        self._cramped()
        diff_loads = []
        self.h.load_diff = lambda: diff_loads.append(1)
        self.h.tree_scroll(1)
        self.assertEqual((self.h.left_offset, self.h.tsel), (1, 0))
        self.assertEqual(diff_loads, [])

    def test_tree_scroll_is_bounded_and_thumb_reaches_bottom(self):
        self._cramped()
        vis = self.h.visible_rows()
        self.h.tree_scroll(999)
        self.assertEqual(self.h.left_offset, len(self.h.rows) - vis)
        pos, size = self.h._scrollbar()
        self.assertEqual(pos + size, vis)
        self.h.tree_scroll(-999)
        self.assertEqual(self.h.left_offset, 0)
        self.assertEqual(self.h._scrollbar()[0], 0)

    def test_arrow_pulls_scroll_back_to_cursor(self):
        self._cramped()
        self.h.tree_scroll(999)
        self.h.tree_move(1)
        self.assertEqual(self.h.tsel, 1)
        self.assertLessEqual(self.h.left_offset, self.h.tsel)
        self.assertLess(self.h.tsel, self.h.left_offset + self.h.visible_rows())

    def test_diff_pane_has_its_own_thumb(self):
        self._select_file('big.txt')
        self.h.expand = True
        self.h.build_diff_rows()
        wire(self.h, rows=8, cols=80)
        self.h.build_diff_rows()
        vis = self.h.visible_rows()
        self.assertGreater(len(self.h.diff_rows), vis)
        top = self.h._thumb(0, len(self.h.diff_rows), vis)
        self.assertEqual(top[0], 0)
        self.h.diff_scroll(999)
        bottom = self.h._thumb(self.h.diff_offset, len(self.h.diff_rows), vis)
        self.assertEqual(bottom[0] + bottom[1], vis)

    def test_change_map_marks_ride_the_diff_scrollbar(self):
        self._select_file('big.txt')
        self.assertIn('add', self.h.diff_marks)          # unified: фон строк → метки
        self.h.draw_screen()
        # риска стоит в своей колонке (символ ещё и разделяет панели)
        map_col = f'\x1b[{self.h.screen_size.cols - 1}G│'
        self.assertIn(map_col, draw_text(self.h))

    def test_change_map_marks_follow_the_final_view(self):
        self._select_file('big.txt')
        self.h.toggle_view_mode()
        # в final метки идут по строкам файла: правка была на 16-й
        self.assertEqual(self.h.diff_marks[15], 'mod')
        self.assertEqual(len(self.h.diff_marks), len(self.h.diff_rows))
        self.assertIsNone(self.h.diff_marks[0])

    def test_change_map_has_its_own_column_next_to_the_thumb(self):
        cmap = ['add', None]
        self.assertIn('│', self.h._change_cell(cmap, 0))    # тонкая риска, не жирный ползунок
        self.assertNotIn('┃', self.h._change_cell(cmap, 0))
        self.assertEqual(self.h._change_cell(cmap, 1), ' ')
        self.assertEqual(self.h._change_cell(cmap, 9), ' ')   # за пределами карты
        # ползунок живёт отдельно и о карте не знает
        self.assertIn('┃', self.h._thumb_cell((0, 1), 0))

    def test_thumb_and_map_columns_are_reserved_in_both_panes(self):
        separator = 3                    # ползунок дерева живёт внутри left_width()
        change_map_col, diff_thumb = 1, 1
        self.assertEqual(
            self.h.left_width() + separator + self.h.diff_width()
            + change_map_col + diff_thumb,
            self.h.screen_size.cols)

    def test_wheel_over_tree_scrolls_wheel_over_diff_scrolls_diff(self):
        self._cramped()
        calls = []
        self.h.tree_scroll = lambda d: calls.append(('tree', d))
        self.h.diff_scroll = lambda d: calls.append(('diff', d))

        class Ev:
            buttons = kittymock.MouseButton.WHEEL_DOWN
            cell_x = 0
            cell_y = 5

        self.h.on_mouse_event(Ev())
        Ev.cell_x = self.h.left_width() + 5
        self.h.on_mouse_event(Ev())
        self.assertEqual(calls, [('tree', 3), ('diff', 3)])

    def test_pointer_shape_follows_hover_zone(self):
        self._select_file('big.txt')
        lw = self.h.left_width()

        def move(x, y):
            self.h.out.clear()
            self.h.on_mouse_event(
                MouseEvent(cell_x=x, cell_y=y, type=EventType.MOVE))

        # строка кода (правее гуттера номеров) → текстовый курсор
        code_y = next(y for y in range(2, self.h.visible_rows() + 2)
                      if self.h._diff_row_at(MouseEvent(cell_x=lw + 5, cell_y=y))
                      is not None
                      and self.h._gap_at(self.h._diff_row_at(
                          MouseEvent(cell_x=lw + 5, cell_y=y))) is None)
        code_x = lw + 3 + self.h._gutter_cols() + 3
        move(code_x, code_y)
        self.assertEqual(self.h._pointer_shape, 'text')
        self.assertIn('\x1b]22;>text\x1b\\', self.h.out)

        # папка в дереве → рука; на смене зоны сначала pop, потом push
        dir_row = next(i for i, r in enumerate(self.h.rows) if r['type'] == 'dir')
        move(1, dir_row - self.h.left_offset + 2)
        self.assertEqual(self.h._pointer_shape, 'pointer')
        self.assertEqual(self.h.out,
                         ['\x1b]22;<\x1b\\', '\x1b]22;>pointer\x1b\\'])

        # файл в дереве (кликабелен, но не «раскрытие») → стрелка
        file_row = next(i for i, r in enumerate(self.h.rows) if r['type'] == 'file')
        move(1, file_row - self.h.left_offset + 2)
        self.assertIsNone(self.h._pointer_shape)
        self.assertEqual(self.h.out, ['\x1b]22;<\x1b\\'])

        # повторное движение в той же зоне — без нового escape
        move(2, file_row - self.h.left_offset + 2)
        self.assertEqual(self.h.out, [])

    def test_fast_tree_scroll_defers_diff_load(self):
        scheduled = []

        class Timer:
            def __init__(self):
                self.cancelled = False

            def cancel(self):
                self.cancelled = True

        class DeferredLoop:
            def call_later(self, delay, cb, *args):
                t = Timer()
                scheduled.append((t, cb))
                return t

        self.h.asyncio_loop = DeferredLoop()
        loads = []
        self.h.load_diff = lambda: loads.append(self.h.tsel)
        self.h.tsel = 0
        self.h.tree_move(1)
        self.h.tree_move(1)
        self.assertEqual(loads, [])                    # во время прокрутки дифф не грузится
        self.assertTrue(scheduled[0][0].cancelled)     # прежний таймер отменён
        self.assertFalse(scheduled[1][0].cancelled)
        scheduled[-1][1]()                             # прокрутка утихла — таймер сработал
        self.assertEqual(loads, [2])                   # одна загрузка, по итоговой позиции

    def test_draw_screen_smoke(self):
        self.h.draw_screen()
        text = draw_text(self.h)
        self.assertTrue(self.h.out)
        self.assertIn('[tree]', text)              # футер режима дерева

    def test_draw_screen_is_atomic_frame(self):
        calls = []

        class RecordingCmd:
            def __getattr__(self, name):
                return lambda *a, **k: calls.append(name)

        self.h.cmd = RecordingCmd()
        self.h.draw_screen()
        # кадр обёрнут в synchronized update (mode 2026) —
        # иначе панели мигают
        self.assertEqual(calls[0], 'set_mode')
        self.assertEqual(calls[-1], 'reset_mode')
        self.assertIn('clear_screen', calls)

    # --- flash: сообщение поверх футера гаснет само ---

    def _defer_timers(self):
        """asyncio_loop, копящий колбэки вместо немедленного вызова."""
        scheduled = []

        class Timer:
            def __init__(self):
                self.cancelled = False

            def cancel(self):
                self.cancelled = True

        class DeferredLoop:
            def call_later(self, delay, cb, *args):
                t = Timer()
                scheduled.append((t, delay, cb))
                return t

        self.h.asyncio_loop = DeferredLoop()
        return scheduled

    def test_flash_expires_and_footer_comes_back(self):
        scheduled = self._defer_timers()
        self.h.flash = 'unified diff'
        self.h.draw_screen()
        self.assertIn('unified diff', draw_text(self.h))
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][1], self.h.FLASH_TTL)
        self.h.out = []
        scheduled[0][2]()                            # таймер сработал
        text = draw_text(self.h)
        self.assertNotIn('unified diff', text)
        self.assertIn('[tree]', text)                # подсказки футера вернулись

    def test_frame_without_flash_arms_no_timer(self):
        scheduled = self._defer_timers()
        self.h.draw_screen()
        self.assertEqual(scheduled, [])

    def test_new_flash_restarts_the_countdown(self):
        scheduled = self._defer_timers()
        self.h.flash = 'first'
        self.h.draw_screen()
        self.h.flash = 'second'
        self.h.draw_screen()
        self.assertTrue(scheduled[0][0].cancelled)   # прежний отсчёт неактуален
        self.assertFalse(scheduled[1][0].cancelled)

    def test_flash_timer_cancelled_by_plain_redraw(self):
        scheduled = self._defer_timers()
        self.h.flash = 'copied'
        self.h.draw_screen()
        self.h.draw_screen()                         # обычный кадр, flash уже снят
        self.assertTrue(scheduled[0][0].cancelled)
        self.assertEqual(len(scheduled), 1)
        self.assertIsNone(self.h._flash_timer)

    # --- фильтр ---

    def test_filter_narrows_tree(self):
        self.h.filter_query = 'big'
        self.h.rebuild_tree()
        names = [r['name'] for r in self.h.rows if r['type'] == 'file']
        self.assertEqual(names, ['big.txt'])
        self.assertEqual(self.h.n_files, 1)

    def test_escape_clears_filter_then_asks_to_close(self):
        self.h.filter_query = 'big'
        self.h.rebuild_tree()
        self.h.on_key(kittymock.KeyEvent('ESCAPE'))   # применённый фильтр сбрасывается
        self.assertEqual(self.h.filter_query, '')
        self.h.on_key(kittymock.KeyEvent('ESCAPE'))   # дно каскада: вопрос вместо выхода
        self.assertTrue(self.h.confirm_active)
        self.assertEqual(self.h.quits, [])
        self.h.on_text('y')
        self.assertEqual(self.h.quits, [0])

    # --- шум ---

    def test_toggle_noise(self):
        self.assertFalse(self.h.show_noise)
        self.h.toggle_noise()
        self.assertTrue(self.h.show_noise)

    # --- ошибки git ---

    def test_git_error_shown_when_scan_fails(self):
        d = tempfile.mkdtemp(prefix='ccrev_notrepo_')
        try:
            self.h.root = d
            self.h.load_source()
            self.assertEqual(self.h.items, [])
            self.assertIn('not a git repository', self.h.status)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # --- бинарные файлы ---

    def test_binary_file_shows_placeholder(self):
        with open(os.path.join(self.repo, 'blob.bin'), 'wb') as f:
            f.write(b'\x00\x01\x02data\x00')
        self.h.refresh()
        self._select_file('blob.bin')
        self.assertEqual(self.h.diff_plain, ['  (binary file)'])
        self.assertEqual(self.h.diff_lineno, [0])   # не строка кода: копировать нечего

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

    def _dir_row(self):
        return next(i for i, r in enumerate(self.h.rows) if r['type'] == 'dir')

    def _click_tree(self, row_idx):
        self.h.on_click(MouseEvent(cell_x=1, cell_y=row_idx - self.h.left_offset + 2))

    def test_first_click_on_folder_only_selects_it(self):
        di = self._dir_row()
        self.h.tsel = self.h._first_file()
        self._click_tree(di)
        self.assertEqual(self.h.tsel, di)
        self.assertNotIn(self.h.rows[di]['key'], self.h.collapsed)

    def test_second_click_on_the_selected_folder_folds_it(self):
        di = self._dir_row()
        self.h.tsel = self.h._first_file()
        self._click_tree(di)
        self._click_tree(di)
        self.assertIn(self.h.rows[di]['key'], self.h.collapsed)

    def test_click_from_diff_focus_selects_before_folding(self):
        di = self._dir_row()
        self.h.tsel = di
        self.h.focus = 'diff'
        self._click_tree(di)
        self.assertEqual(self.h.focus, 'tree')
        self.assertNotIn(self.h.rows[di]['key'], self.h.collapsed)

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

    # --- final-вид (весь файл, маркеры на полях) ---

    def test_final_view_shows_whole_file_without_signs(self):
        self._select_file('big.txt')
        self.h.toggle_view_mode()
        self.assertEqual(self.h.view_mode, 'final')
        self.assertEqual(len(self.h.diff_rows), 30)          # весь файл, без свёртки
        self.assertEqual(self.h.diff_lineno, list(range(1, 31)))
        self.assertTrue(all(g is None for g in self.h.diff_gap))
        self.assertFalse(any('hidden' in p for p in self.h.diff_plain))
        self.assertIn('line CHANGED', self.h.diff_plain[15])
        self.assertFalse(any('line 15' in p for p in self.h.diff_plain))   # старой строки нет

    def test_toggle_view_keeps_cursor_on_same_source_line(self):
        self._select_file('big.txt')
        self.h.set_focus('diff')
        di = next(i for i, ln in enumerate(self.h.diff_lineno) if ln == 16)
        self.h.diff_cur = di
        self.h.toggle_view_mode()
        self.assertEqual(self.h.diff_lineno[self.h.diff_cur], 16)
        self.h.toggle_view_mode()                            # и обратно в unified
        self.assertEqual(self.h.view_mode, 'diff')
        self.assertEqual(self.h.diff_lineno[self.h.diff_cur], 16)

    def test_final_view_comments_land_on_right_line(self):
        self._select_file('big.txt')
        self.h.toggle_view_mode()
        self.h.set_focus('diff')
        self.h.diff_cur = 15                                 # строка «line CHANGED»
        self.h.start_comment()
        self.assertEqual(self.h.comment_target[1], 16)       # номер строки нового файла
        self.assertEqual(self.h.comment_target[2], 'line CHANGED')

    def test_expand_is_noop_in_final_view(self):
        self._select_file('big.txt')
        self.h.toggle_view_mode()
        rows = list(self.h.diff_rows)
        self.h.toggle_expand()
        self.assertFalse(self.h.expand)
        self.assertEqual(self.h.diff_rows, rows)

    def test_final_view_of_deleted_file_shows_placeholder(self):
        os.remove(os.path.join(self.repo, 'dir/sub.txt'))
        self.h.load_source()
        self._select_file('sub.txt')
        self.h.toggle_view_mode()
        self.assertIn('deleted', self.h.diff_plain[0])

    def test_view_mode_survives_file_switch(self):
        self._select_file('big.txt')
        self.h.toggle_view_mode()
        self._select_file('sub.txt')
        self.assertEqual(self.h.view_mode, 'final')
        self.assertEqual(self.h.diff_plain[0].strip().split(maxsplit=1)[1], '▎ sub edited')

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
        # flash гаснет к концу draw_screen, но текст и OSC52
        # уже попали в вывод
        self.assertIn('copied', draw_text(self.h))
        self.assertTrue(any('\x1b]52;c;' in str(x) for x in self.h.out))  # OSC52 в буфер

    def test_export_without_comments_keeps_hint(self):
        self.h.out = []
        self.h.export_review()
        self.assertIn('no comments', draw_text(self.h))
        self.assertFalse(any('\x1b]52;c;' in str(x) for x in self.h.out))

    def test_export_clears_annotations_and_markers(self):
        self._start_comment()
        self.h.input_buffer = 'первый'
        self.h.commit_input()
        rel = self.h.current_item()['rel']
        di = self.h._first_commentable(0)
        self.assertTrue(self.h._diff_annotated(di, rel))
        copied = []
        self.h._copy_clipboard = copied.append
        self.h.export_review()
        self.assertIn('первый', copied[0])
        self.assertEqual(self.h.annots, {})
        self.assertFalse(self.h._diff_annotated(di, rel))

    def _start_comment(self):
        self._select_file('big.txt')
        self.h.set_focus('diff')
        self.h.diff_cur = self.h._first_commentable(0)
        self.h.start_comment()

    def test_send_review_exits_with_comments_as_action(self):
        self._start_comment()
        self.h.input_buffer = 'нужен рефактор'
        self.h.commit_input()
        self.h.on_text('s')
        self.assertEqual(self.h.quits, [0])
        self.assertEqual(self.h.action['action'], 'send')
        self.assertIn('нужен рефактор', self.h.action['text'])

    def test_send_review_without_comments_stays_and_hints(self):
        self.h.out = []
        self.h.on_text('s')
        self.assertEqual(self.h.quits, [])
        self.assertIsNone(self.h.action)
        self.assertIn('no comments', draw_text(self.h))

    def test_handle_result_send_pastes_into_source_window(self):
        pasted = []
        w = SimpleNamespace(paste_text=pasted.append, tabref=lambda: None)
        boss = SimpleNamespace(window_id_map={7: w})
        R.handle_result([], {'action': 'send', 'text': '# Review comments'}, 7, boss)
        self.assertEqual(pasted, ['# Review comments'])

    def test_handle_result_send_ignores_closed_source_window(self):
        boss = SimpleNamespace(window_id_map={})
        R.handle_result([], {'action': 'send', 'text': 'x'}, 7, boss)

    def test_shift_enter_adds_newline_plain_enter_saves(self):
        self._start_comment()
        self.h.input_text('первая')
        self.h.input_key('ENTER', shift=True)
        self.h.input_text('вторая')
        self.assertEqual(self.h.input_mode, 'comment')
        self.h.input_key('ENTER')
        self.assertIsNone(self.h.input_mode)
        self.assertEqual(next(iter(self.h.annots.values()))['text'], 'первая\nвторая')

    def test_shift_enter_saves_in_single_line_modes(self):
        self.h.start_filter()
        self.h.input_text('abc')
        self.h.input_key('ENTER', shift=True)
        self.assertIsNone(self.h.input_mode)
        self.assertEqual(self.h.filter_query, 'abc')

    def test_long_comment_wraps_and_grows_input_area(self):
        self._start_comment()
        rows_before = self.h.visible_rows()
        self.h.input_buffer = 'слово ' * 60
        lines = self.h.input_lines(self.h.screen_size.cols)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(len(ln) <= self.h.screen_size.cols for ln in lines))
        self.assertLess(self.h.visible_rows(), rows_before)

    def test_input_area_capped_at_third_of_screen(self):
        self._start_comment()
        self.h.input_buffer = 'x\n' * 200
        self.assertEqual(self.h.input_rows(), self.h.screen_size.rows // 3)

    def test_kill_word_and_kill_all(self):
        self._start_comment()
        self.h.input_text('нужен рефактор этой функции')
        self.h.input_kill_word()
        self.assertEqual(self.h.input_buffer, 'нужен рефактор этой ')
        self.h.input_kill_word()
        self.assertEqual(self.h.input_buffer, 'нужен рефактор ')
        self.h.input_kill_all()
        self.assertEqual(self.h.input_buffer, '')

    def test_kill_word_on_empty_buffer_is_noop(self):
        self._start_comment()
        self.h.input_kill_word()
        self.assertEqual(self.h.input_buffer, '')

    def test_kill_word_stops_at_newline(self):
        self._start_comment()
        self.h.input_buffer = 'первая\nвторая строка'
        self.h.input_kill_word()
        self.assertEqual(self.h.input_buffer, 'первая\nвторая ')
        self.h.input_kill_word()
        self.assertEqual(self.h.input_buffer, 'первая\n')

    def test_ctrl_u_erases_text_while_typing(self):
        self._start_comment()
        self.h.input_text('текст')
        scrolls = []
        self.h.diff_scroll = lambda d: scrolls.append(d)
        self.h.on_key(kittymock.KeyEvent('u', ctrl=True))
        self.assertEqual((self.h.input_buffer, scrolls), ('', []))

    def test_ctrl_u_scrolls_diff_outside_input(self):
        scrolls = []
        self.h.diff_scroll = lambda d: scrolls.append(d)
        self.h.on_key(kittymock.KeyEvent('u', ctrl=True))
        self.assertEqual(len(scrolls), 1)

    def test_ctrl_w_erases_word_and_does_not_leak_to_hotkeys(self):
        self._start_comment()
        self.h.input_text('раз два')
        exported = []
        self.h.export_review = lambda: exported.append(1)
        self.h.on_key(kittymock.KeyEvent('w', ctrl=True))
        self.assertEqual((self.h.input_buffer, exported), ('раз ', []))

    def test_russian_ctrl_w_as_c0_erases_word(self):
        # на кириллице ctrl+ц приходит текстом '\x17' (send_text
        # из терминального конфига), а не key-событием
        self._start_comment()
        self.h.input_text('раз два')
        self.h.on_text('\x17')
        self.assertEqual(self.h.input_buffer, 'раз ')

    def test_russian_ctrl_u_as_c0_scrolls_outside_input(self):
        scrolls = []
        self.h.diff_scroll = lambda d: scrolls.append(d)
        self.h.on_text('\x15')
        self.assertEqual(len(scrolls), 1)

    def test_multiline_comment_exported_with_indent(self):
        self._start_comment()
        self.h.input_buffer = 'первая\nвторая'
        self.h.commit_input()
        copied = []
        self.h._copy_clipboard = copied.append
        self.h.export_review()
        self.assertIn('\n  первая\n  вторая', copied[0])

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
        cmd, gui = R.editor_command(self.proj, '/f.py', 10)
        self.assertFalse(gui)
        self.assertEqual(cmd, ['vim', '+10', '/f.py'])

    def test_gui_editor_code(self):
        os.environ.pop('VISUAL', None)
        os.environ['EDITOR'] = 'code'
        cmd, gui = R.editor_command(self.proj, '/f.py', 7)
        self.assertTrue(gui)
        self.assertEqual(cmd, ['code', '-g', '/f.py:7'])

    def test_gui_editor_subl_positional(self):
        os.environ.pop('VISUAL', None)
        os.environ['EDITOR'] = 'subl'
        cmd, gui = R.editor_command(self.proj, '/f.py', 3)
        self.assertTrue(gui)
        self.assertEqual(cmd, ['subl', '/f.py:3'])

    def test_visual_precedence(self):
        os.environ['VISUAL'] = 'code'
        os.environ['EDITOR'] = 'vim'
        cmd, gui = R.editor_command(self.proj, '/f.py', 1)
        self.assertTrue(gui)
        self.assertEqual(cmd[0], 'code')


class YankTest(unittest.TestCase):
    """Сборка payload для копирования кода из диффа —
    чистая логика без git.
    """

    def setUp(self):
        self.h = R.ReviewHandler([], '/repo', '/repo')
        wire(self.h, rows=40, cols=120)
        # минимально имитируем выбранный файл a/b.py и загруженный дифф
        self.h.filtered = [{'path': 'a/b.py', 'rel': 'a/b.py', 'kind': 'modified',
                            'xy': ' M', 'untracked': False}]
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
        # диапазон [1..3] включает гэп (индекс 2), номера
        # строк 2 и 3 → код l2..l3
        code, a, b = self.h._yank_code(1, 3)
        self.assertEqual((a, b), (2, 3))
        self.assertEqual(code, 'l2\nl3')

    def test_no_real_lines_returns_none(self):
        self.assertIsNone(self.h._yank_code(2, 2))   # только гэп

    def test_hscroll_capped_at_right_edge(self):
        self.h.diff_before = 'a\n'
        self.h.diff_after = 'a' + 'Z' * 200 + '\n'   # одна очень длинная строка
        self.h.diff_src = DiffSource(self.h.diff_before, self.h.diff_after)
        self.h.build_diff_rows()
        cap = self.h.hscroll_max
        self.assertGreater(cap, 0)
        self.h.hscroll_by(10_000)
        self.assertEqual(self.h.hscroll, cap)        # вправо не уезжает за предел
        self.h.hscroll_by(-10_000)
        self.assertEqual(self.h.hscroll, 0)          # влево — до нуля

    def test_is_code_row_skips_gap_and_padding(self):
        lineno, gap = self.h.diff_lineno, self.h.diff_gap
        self.assertTrue(is_code_row(0, lineno, gap))      # обычная строка
        self.assertTrue(is_code_row(4, lineno, gap))
        self.assertFalse(is_code_row(2, lineno, gap))     # padding/разделитель гэпа

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

    def test_copy_location_writes_mention_with_line(self):
        self.h.diff_cur = 3                     # lineno 3
        self.h.copy_location()
        self.assertEqual(self._copied(), '@a/b.py#L3')

    def test_copy_location_writes_line_range_when_selected(self):
        self.h.diff_sel = (0, 3)
        self.h.copy_location()
        self.assertEqual(self._copied(), '@a/b.py#L1-3')

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

    def test_copy_path_writes_mention(self):
        self.h.copy_path()
        self.assertEqual(self._copied(), '@a/b.py')

    def test_smart_copy_tree_copies_path(self):
        self.h.focus = 'tree'
        self.h.smart_copy()
        self.assertEqual(self._copied(), '@a/b.py')

    def test_smart_copy_diff_copies_code(self):
        self.h.focus = 'diff'
        self.h.diff_cur = 0
        self.h.smart_copy()
        self.assertEqual(self._copied(), 'l1')

    def test_smart_copy_location_diff(self):
        self.h.focus = 'diff'
        self.h.diff_cur = 0
        self.h.smart_copy_location()
        self.assertEqual(self._copied(), '@a/b.py#L1')

    def test_smart_copy_location_tree_copies_path(self):
        self.h.focus = 'tree'
        self.h.smart_copy_location()
        self.assertEqual(self._copied(), '@a/b.py')


if __name__ == '__main__':
    unittest.main()
