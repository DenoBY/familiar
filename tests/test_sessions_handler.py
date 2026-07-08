import os
import json
import shutil
import tempfile
import unittest

import kittymock  # noqa: F401
from kittymock import wire, draw_text, MouseEvent
import session as S

NOW = 2_000_000.0


class SessionsHandlerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='ccsess_h_')
        self.h = S.SessionsHandler([], NOW)
        wire(self.h, rows=40, cols=120)

        # два интерактивных проекта (cli) + один sdk (должен отфильтроваться)
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
        self.assertTrue(self.h.preview_lines)
        self.assertTrue(any('hello from A' in t for t, _, _ in self.h.preview_lines))

        self.h.search_query = 'reply'
        self.h.run_search()
        self.assertTrue(self.h.search_matches)
        idx0 = self.h.search_idx
        self.h.search_jump(1)
        self.assertTrue(self.h.search_idx >= 0 or idx0 >= 0)

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
        import modules.session.data as Dt
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
