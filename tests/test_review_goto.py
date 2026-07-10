import os
import shutil
import subprocess
import tempfile
import unittest

import kittymock  # noqa: F401
import review as R
from kittymock import KeyEvent, MouseButton, MouseEvent, draw_text, wire
from modules.vcs.diff import gutter_width


_ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


class GotoDefinitionTest(unittest.TestCase):
    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        self.repo = tempfile.mkdtemp(prefix='ccrev_goto_')
        self._git('init', '-b', 'main')
        self.write('changed.py', 'x = 1\ndef unique_def():\n    return 1\n')
        self.write('ext.py', 'def only_here():\n    return 2\n')
        self.write('dupa.py', 'def dup_def():\n    return 3\n')
        self.write('dupb.py', 'def dup_def():\n    return 4\n')
        # far.py: def далеко от будущей правки и не как scope ханка —
        # в unified строка определения окажется скрытой (свёрнута)
        far = ([f'a{i} = {i}' for i in range(18)]           # 1..18
               + ['def far_target():', '    return 0']       # 19..20 (module-level)
               + [f'b{i} = {i}' for i in range(20)])         # 21..40
        self.write('far.py', '\n'.join(far) + '\n')
        self._git('add', '-A')
        self._git('commit', '-m', 'init')
        # рабочие правки: changed.py и dupa.py меняются (в ревью),
        # ext.py и dupb.py неизменны (внешние для ревью)
        self.write('changed.py', 'x = 2\ndef unique_def():\n    return 1\n')
        self.write('dupa.py', 'def dup_def():\n    return 30\n')
        self.write('far.py', '\n'.join(far[:-1] + ['b19 = 999']) + '\n')

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

    def _select(self, basename):
        for i, r in enumerate(self.h.rows):
            if r['type'] == 'file' and r['name'] == basename:
                self.h.tsel = i
                self.h.load_diff()
                return
        self.fail(f'{basename} не в дереве')

    # --- резолв и переход внутри ревью ---

    def test_navigate_to_changed_file(self):
        self._select('dupa.py')
        self.h.goto_definition('unique_def')
        self.assertIsNone(self.h._external)
        self.assertEqual(self.h.current_item()['rel'], 'changed.py')
        # курсор встал на строку объявления (line 2 нового файла)
        self.assertEqual(self.h.diff_lineno[self.h.diff_cur], 2)
        self.assertEqual(len(self.h._navstack), 1)

    def test_hidden_line_switches_to_final(self):
        # определение на неизменённой строке, скрытой в unified —
        # переходим в final, чтобы строка стала видимой
        self._select('changed.py')
        self.h.goto_definition('far_target')
        self.assertIsNone(self.h._external)
        self.assertEqual(self.h.current_item()['rel'], 'far.py')
        self.assertEqual(self.h.view_mode, 'final')
        self.assertEqual(self.h.diff_lineno[self.h.diff_cur], 19)

    def test_no_definition_flashes(self):
        self._select('changed.py')
        self.h.goto_definition('nonexistent_zzz')
        self.assertIn('no definition', draw_text(self.h))
        self.assertEqual(self.h._navstack, [])

    # --- внешний (неизменённый) файл: in-viewer read-only ---

    def test_external_file_shown_readonly(self):
        self._select('changed.py')
        self.h.goto_definition('only_here')
        self.assertEqual(self.h._external, 'ext.py')
        self.assertEqual(self.h.view_mode, 'final')
        self.assertEqual(self.h.diff_lineno[self.h.diff_cur], 1)

    def test_nav_back_restores(self):
        self._select('changed.py')
        before = self.h.current_item()['rel']
        self.h.goto_definition('only_here')
        self.assertEqual(self.h._external, 'ext.py')
        self.h.nav_back()
        self.assertIsNone(self.h._external)
        self.assertEqual(self.h.current_item()['rel'], before)
        self.assertEqual(self.h._navstack, [])

    def test_nav_back_empty_flashes(self):
        self.h.nav_back()
        self.assertIn('nothing to go back', draw_text(self.h))

    def test_readonly_blocks_comment(self):
        self._select('changed.py')
        self.h.goto_definition('only_here')
        self.h.start_comment()
        self.assertIn('read-only', draw_text(self.h))
        self.assertIsNone(self.h.comment_target)

    # --- несколько кандидатов: пикер ---

    def test_multiple_candidates_open_picker(self):
        self._select('changed.py')
        self.h.goto_definition('dup_def')
        self.assertIsNotNone(self.h._cand)
        self.assertGreaterEqual(len(self.h._cand), 2)

    def test_pick_navigates_and_closes(self):
        self._select('changed.py')
        self.h.goto_definition('dup_def')
        first = self.h._cand[0]
        self.h._pick(0)
        self.assertIsNone(self.h._cand)
        shown = self.h._external or self.h.current_item()['rel']
        self.assertEqual(shown, first.path)

    # --- мышь ---

    def test_alt_click_dispatches_goto(self):
        got = []
        self.h.goto_definition = lambda *a: got.append(a)
        self.h._word_at = lambda ev: ('sym', False, False, None)
        ev = MouseEvent(cell_x=50, cell_y=5, buttons=MouseButton.LEFT)
        ev.mods = R._ALT_MOD
        self.h.on_mouse_event(ev)
        self.assertEqual(got, [('sym', False, False, None)])

    def test_plain_click_does_not_goto(self):
        got = []
        self.h.goto_definition = lambda *a: got.append(a)
        self.h._word_at = lambda ev: ('sym', False, False, None)
        ev = MouseEvent(cell_x=50, cell_y=5, buttons=MouseButton.LEFT)
        ev.mods = 0
        self.h.on_mouse_event(ev)
        self.assertEqual(got, [])

    # --- жесты мыши: слово / комментарий ---

    def test_double_click_selects_word(self):
        self._select('changed.py')
        self.h.diff_offset = 0
        di = next(i for i, p in enumerate(self.h.diff_plain) if 'unique_def' in p)
        idx = self.h.diff_plain[di].index('unique_def')
        ev = MouseEvent(cell_x=self.h.left_width() + 3 + idx, cell_y=di + 2)
        self.h.on_click(ev)          # первый клик
        self.h.on_click(ev)          # второй → double → выделить слово
        self.assertEqual(self.h.diff_char_sel,
                         (di, idx, idx + len('unique_def')))

    def test_line_number_click_starts_comment(self):
        self._select('changed.py')
        self.h.diff_offset = 0
        di = next(i for i in range(len(self.h.diff_lineno))
                  if self.h._commentable(i))
        # колонка 0 попадает в гуттер номеров строк
        self.h.on_click(MouseEvent(cell_x=self.h.left_width() + 3, cell_y=di + 2))
        self.assertEqual(self.h.input_mode, 'comment')

    def test_diff_col_at_hscroll_keeps_gutter_fixed(self):
        # hscroll сдвигает только код: клик по номеру строки остаётся
        # в гуттере, клик по коду учитывает скролл
        self._select('changed.py')
        self.h.hscroll = 5
        lw = self.h.left_width()
        gut = MouseEvent(cell_x=lw + 3, cell_y=2)
        self.assertEqual(self.h._diff_col_at(gut), 0)
        code_x = self.h._gutter_cols() + 2 + 1
        code = MouseEvent(cell_x=lw + 3 + code_x, cell_y=2)
        self.assertEqual(self.h._diff_col_at(code), code_x + 5)

    def test_gutter_cols_final_view(self):
        # final-вид считает гуттер по одной колонке (как final_rows),
        # а не по diff_src.one_col — иначе split кода съезжает
        self._select('changed.py')
        self.h.goto_definition('only_here')          # внешний файл → final
        self.assertEqual(self.h.view_mode, 'final')
        self.assertEqual(self.h._gutter_cols(),
                         gutter_width(True, self.h.diff_width()))

    # --- форма курсора ---

    def test_line_number_hover_pointer(self):
        self._select('changed.py')
        self.h.diff_offset = 0
        di = next(i for i in range(len(self.h.diff_lineno))
                  if self.h._commentable(i))
        lw = self.h.left_width()
        gut = MouseEvent(cell_x=lw + 3, cell_y=di + 2)           # col 0 → номер
        self.assertEqual(self.h._wanted_pointer(gut), 'pointer')
        code = MouseEvent(cell_x=lw + 3 + self.h._gutter_cols() + 3, cell_y=di + 2)
        self.assertEqual(self.h._wanted_pointer(code), 'text')

    def test_alt_hover_pointer(self):
        self._select('changed.py')
        self.h.diff_offset = 0
        di = next(i for i, p in enumerate(self.h.diff_plain) if 'unique_def' in p)
        idx = self.h.diff_plain[di].index('unique_def')
        ev = MouseEvent(cell_x=self.h.left_width() + 3 + idx, cell_y=di + 2)
        ev.mods = R._ALT_MOD
        self.assertEqual(self.h._wanted_pointer(ev), 'pointer')

    # --- русская раскладка ---

    def test_russian_d_triggers_goto(self):
        # физическая клавиша d на ЙЦУКЕН даёт «в»
        self._select('changed.py')
        di = next(i for i, p in enumerate(self.h.diff_plain) if 'unique_def' in p)
        idx = self.h.diff_plain[di].index('unique_def')
        self.h.diff_char_sel = (di, idx, idx + len('unique_def'))
        got = []
        self.h.goto_definition = lambda *a: got.append(a)
        self.h.on_text('в')
        self.assertTrue(got and got[0][0] == 'unique_def')

    def test_russian_ctrl_o_navigates_back(self):
        # физическая клавиша o на ЙЦУКЕН даёт «щ»
        called = []
        self.h.nav_back = lambda: called.append(True)
        ev = KeyEvent(key='щ')
        ev.ctrl = True
        self.h.on_key(ev)
        self.assertTrue(called)

    def test_word_at_maps_cell_to_symbol(self):
        self._select('changed.py')
        self.h.diff_offset = 0
        di = next(i for i, p in enumerate(self.h.diff_plain) if 'unique_def' in p)
        idx = self.h.diff_plain[di].index('unique_def')
        ev = MouseEvent(cell_x=self.h.left_width() + 3 + idx, cell_y=di + 2)
        self.assertEqual(self.h._word_at(ev)[0], 'unique_def')


if __name__ == '__main__':
    unittest.main()
