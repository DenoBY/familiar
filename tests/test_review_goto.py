import os
import shutil
import subprocess
import tempfile
import unittest

from lspmock import FakePool, FakeSession
from lspmock import loc as _loc
from lspmock import sym as _sym

import kittymock  # noqa: F401
import review as R
from kittymock import (
    KeyEvent,
    MouseButton,
    MouseEvent,
    draw_text,
    run_threads_inline,
    wire,
)
from modules.lsp import registry
from modules.lsp.rpc import RpcError
from modules.lsp.session import NoServer, Progress
from modules.vcs.diff import gutter_width
from modules.vcs.goto import ALT_MOD


_ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


class GotoDefinitionTest(unittest.TestCase):
    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        self._backup['XDG_CONFIG_HOME'] = os.environ.get('XDG_CONFIG_HOME')
        os.environ.update(_ENV)
        run_threads_inline(self)
        self.repo = tempfile.mkdtemp(prefix='ccrev_goto_')
        # герметичность: настоящий сервер не должен подняться, даже
        # если pyright стоит в системе
        os.environ['XDG_CONFIG_HOME'] = os.path.join(self.repo, '.xdgconf')
        conf = registry.user_path()
        os.makedirs(os.path.dirname(conf), exist_ok=True)
        with open(conf, 'w') as f:
            f.write('server python\n  disabled yes\n')
        registry.reset_cache()

        self._git('init', '-b', 'main')
        self.write('changed.py', 'x = 1\ndef unique_def():\n    return 1\n'
                                 'result = unique_def()\n')
        self.write('ext.py', 'def only_here():\n    return 2\n')
        self.write('dupa.py', 'def dup_def():\n    return 3\n')
        self.write('dupb.py', 'def dup_def():\n    return 4\n')
        # far.py: def далеко от будущей правки и не как scope ханка —
        # в unified строка определения окажется скрытой (свёрнута)
        far = ([f'a{i} = {i}' for i in range(18)]           # 1..18
               + ['def far_target():', '    return 0']       # 19..20
               + [f'b{i} = {i}' for i in range(20)])         # 21..40
        self.write('far.py', '\n'.join(far) + '\n')
        self._git('add', '-A')
        self._git('commit', '-m', 'init')
        # рабочие правки: changed.py и dupa.py меняются (в ревью),
        # ext.py и dupb.py неизменны (внешние для ревью)
        self.write('changed.py', 'x = 2\ndef unique_def():\n    return 1\n'
                                 'result = unique_def(2)\n')
        self.write('dupa.py', 'def dup_def():\n    return 30\n')
        self.write('far.py', '\n'.join(far[:-1] + ['b19 = 999']) + '\n')

        self.h = R.ReviewHandler([], self.repo, self.repo)
        wire(self.h, rows=40, cols=120)
        self.h.load_source()

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        registry.reset_cache()
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

    def path(self, rel):
        return os.path.join(self.repo, rel)

    def _select(self, basename):
        for i, r in enumerate(self.h.rows):
            if r['type'] == 'file' and r['name'] == basename:
                self.h.tsel = i
                self.h.load_diff()
                return
        self.fail(f'{basename} не в дереве')

    def lsp(self, **kw):
        session = FakeSession(**kw)
        self.h._lsp = FakePool(session)
        return session

    def row_with(self, text, sign=None):
        gut = self.h._gutter_cols()
        for i, plain in enumerate(self.h.diff_plain):
            if text in plain[gut + 2:] and (sign is None
                                            or plain[gut:gut + 2].startswith(sign)):
                return i
        self.fail(f'строки с {text!r} нет')

    def ref_for(self, symbol, sign=None):
        di = self.row_with(symbol, sign)
        return self.h._doc_ref(di, self.h.diff_plain[di].index(symbol))

    def goto(self, symbol, sign=None):
        self.h.goto_definition(self.ref_for(symbol, sign))

    # --- позиция под курсором ---

    def test_doc_ref_maps_cell_to_symbol(self):
        self._select('changed.py')
        ref = self.ref_for('unique_def')
        self.assertEqual(ref.word, 'unique_def')
        self.assertEqual(ref.side, 'after')
        self.assertEqual(ref.rel, 'changed.py')

    def test_doc_ref_on_removed_line_is_before_side(self):
        self._select('changed.py')
        ref = self.ref_for('unique_def', sign='-')
        self.assertEqual(ref.side, 'before')

    def test_doc_ref_in_gutter_is_none(self):
        self._select('changed.py')
        di = self.row_with('unique_def')
        self.assertIsNone(self.h._doc_ref(di, 0))

    def test_doc_ref_on_gap_is_none(self):
        self._select('far.py')
        di = next((i for i in range(len(self.h.diff_plain))
                   if self.h._gap_at(i) is not None), None)
        self.assertIsNotNone(di, 'в far.py должен быть свёрнутый промежуток')
        self.assertIsNone(self.h._doc_ref(di, self.h._gutter_cols() + 5))

    def test_doc_ref_column_accounts_for_tabs(self):
        # в diff_plain табы развёрнуты в пробелы, в файле — нет:
        # колонка клика обязана вернуться в индекс исходной строки
        self.write('changed.py', 'x = 2\ndef unique_def():\n'
                                 '\tvalue = helper()\n    return 1\n')
        self.h.refresh()
        self._select('changed.py')
        ref = self.ref_for('helper')
        self.assertEqual(ref.raw[ref.col:ref.col + 6], 'helper')
        self.assertTrue(ref.raw.startswith('\t'))

    # --- резолв и переход внутри ревью ---

    def test_navigate_to_changed_file(self):
        self._select('dupa.py')
        self.lsp(defs={('dupa.py', 1): [_loc(self.path('changed.py'), 1)]})
        self.goto('dup_def')
        self.assertIsNone(self.h._external)
        self.assertEqual(self.h.current_item()['path'], 'changed.py')
        self.assertEqual(self.h.diff_lineno[self.h.diff_cur], 2)
        self.assertEqual(len(self.h._navstack), 1)

    def test_hidden_line_switches_to_final(self):
        # определение на неизменённой строке, скрытой в unified —
        # переходим в final, чтобы строка стала видимой
        self._select('changed.py')
        self.lsp(defs={('changed.py', 4): [_loc(self.path('far.py'), 18)]})
        self.goto('unique_def', sign='+')
        self.assertEqual(self.h.current_item()['path'], 'far.py')
        self.assertEqual(self.h.view_mode, 'final')
        self.assertEqual(self.h.diff_lineno[self.h.diff_cur], 19)

    def test_external_file_shown_readonly(self):
        self._select('changed.py')
        self.lsp(defs={('changed.py', 4): [_loc(self.path('ext.py'), 0)]})
        self.goto('unique_def', sign='+')
        self.assertEqual(self.h._external, 'ext.py')
        self.assertEqual(self.h.view_mode, 'final')
        self.assertEqual(self.h.diff_lineno[self.h.diff_cur], 1)

    def test_target_outside_repo_opens_by_absolute_path(self):
        # определение в vendor или stdlib: source про такой файл не
        # знает, читаем с диска
        outside = os.path.join(tempfile.mkdtemp(prefix='ccrev_vendor_'), 'lib.py')
        with open(outside, 'w') as f:
            f.write('def vendored():\n    return 1\n')
        self.addCleanup(shutil.rmtree, os.path.dirname(outside), True)
        self._select('changed.py')
        self.lsp(defs={('changed.py', 4): [_loc(outside, 0)]})
        self.goto('unique_def', sign='+')
        self.assertEqual(self.h._external, outside)
        self.assertIn('vendored', draw_text(self.h))

    def test_click_on_the_declaration_says_so(self):
        # сервер отвечает той же строкой — прыжок «в себя» выглядел бы
        # как «ничего не произошло», да ещё и стек ⌃o засорял
        self._select('changed.py')
        di = self.row_with('unique_def', sign='+')
        self.h.diff_cur = di
        line = self.h.diff_lineno[di]
        self.lsp(defs={('changed.py', line): [_loc(self.path('changed.py'),
                                                   line - 1)]})
        self.goto('unique_def', sign='+')
        self.assertIn('already at the definition', draw_text(self.h))
        self.assertEqual(self.h._navstack, [])

    def test_no_definition_flashes(self):
        self._select('changed.py')
        self.lsp()
        self.goto('unique_def', sign='+')
        self.assertIn('no definition', draw_text(self.h))
        self.assertEqual(self.h._navstack, [])

    def test_server_error_is_not_reported_as_no_definition(self):
        # сбой сервера иначе выглядел бы уверенным «определения нет»
        self._select('changed.py')
        self.lsp(error=RpcError('crashed'))
        self.goto('unique_def', sign='+')
        text = draw_text(self.h)
        self.assertNotIn('no definition', text)
        self.assertIn('language server', text)

    def test_missing_server_names_the_command(self):
        self._select('changed.py')
        self.h._lsp = FakePool(raises=NoServer('x not installed — run: '
                                               'familiar lsp install python'))
        self.goto('unique_def', sign='+')
        self.assertIn('familiar lsp install python', draw_text(self.h))

    def test_unexpected_failure_does_not_wedge_the_kitten(self):
        # если фоновая ошибка унесёт поток, _goto_busy останется
        # взведён и кит перестанет отвечать на ⌥-клик совсем
        self._select('changed.py')
        self.lsp(error=KeyError('boom'))
        self.goto('unique_def', sign='+')
        self.assertIn('KeyError', draw_text(self.h))
        self.assertFalse(self.h._goto_busy)

    def test_search_is_not_restarted_while_one_is_running(self):
        self._select('changed.py')
        self.lsp()
        self.h._goto_busy = True
        self.h.flash = ''
        self.goto('unique_def', sign='+')
        self.assertEqual(self.h.flash, '')

    # --- цепочка резолва ---

    def test_position_query_uses_working_tree_text(self):
        self._select('changed.py')
        session = self.lsp(defs={('changed.py', 4): [_loc(self.path('ext.py'), 0)]})
        self.goto('unique_def', sign='+')
        self.assertEqual(session.opened[0][0], 'changed.py')
        self.assertIn('unique_def(2)', session.opened[0][1])

    def test_removed_line_falls_back_to_symbol_search(self):
        self._select('changed.py')
        session = self.lsp(syms={'unique_def': [_sym('unique_def',
                                                     self.path('ext.py'), 0)]})
        self.goto('unique_def', sign='-')
        self.assertEqual(session.asked, [], 'по старой строке позицию не спрашивают')
        self.assertEqual(self.h._external, 'ext.py')

    def test_empty_symbol_search_tries_twin_line(self):
        # символьного поиска не хватило — тот же идентификатор ищем в
        # новой версии файла и спрашиваем позиционно
        self._select('changed.py')
        session = self.lsp(defs={('changed.py', 2): [_loc(self.path('ext.py'), 0)]})
        self.goto('unique_def', sign='-')
        self.assertEqual(self.h._external, 'ext.py')
        self.assertTrue(session.asked)

    def test_definition_falls_back_to_symbols_when_empty(self):
        self._select('changed.py')
        self.lsp(syms={'unique_def': [_sym('unique_def', self.path('ext.py'), 0)]})
        self.goto('unique_def', sign='+')
        self.assertEqual(self.h._external, 'ext.py')

    # --- несколько кандидатов: пикер ---

    def test_multiple_candidates_open_picker(self):
        self._select('changed.py')
        self.lsp(syms={'unique_def': [_sym('unique_def', self.path('dupa.py'), 0),
                                      _sym('unique_def', self.path('dupb.py'), 0)]})
        self.goto('unique_def', sign='+')
        self.assertIsNotNone(self.h._cand)
        self.assertGreaterEqual(len(self.h._cand), 2)

    def test_pick_navigates_and_closes(self):
        self._select('changed.py')
        self.lsp(syms={'unique_def': [_sym('unique_def', self.path('dupa.py'), 0),
                                      _sym('unique_def', self.path('dupb.py'), 0)]})
        self.goto('unique_def', sign='+')
        first = self.h._cand[0]
        self.h._pick(0)
        self.assertIsNone(self.h._cand)
        shown = self.h._external or self.h.current_item()['path']
        self.assertEqual(shown, first.path)

    def test_pick_out_of_range_keeps_the_list(self):
        # цифра мимо (нажали 5 при двух кандидатах) — не повод
        # заставлять искать заново
        self._select('changed.py')
        self.lsp(syms={'unique_def': [_sym('unique_def', self.path('dupa.py'), 0),
                                      _sym('unique_def', self.path('dupb.py'), 0)]})
        self.goto('unique_def', sign='+')
        self.h._pick(5)
        self.assertIsNotNone(self.h._cand)

    def test_picker_shows_preview_from_file(self):
        self._select('changed.py')
        self.lsp(syms={'unique_def': [_sym('unique_def', self.path('dupa.py'), 0),
                                      _sym('unique_def', self.path('dupb.py'), 0)]})
        self.goto('unique_def', sign='+')
        self.assertIn('def dup_def', draw_text(self.h))

    # --- возврат ---

    def test_nav_back_restores(self):
        self._select('changed.py')
        before = self.h.current_item()['path']
        self.lsp(defs={('changed.py', 4): [_loc(self.path('ext.py'), 0)]})
        self.goto('unique_def', sign='+')
        self.assertEqual(self.h._external, 'ext.py')
        self.h.nav_back()
        self.assertIsNone(self.h._external)
        self.assertEqual(self.h.current_item()['path'], before)
        self.assertEqual(self.h._navstack, [])

    def test_nav_back_empty_flashes(self):
        self.h.nav_back()
        self.assertIn('nothing to go back', draw_text(self.h))

    def test_readonly_blocks_comment(self):
        self._select('changed.py')
        self.lsp(defs={('changed.py', 4): [_loc(self.path('ext.py'), 0)]})
        self.goto('unique_def', sign='+')
        self.h.start_comment()
        self.assertIn('read-only', draw_text(self.h))
        self.assertIsNone(self.h.comment_target)

    # --- индикатор индексации ---

    def test_badge_shows_percentage(self):
        self._select('changed.py')
        self.lsp(status=Progress('indexing', 62, '', 3.0, 0))
        self.h.draw_screen()
        self.assertIn('62%', draw_text(self.h))

    def test_badge_falls_back_to_time_and_cache(self):
        self._select('changed.py')
        self.lsp(status=Progress('indexing', -1, '', 72.0, 34 * 1024 * 1024))
        self.h.draw_screen()
        shown = draw_text(self.h)
        self.assertIn('1:12', shown)
        self.assertIn('34 MB', shown)

    def test_badge_silent_for_a_quick_start(self):
        # повторный старт по тёплому кэшу — доли секунды: мигать
        # индикатором на такой работе незачем
        self._select('changed.py')
        self.lsp(status=Progress('indexing', 5, '', 0.3, 0))
        self.h.draw_screen()
        self.assertNotIn('⟳', draw_text(self.h))

    def test_badge_silent_when_ready(self):
        self._select('changed.py')
        self.lsp(status=Progress('ready', -1, '', 5.0, 0))
        self.h.draw_screen()
        self.assertNotIn('⟳', draw_text(self.h))

    def test_badge_comes_before_the_hints(self):
        # в хвосте серых подсказок индикатор терялся
        self._select('changed.py')
        self.lsp(status=Progress('indexing', 62, '', 3.0, 0))
        self.h.focus = 'diff'
        self.h.draw_screen()
        shown = draw_text(self.h)
        self.assertLess(shown.index('62%'), shown.index('⌥/d def'))

    def test_badge_does_not_overflow_the_line(self):
        self._select('changed.py')
        self.lsp(status=Progress('indexing', 62, '', 3.0, 0))
        self.h.draw_screen()
        self.assertLessEqual(len(self.h.out[-1]), self.h.screen_size.cols + 20)

    def test_file_without_extension_passes_its_shebang(self):
        # bin/familiar и git-хуки опознаются только по первой строке
        self.write('tool', '#!/usr/bin/env python3\nvalue = helper()\n')
        self._git('add', '-N', 'tool')
        self.h.refresh()
        self._select('tool')
        session = self.lsp()
        self.goto('helper')
        self.assertEqual(self.h._lsp.asked_for[1], '#!/usr/bin/env python3')
        self.assertEqual(session.opened[0][0], 'tool')

    def test_load_diff_warms_the_server(self):
        # сервер поднимается, пока читают дифф, а не по первому клику
        del self.h._lsp_warm            # снять заглушку из kittymock
        warmed = []
        self.h._lsp_warm_now = lambda: warmed.append(True)
        self._select('changed.py')
        self.assertTrue(warmed, 'прогрев не запустился')

    def test_warm_failure_is_silent(self):
        # язык без сервера — обычное дело, шуметь об этом не нужно
        del self.h._lsp_warm
        self.h._lsp = FakePool(raises=NoServer('nope'))
        self._select('changed.py')
        self.assertNotIn('nope', draw_text(self.h))

    def test_finalize_stops_servers(self):
        self.lsp()
        self.h.finalize()
        self.assertTrue(self.h._lsp.stopped)

    # --- мышь ---

    def test_alt_click_dispatches_goto(self):
        self._select('changed.py')
        got = []
        self.h.goto_definition = lambda ref: got.append(ref)
        di = self.row_with('unique_def')
        idx = self.h.diff_plain[di].index('unique_def')
        ev = MouseEvent(cell_x=self.h.left_width() + 3 + idx, cell_y=di + 2,
                        buttons=MouseButton.LEFT)
        ev.mods = ALT_MOD
        self.h.on_mouse_event(ev)
        self.assertTrue(got and got[0].word == 'unique_def')

    def test_plain_click_does_not_goto(self):
        self._select('changed.py')
        got = []
        self.h.goto_definition = lambda ref: got.append(ref)
        ev = MouseEvent(cell_x=50, cell_y=5, buttons=MouseButton.LEFT)
        ev.mods = 0
        self.h.on_mouse_event(ev)
        self.assertEqual(got, [])

    # --- жесты мыши: слово / комментарий ---

    def test_double_click_selects_word(self):
        self._select('changed.py')
        self.h.diff_offset = 0
        di = self.row_with('unique_def')
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
        self.lsp(defs={('changed.py', 4): [_loc(self.path('ext.py'), 0)]})
        self.goto('unique_def', sign='+')            # внешний файл → final
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
        di = self.row_with('unique_def')
        idx = self.h.diff_plain[di].index('unique_def')
        ev = MouseEvent(cell_x=self.h.left_width() + 3 + idx, cell_y=di + 2)
        ev.mods = ALT_MOD
        self.assertEqual(self.h._wanted_pointer(ev), 'pointer')

    # --- русская раскладка ---

    def test_russian_d_triggers_goto(self):
        # физическая клавиша d на ЙЦУКЕН даёт «в»
        self._select('changed.py')
        di = self.row_with('unique_def')
        idx = self.h.diff_plain[di].index('unique_def')
        self.h.diff_char_sel = (di, idx, idx + len('unique_def'))
        got = []
        self.h.goto_definition = lambda ref: got.append(ref)
        self.h.on_text('в')
        self.assertTrue(got and got[0].word == 'unique_def')

    def test_russian_ctrl_o_navigates_back(self):
        # физическая клавиша o на ЙЦУКЕН даёт «щ»
        called = []
        self.h.nav_back = lambda: called.append(True)
        ev = KeyEvent(key='щ')
        ev.ctrl = True
        self.h.on_key(ev)
        self.assertTrue(called)

    def test_russian_ctrl_o_as_c0_text_navigates_back(self):
        # терминальный конфиг мапит ctrl+щ в send_text C0-байта,
        # поэтому на кириллице ctrl+o приходит текстом '\x0f'
        called = []
        self.h.nav_back = lambda: called.append(True)
        self.h.on_text('\x0f')
        self.assertTrue(called)

    def test_c0_in_paste_is_not_a_hotkey(self):
        called = []
        self.h.nav_back = lambda: called.append(True)
        self.h.on_text('\x0f', in_bracketed_paste=True)
        self.assertEqual(called, [])


if __name__ == '__main__':
    unittest.main()
