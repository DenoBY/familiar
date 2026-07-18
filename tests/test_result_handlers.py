import os
import shutil
import tempfile
import unittest

from test_overlay import FakeTab

import kittymock  # noqa: F401
import log as L
import review as R
import session as S


class FakeChild:
    def __init__(self, foreground=(), background=()):
        self.foreground_processes = list(foreground)
        self.background_processes = list(background)


class FakeWindow:
    def __init__(self, child=None, tab=None):
        self.child = child or FakeChild()
        self._tab = tab

    def tabref(self):
        return self._tab


class FakeBoss:
    def __init__(self):
        self.window_id_map = {7: FakeWindow()}
        self.calls = []

    def call_remote_control(self, w, cmd):
        self.calls.append((w, cmd))


def boss_with_window(window):
    boss = FakeBoss()
    boss.window_id_map = {7: window}
    return boss


class ReviewResultTest(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp(prefix='proj_')   # без .idea/.vscode
        self._backup = {k: os.environ.get(k) for k in ('VISUAL', 'EDITOR')}
        os.environ.pop('VISUAL', None)
        os.environ['EDITOR'] = 'vim'

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_edit_launches_tab_for_terminal_editor(self):
        boss = FakeBoss()
        answer = {'action': 'edit', 'cwd': self.proj, 'path': '/f.py', 'line': 5}
        R.handle_result([], answer, 7, boss)
        self.assertEqual(len(boss.calls), 1)
        _, cmd = boss.calls[0]
        self.assertEqual(cmd, ('launch', '--type=tab', '--cwd', self.proj,
                               'vim', '+5', '/f.py'))

    def test_gui_editor_uses_background(self):
        os.environ['EDITOR'] = 'code'
        boss = FakeBoss()
        answer = {'action': 'edit', 'cwd': self.proj, 'path': '/f.py', 'line': 2}
        R.handle_result([], answer, 7, boss)
        _, cmd = boss.calls[0]
        self.assertEqual(cmd[:4], ('launch', '--type=background', '--cwd', self.proj))

    def test_ignores_non_edit(self):
        boss = FakeBoss()
        R.handle_result([], None, 7, boss)
        R.handle_result([], {'action': 'other'}, 7, boss)
        self.assertEqual(boss.calls, [])


class SessionsResultTest(unittest.TestCase):
    def test_resume_overlay_command(self):
        boss = FakeBoss()
        result = {'action': 'resume', 'session_id': 'SID', 'cwd': '/c'}
        S.handle_result([], result, 7, boss)
        self.assertEqual(len(boss.calls), 1)
        _, cmd = boss.calls[0]
        shell = os.environ.get('SHELL') or '/bin/zsh'
        self.assertEqual(cmd, ('launch', '--type=overlay', '--cwd', '/c',
                               shell, '-l', '-i', '-c', 'exec claude --resume SID'))

    def test_resume_without_cwd(self):
        boss = FakeBoss()
        S.handle_result([], {'action': 'resume', 'session_id': 'SID'}, 7, boss)
        _, cmd = boss.calls[0]
        shell = os.environ.get('SHELL') or '/bin/zsh'
        self.assertEqual(cmd, ('launch', '--type=overlay',
                               shell, '-l', '-i', '-c', 'exec claude --resume SID'))

    def test_resume_fork_appends_flag(self):
        boss = FakeBoss()
        result = {'action': 'resume', 'session_id': 'SID', 'cwd': '/c', 'fork': True}
        S.handle_result([], result, 7, boss)
        _, cmd = boss.calls[0]
        self.assertEqual(cmd[-1], 'exec claude --resume SID --fork-session')

    def test_continue_command(self):
        boss = FakeBoss()
        S.handle_result([], {'action': 'continue', 'cwd': '/c'}, 7, boss)
        _, cmd = boss.calls[0]
        shell = os.environ.get('SHELL') or '/bin/zsh'
        self.assertEqual(cmd, ('launch', '--type=overlay', '--cwd', '/c',
                               shell, '-l', '-i', '-c', 'exec claude --continue'))

    def test_new_session_command(self):
        boss = FakeBoss()
        S.handle_result([], {'action': 'new', 'cwd': '/c'}, 7, boss)
        _, cmd = boss.calls[0]
        self.assertEqual(cmd[-1], 'exec claude')
        self.assertIn('--cwd', cmd)

    def test_worktree_named_and_auto(self):
        boss = FakeBoss()
        S.handle_result([], {'action': 'worktree', 'cwd': '/c', 'name': 'feat x'}, 7, boss)
        self.assertEqual(boss.calls[0][1][-1], "exec claude --worktree 'feat x'")
        S.handle_result([], {'action': 'worktree', 'cwd': '/c', 'name': ''}, 7, boss)
        self.assertEqual(boss.calls[1][1][-1], 'exec claude --worktree')

    def test_ignores_non_resume(self):
        boss = FakeBoss()
        S.handle_result([], None, 7, boss)
        S.handle_result([], {'action': 'edit'}, 7, boss)
        S.handle_result([], {'action': 'resume'}, 7, boss)   # без session_id
        self.assertEqual(boss.calls, [])

    def test_split_when_window_runs_claude(self):
        win = FakeWindow(FakeChild(foreground=[{'cmdline': ['claude', '--resume', 'X']}]))
        boss = boss_with_window(win)
        S.handle_result([], {'action': 'new', 'cwd': '/c'}, 7, boss)
        self.assertEqual(boss.calls[0][1][:2], ('launch', '--location=vsplit'))

    def test_overlay_when_window_has_only_shell(self):
        win = FakeWindow(FakeChild(foreground=[{'cmdline': ['/bin/zsh', '-l', '-i']}]))
        boss = boss_with_window(win)
        S.handle_result([], {'action': 'new', 'cwd': '/c'}, 7, boss)
        self.assertEqual(boss.calls[0][1][:2], ('launch', '--type=overlay'))

    def test_claude_in_background_also_splits(self):
        # claude запустил дочерний процесс — сам claude ушёл
        # в background
        win = FakeWindow(FakeChild(foreground=[{'cmdline': ['/bin/bash', '-c', 'ls']}],
                                   background=[{'cmdline': ['claude']}]))
        boss = boss_with_window(win)
        S.handle_result([], {'action': 'new', 'cwd': '/c'}, 7, boss)
        self.assertEqual(boss.calls[0][1][1], '--location=vsplit')


class RestoreLayoutOnCloseTest(unittest.TestCase):
    """Каждый кит возвращает layout таба из stack при закрытии —
    даже без действия (answer None / 'close').
    """

    def boss_in_stack(self):
        tab = FakeTab()
        return boss_with_window(FakeWindow(tab=tab)), tab

    def test_review_restores_layout(self):
        boss, tab = self.boss_in_stack()
        R.handle_result([], {'action': 'close'}, 7, boss)
        self.assertEqual(tab.gotos, ['splits:split_axis=horizontal'])

    def test_log_restores_layout(self):
        boss, tab = self.boss_in_stack()
        L.handle_result([], {'action': 'close'}, 7, boss)
        self.assertEqual(tab.gotos, ['splits:split_axis=horizontal'])

    def test_session_restores_layout_before_launch(self):
        boss, tab = self.boss_in_stack()
        S.handle_result([], {'action': 'new', 'cwd': '/c'}, 7, boss)
        self.assertEqual(tab.gotos, ['splits:split_axis=horizontal'])
        # claude запускается уже после возврата layout
        self.assertEqual(len(boss.calls), 1)


if __name__ == '__main__':
    unittest.main()
