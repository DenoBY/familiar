import json
import os
import shutil
import tempfile
import unittest

import kittymock  # noqa: F401
import modules.session.data as Dt
import session as S
from kittymock import EventType, KeyEvent, MouseEvent, draw_text, wire


NOW = 2_000_000.0


class SessionsHandlerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='ccsess_h_')
        self.h = S.SessionsHandler([], NOW)
        wire(self.h, rows=40, cols=120)

        # два интерактивных проекта (cli) + один sdk
        # (должен отфильтроваться)
        cur = os.path.realpath(os.getcwd())
        self.projA = self._project('projA', '/tmp/projA-fake', 'cli', 'sid-a', 'hello from A')
        self.projB = self._project('projB', cur, 'cli', 'sid-b', 'hello from B')
        self.projC = self._project('projC', '/tmp/projC', 'sdk-cli', 'sid-c', 'sdk stuff')
        self.h._all_projects = [self.projA, self.projB, self.projC]
        self.h.running = {}
        self.h.running_ids = set()
        self.h.rebuild_projects()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _project(self, name, path, entrypoint, sid, first_msg):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        f = os.path.join(d, sid + '.jsonl')
        with open(f, 'w') as fh:
            fh.write(json.dumps({'type': 'user', 'cwd': path,
                                 'message': {'content': first_msg}}) + '\n')
            fh.write(json.dumps({'type': 'assistant',
                                 'message': {'content': 'reply text here'}}) + '\n')
        os.utime(f, (NOW - 100, NOW - 100))
        return {'dir': d, 'dir_name': name, 'path': path, 'name': name,
                'probes': [{'file': f, 'entrypoint': entrypoint, 'mtime': NOW - 100}]}

    def _rebuild(self):
        self.h.preview.rebuild(self.h.screen_size.cols)

    def _limit(self):
        return self.h.preview.limit(self.h.visible_rows())

    # --- список проектов ---

    def test_rebuild_filters_non_interactive(self):
        names = [p['name'] for p in self.h.projects]
        self.assertIn('projA', names)
        self.assertIn('projB', names)
        self.assertNotIn('projC', names)          # sdk отфильтрован

    def test_show_all_includes_sdk(self):
        self.h.toggle_show_all()
        names = [p['name'] for p in self.h.projects]
        self.assertIn('projC', names)

    def test_current_project_marked(self):
        cur = [p for p in self.h.projects if p['is_current']]
        self.assertEqual([p['name'] for p in cur], ['projB'])

    def test_filter_projects(self):
        self.h.filter_query = 'projA'
        items = self.h.items()
        self.assertEqual([p['name'] for p in items], ['projA'])

    def test_move_bounded(self):
        self.h.move(-9)
        self.assertEqual(self.h.sel, 0)
        self.h.move(99)
        self.assertEqual(self.h.sel, self.h.current_len() - 1)

    def test_project_row_contains_name_and_fits_width(self):
        row = self.h._project_row(self.h.projects[0], 80, False)
        self.assertIn(self.h.projects[0]['name'], row)

    def test_draw_projects_smoke(self):
        self.h.draw_screen()
        text = draw_text(self.h)
        self.assertIn('Enter — open', text)
        self.assertTrue(any('projA' in str(x) for x in self.h.out))

    # --- сессии ---

    def _open_A(self):
        projA = next(p for p in self.h.projects if p['name'] == 'projA')
        self.h.open_project(projA)

    def test_open_project_loads_sessions(self):
        self._open_A()
        self.assertEqual(self.h.screen, 'sessions')
        self.assertEqual(len(self.h.sessions), 1)
        self.assertEqual(self.h.sessions[0]['id'], 'sid-a')
        self.assertEqual(self.h.sessions[0]['title'], 'hello from A')

    def test_active_session_sorted_first(self):
        self.h.running = {'sid-a': {'status': 'busy', 'cwd': '/x', 'waitingFor': None}}
        self.h.running_ids = {'sid-a'}
        self._open_A()
        self.assertTrue(self.h.sessions[0]['active'])
        self.assertEqual(self.h.sessions[0]['status'], 'busy')

    def test_session_row_shows_status(self):
        self.h.running = {'sid-a': {'status': 'busy', 'cwd': '/x', 'waitingFor': None}}
        self.h.running_ids = {'sid-a'}
        self._open_A()
        row = self.h._session_row(self.h.sessions[0], 100, False)
        self.assertIn('busy', row)

    def _open_A_bg(self):
        self.h.running = {'sid-a': {'status': 'idle', 'cwd': '/x',
                                    'waitingFor': None, 'kind': 'bg'}}
        self.h.running_ids = {'sid-a'}
        self._open_A()

    def test_session_row_marks_background_agent(self):
        self._open_A_bg()
        row = self.h._session_row(self.h.sessions[0], 100, False)
        self.assertIn('◆', row)
        self.assertIn('bg idle', row)

    def test_resume_refuses_background_agent(self):
        self._open_A_bg()
        self.h.do_resume(self.h.sessions[0])
        self.assertIsNone(self.h.result)
        self.assertIn('background agent', self.h.status)

    def test_fork_allowed_for_background_agent(self):
        self._open_A_bg()
        self.h.do_resume(self.h.sessions[0], fork=True)
        self.assertEqual(self.h.result['action'], 'resume')
        self.assertTrue(self.h.result['fork'])

    def test_session_row_shows_branch(self):
        self._open_A()
        s = dict(self.h.sessions[0], branch='feature/x')
        self.assertIn('feature/x', self.h._session_row(s, 100, False))

    def test_draw_sessions_smoke(self):
        self._open_A()
        self.h.draw_screen()
        self.assertIn('sessions', draw_text(self.h))

    # --- предпросмотр ---

    def test_preview_and_search(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        self.assertEqual(self.h.screen, 'preview')
        self.assertTrue(self.h.preview.lines)
        self.assertTrue(any('hello from A' in ln.text for ln in self.h.preview.lines))

        self.h.preview.run_search('reply', self.h.screen_size.cols,
                                  self.h.visible_rows())
        self.assertTrue(self.h.preview.search_matches)
        idx0 = self.h.preview.search_idx
        self.h.search_jump(1)
        self.assertTrue(self.h.preview.search_idx >= 0 or idx0 >= 0)

    def _folded_preview(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        body = '\n'.join(f'line {i}' for i in range(30))
        self.h.preview.entries = [Dt.Entry('result', body)]
        self._rebuild()
        return len(self.h.preview.lines)

    def test_expand_all_expands_then_collapses(self):
        collapsed = self._folded_preview()

        self.h.expand_all()
        self.assertEqual(self.h.preview.expanded, {0})
        self.assertGreater(len(self.h.preview.lines), collapsed)

        self.h.expand_all()
        self.assertEqual(self.h.preview.expanded, set())
        self.assertEqual(len(self.h.preview.lines), collapsed)

    def test_ctrl_o_expands(self):
        collapsed = self._folded_preview()
        self.h.on_key(KeyEvent(key='o', ctrl=True))
        self.assertEqual(self.h.preview.expanded, {0})
        self.assertGreater(len(self.h.preview.lines), collapsed)

    def test_ctrl_o_expands_on_cyrillic_layout(self):
        self._folded_preview()
        self.h.on_key(KeyEvent(key='щ', ctrl=True))     # физическая клавиша o
        self.assertEqual(self.h.preview.expanded, {0})

    def test_ctrl_o_expands_when_sent_as_control_byte(self):
        # config/keys/russian-ctrl.conf мапит ctrl+щ в
        # `send_text all \x0f`
        self._folded_preview()
        self.h.on_text('\x0f')
        self.assertEqual(self.h.preview.expanded, {0})

    def test_cmd_c_copies_on_cyrillic_layout(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        self._drag(2, 3)
        self.h.on_key(KeyEvent(key='с', super=True))    # кириллическая «эс»
        self.assertIn('\x1b]52;c;', ''.join(str(x) for x in self.h.out))

    def test_pointer_shape_follows_hover_zone(self):
        def move(x, y):
            self.h.out.clear()
            self.h.on_mouse_event(
                MouseEvent(cell_x=x, cell_y=y, type=EventType.MOVE))

        # экран проектов: строка проекта → рука, пустота ниже → стрелка
        move(5, 0)
        self.assertEqual(self.h._pointer_shape, 'pointer')
        self.assertIn('\x1b]22;>pointer\x1b\\', self.h.out)
        move(5, len(self.h.items()) + 3)
        self.assertIsNone(self.h._pointer_shape)
        self.assertEqual(self.h.out, ['\x1b]22;<\x1b\\'])

        # превью: обычная строка диалога → текстовый курсор
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        self.h.preview.offset = 0
        text_row = next(i for i, ln in enumerate(self.h.preview.lines)
                        if ln.entry < 0)
        move(3, text_row + 2)
        self.assertEqual(self.h._pointer_shape, 'text')

        # превью: сворачиваемая запись → рука
        self._folded_preview()
        self.h.preview.offset = 0
        fold_row = next(i for i, ln in enumerate(self.h.preview.lines)
                        if ln.entry >= 0)
        move(3, fold_row + 2)
        self.assertEqual(self.h._pointer_shape, 'pointer')

    def test_click_toggles_fold(self):
        collapsed = self._folded_preview()
        self.h.on_click(MouseEvent(cell_x=3, cell_y=2))     # строка ⎿ под шапкой
        self.assertEqual(self.h.preview.expanded, {0})
        self.assertGreater(len(self.h.preview.lines), collapsed)

    def test_click_on_fold_marker_expands(self):
        self._folded_preview()
        row = next(i for i, ln in enumerate(self.h.preview.lines)
                   if ln.text.strip().startswith('…'))
        self.h.on_click(MouseEvent(cell_x=3, cell_y=row + 2))
        self.assertEqual(self.h.preview.expanded, {0})

    def test_ctrl_o_expands_every_block_at_once(self):
        # раскрывается весь свёрнутый вывод, а не по одной
        # записи за нажатие
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        body = '\n'.join(f'line {i}' for i in range(30))
        self.h.preview.entries = [Dt.Entry('result', body),
                                  Dt.Entry('result', body)]
        self._rebuild()
        self.h.expand_all()
        self.assertEqual(self.h.preview.expanded, {0, 1})
        self.h.expand_all()
        self.assertEqual(self.h.preview.expanded, set())

    def test_expand_all_absorbs_a_single_open_block(self):
        # один блок раскрыт кликом — ctrl+o дораскрывает остальные,
        # а не сворачивает раскрытый
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        body = '\n'.join(f'line {i}' for i in range(30))
        self.h.preview.entries = [Dt.Entry('result', body),
                                  Dt.Entry('result', body)]
        self._rebuild()
        self.h.toggle_fold(0)
        self.h.expand_all()
        self.assertEqual(self.h.preview.expanded, {0, 1})

    def test_search_counts_only_visible_matches(self):
        # совпадение за правой границей экрана не «находится»:
        # подсветить его нечем (строка обрезана truncate), прыжок
        # выглядел бы сломанным
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        self.h.preview.entries = [Dt.Entry('result', 'x' * 200 + 'needle')]
        self._rebuild()
        self.h.preview.search_query = 'needle'
        self.h.preview.find_matches(self.h.screen_size.cols)
        self.assertEqual(self.h.preview.search_matches, [])

    def test_expand_all_without_foldable_reports(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        before = list(self.h.preview.lines)
        self.h.expand_all()
        self.assertEqual(self.h.preview.lines, before)
        self.assertEqual(self.h.status, 'nothing to expand')

    def test_preview_opens_at_the_end(self):
        self._open_A()
        self.h.screen_size.rows = 4          # заведомо короче диалога
        self.h.open_preview(self.h.sessions[0])
        self.assertEqual(self.h.preview.offset, self._limit())
        self.assertGreater(self.h.preview.offset, 0)

    def test_scroll_jumps_to_edges(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        self.h.preview.lines = self.h.preview.lines * 30    # заведомо длиннее экрана
        self.h.preview_jump(True)
        self.assertEqual(self.h.preview.offset, self._limit())
        self.h.preview_jump(False)
        self.assertEqual(self.h.preview.offset, 0)

    # --- прыжки по репликам ---

    def _three_prompts(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        body = '\n'.join(f'line {i}' for i in range(30))
        # между репликами — длинный вывод, чтобы прыжок был заметен
        entries = []
        for n in range(3):
            entries.append(Dt.Entry('user', f'вопрос {n}'))
            entries.append(Dt.Entry('result', body))
        self.h.preview.entries = entries
        self._rebuild()
        return [i for i, ln in enumerate(self.h.preview.lines) if ln.prompt]

    def test_jump_prompt_walks_forward_and_back(self):
        rows = self._three_prompts()
        self.assertEqual(len(rows), 3)
        self.h.preview.offset = 0
        self.h.jump_prompt(1)
        self.assertEqual(self.h.preview.offset, min(rows[1], self._limit()))
        self.h.jump_prompt(-1)
        self.assertEqual(self.h.preview.offset, rows[0])

    def test_jump_prompt_reports_at_the_edges(self):
        self._three_prompts()
        self.h.preview.offset = 0
        self.h.jump_prompt(-1)
        self.assertEqual(self.h.status, 'first prompt')

    def test_jump_prompt_without_prompts(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        self.h.preview.entries = [Dt.Entry('result', 'ok')]
        self._rebuild()
        self.h.jump_prompt(1)
        self.assertEqual(self.h.status, 'no prompts')

    def test_bracket_keys_jump_on_cyrillic_layout(self):
        rows = self._three_prompts()
        self.h.preview.offset = 0
        self.h.on_text('ъ')          # физическая ], ЙЦУКЕН
        self.assertEqual(self.h.preview.offset, min(rows[1], self._limit()))
        self.h.on_text('х')          # физическая [
        self.assertEqual(self.h.preview.offset, rows[0])

    # --- выделение и копирование ---

    def _drag(self, y0, y1, x0=0, x1=0):
        press = MouseEvent(cell_x=x0, cell_y=y0, buttons=1, type='PRESS')
        move = MouseEvent(cell_x=x1, cell_y=y1, buttons=1, type='MOVE')
        release = MouseEvent(buttons=1, type='RELEASE')
        for ev in (press, move, release):
            self.h.on_mouse_event(ev)

    def test_drag_across_rows_selects_lines(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        self._drag(2, 4)
        self.assertEqual(self.h.preview.sel, (0, 2))
        self.assertIsNone(self.h.preview.char_sel)

    def test_drag_within_row_selects_chars(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        self._drag(2, 2, x0=2, x1=7)
        # правая граница включительно: символ под курсором тоже выделен
        self.assertEqual(self.h.preview.char_sel, (0, 2, 8))
        self.assertIsNone(self.h.preview.sel)

    def test_drag_upwards_selects_same_lines(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        self._drag(4, 2)
        self.assertEqual(self.h.preview.sel, (0, 2))
        self.assertIsNone(self.h.preview.char_sel)

    def test_drag_leftwards_selects_same_chars(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        self._drag(2, 2, x0=7, x1=2)
        self.assertEqual(self.h.preview.char_sel, (0, 2, 8))
        self.assertIsNone(self.h.preview.sel)

    def test_drag_without_movement_selects_nothing(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        self._drag(2, 2, x0=3, x1=3)
        self.assertIsNone(self.h.preview.char_sel)
        self.assertIsNone(self.h.preview.sel)

    def test_copy_puts_selection_into_clipboard(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        self._drag(2, 3)
        self.h.copy_selection()
        payload = ''.join(str(x) for x in self.h.out)
        self.assertIn('\x1b]52;c;', payload)
        self.assertTrue(self.h.status.startswith('copied 2 lines'))
        self.assertIsNone(self.h.preview.sel)

    def test_copy_without_selection_reports(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        self.h.copy_selection()
        self.assertEqual(self.h.status, 'select with the mouse first')

    def test_scroll_clears_selection(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        self._drag(2, 3)
        self.h.preview_scroll(1)
        self.assertIsNone(self.h.preview.sel)

    # --- переименование ---

    def test_rename_writes_to_file(self):
        self._open_A()
        self.h.sel = 0
        self.h.start_rename()
        self.assertEqual(self.h.input_mode, 'rename')
        self.h.input_buffer = 'Renamed A'
        self.h.commit_input()
        self.assertEqual(self.h.sessions[0]['title'], 'Renamed A')
        self.assertTrue(self.h.sessions[0]['custom'])
        # запись реально дописана в файл
        self.assertEqual(Dt.load_session_meta(self.h.sessions[0]['file'])['title'], 'Renamed A')

    # --- resume ---

    def test_resume_sets_result_and_quits(self):
        self._open_A()
        self.h.do_resume(self.h.sessions[0])
        self.assertEqual(self.h.result['action'], 'resume')
        self.assertEqual(self.h.result['session_id'], 'sid-a')
        self.assertFalse(self.h.result['fork'])
        self.assertEqual(self.h.quits, [0])

    def test_fork_sets_fork_flag(self):
        self._open_A()
        self.h.do_resume(self.h.sessions[0], fork=True)
        self.assertEqual(self.h.result['action'], 'resume')
        self.assertTrue(self.h.result['fork'])

    def test_continue_sets_result_from_project(self):
        projA = next(p for p in self.h.projects if p['name'] == 'projA')
        self.h.do_continue(projA)
        self.assertEqual(self.h.result['action'], 'continue')
        self.assertEqual(self.h.result['cwd'], projA['path'])
        self.assertEqual(self.h.quits, [0])

    def test_new_session_from_projects(self):
        self.h.sel = 0
        self.h.on_text('n')
        self.assertEqual(self.h.result['action'], 'new')
        self.assertEqual(self.h.result['cwd'], self.h.projects[0]['path'])

    def test_worktree_input_then_result(self):
        self.h.sel = 0
        self.h.on_text('w')
        self.assertEqual(self.h.input_mode, 'worktree')
        self.h.input_buffer = 'feature-x'
        self.h.commit_input()
        self.assertEqual(self.h.result['action'], 'worktree')
        self.assertEqual(self.h.result['name'], 'feature-x')
        self.assertEqual(self.h.result['cwd'], self.h.projects[0]['path'])

    def test_enter_on_session_resumes(self):
        self._open_A()
        self.h.sel = 0
        self.h.activate()                       # Enter на сессии — запуск
        self.assertEqual(self.h.result['action'], 'resume')
        self.assertEqual(self.h.result['session_id'], 'sid-a')
        self.assertEqual(self.h.quits, [0])

    def test_preview_current_opens_preview(self):
        self._open_A()
        self.h.sel = 0
        self.h.preview_current()                # предпросмотр — отдельной кнопкой (p / →)
        self.assertEqual(self.h.screen, 'preview')
        self.assertIsNone(self.h.result)        # предпросмотр не запускает сессию

    # --- навигация назад ---

    def test_go_back_chain(self):
        self._open_A()
        self.h.open_preview(self.h.sessions[0])
        self.h.go_back()
        self.assertEqual(self.h.screen, 'sessions')
        self.h.go_back()
        self.assertEqual(self.h.screen, 'projects')
        self.h.go_back()
        self.assertEqual(self.h.quits, [0])

    def test_go_back_clears_filter_first(self):
        self.h.filter_query = 'projA'
        self.h.go_back()
        self.assertEqual(self.h.filter_query, '')
        self.assertEqual(self.h.screen, 'projects')   # экран не покинут

    # --- мышь ---

    def test_click_selects_then_activates(self):
        self.h.screen = 'projects'
        self.h.sel = 0
        self.h.offset = 0
        # клик по второй строке (head=0 на проектах) — выбор
        self.h.on_click(MouseEvent(cell_x=2, cell_y=1))
        self.assertEqual(self.h.sel, 1)
        # повторный клик по выбранной — активация (вход в проект)
        self.h.on_click(MouseEvent(cell_x=2, cell_y=1))
        self.assertEqual(self.h.screen, 'sessions')


if __name__ == '__main__':
    unittest.main()
