import os
import shutil
import tempfile
import unittest
from unittest import mock

import kittymock  # noqa: F401
import modules.restore.snapshot as Sn
import modules.restore.store as St


LAUNCH = 'launch \'kitty-unserialize-data={"id": %d}\' zsh -l'


class FakeChild:
    def __init__(self, pid, processes):
        self.pid = pid
        self.foreground_processes = [{'cmdline': list(c)} for c in processes]


class FakeWindow:
    def __init__(self, id, pid=0, processes=(), text=''):
        self.id = id
        self.child = FakeChild(pid, processes)
        self.user_vars = {}
        self._text = text

    def as_text(self, as_ansi=False, add_history=False):
        return self._text

    def set_user_var(self, key, val):
        if val is None:
            self.user_vars.pop(key, None)
        else:
            self.user_vars[key] = val


class FakeBoss:
    """Отдаёт строки, какие пишет kitty: у каждого окна своя launch."""

    def __init__(self, windows):
        self.all_windows = windows
        self.vars_when_serialized = {}

    def serialize_state_as_session(self):
        self.vars_when_serialized = {w.id: dict(w.user_vars) for w in self.all_windows}
        lines = ['new_tab', 'cd /old']
        for w in self.all_windows:
            line = LAUNCH % w.id
            token = w.user_vars.get('cc_restore')
            if token:
                line = line.replace('launch ', f'launch --var=cc_restore={token} ', 1)
            lines.append(line)
        return iter(lines)


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='ccrestore_')
        self.env = mock.patch.dict(os.environ, {'XDG_STATE_HOME': self.tmp})
        self.env.start()
        self.running = {}
        patches = (
            mock.patch.object(Sn, 'running_sessions', side_effect=lambda: self.running),
            mock.patch.object(Sn, 'parent_pids', return_value={}),
        )
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def written(self):
        with open(St.last_session_path()) as f:
            return f.read()


class TestWriteSnapshot(SnapshotTest):
    def test_no_windows_keeps_the_previous_snapshot(self):
        self.assertIsNone(Sn.write_snapshot(FakeBoss([])))
        self.assertFalse(os.path.exists(St.last_session_path()))

    def test_claude_window_is_resumed_with_its_own_session(self):
        self.running = {'sid-1': {'pid': 500, 'cwd': '/proj'}}
        boss = FakeBoss([FakeWindow(1, pid=500)])
        Sn.write_snapshot(boss)
        out = self.written()
        self.assertIn('--env=KITTY_SI_RUN_COMMAND_AT_STARTUP=claude --resume sid-1', out)
        self.assertIn('--cwd=/proj', out)

    def test_claude_window_keeps_no_scrollback(self):
        self.running = {'sid-1': {'pid': 500, 'cwd': '/proj'}}
        boss = FakeBoss([FakeWindow(1, pid=500, text='a\nb\nc\n')])
        Sn.write_snapshot(boss)
        self.assertNotIn('cat ', self.written())

    def test_safe_program_is_restarted(self):
        boss = FakeBoss([FakeWindow(1, processes=[['/bin/zsh'], ['nvim', 'x.py']])])
        Sn.write_snapshot(boss)
        self.assertIn('nvim x.py', self.written())

    def test_no_scrollback_dump_when_a_program_is_restarted(self):
        boss = FakeBoss([FakeWindow(1, processes=[['htop']], text='a\nb\nc\n')])
        path = Sn.write_snapshot(boss)
        self.assertEqual(os.listdir(os.path.dirname(path)), [St.SESSION_NAME])

    def test_scrollback_is_dumped_and_referenced(self):
        boss = FakeBoss([FakeWindow(1, text='one\ntwo\nthree\n')])
        path = Sn.write_snapshot(boss)
        dump = St.scrollback_path(os.path.dirname(path), 'w1')
        self.assertTrue(os.path.exists(dump))
        self.assertIn('cat ' + dump, self.written())
        with open(dump) as f:
            self.assertIn('two', f.read())

    def test_plain_window_is_left_untouched(self):
        boss = FakeBoss([FakeWindow(1, processes=[['zsh']])])
        Sn.write_snapshot(boss)
        out = self.written()
        self.assertNotIn('--env=', out)
        self.assertIn('launch', out)

    def test_token_is_set_for_serialization_and_removed_after(self):
        boss = FakeBoss([FakeWindow(1, text='one\ntwo\nthree\n')])
        Sn.write_snapshot(boss)
        self.assertEqual(boss.vars_when_serialized[1].get('cc_restore'), 'w1')
        self.assertEqual(boss.all_windows[0].user_vars, {})

    def test_layout_lines_from_kitty_are_kept(self):
        boss = FakeBoss([FakeWindow(1, processes=[['zsh']])])
        Sn.write_snapshot(boss)
        self.assertIn('new_tab', self.written())
        self.assertIn('cd /old', self.written())

    def test_each_window_gets_its_own_command(self):
        self.running = {'sid-1': {'pid': 500, 'cwd': '/proj'}}
        boss = FakeBoss([FakeWindow(1, pid=500),
                         FakeWindow(2, processes=[['htop']])])
        Sn.write_snapshot(boss)
        lines = [ln for ln in self.written().splitlines() if ln.startswith('launch')]
        self.assertIn('claude --resume sid-1', lines[0])
        self.assertIn('htop', lines[1])
        self.assertNotIn('claude', lines[1])


class TestSnapshotter(SnapshotTest):
    def test_rate_limited_between_takes(self):
        boss = FakeBoss([FakeWindow(1, processes=[['zsh']])])
        s = Sn.Snapshotter(min_interval=1000.0)
        self.assertIsNotNone(s.take(boss))
        self.assertIsNone(s.take(boss))

    def test_force_ignores_the_interval(self):
        boss = FakeBoss([FakeWindow(1, processes=[['zsh']])])
        s = Sn.Snapshotter(min_interval=1000.0)
        s.take(boss)
        boss.all_windows.append(FakeWindow(2, processes=[['htop']]))
        self.assertIsNotNone(s.take(boss, force=True))

    def test_unchanged_state_is_not_written_again(self):
        """Иначе фоновый таймер круглые сутки гонял бы на диск одно и
        то же, а ротация вымывала бы снимки, ещё нужные для возврата.
        """
        boss = FakeBoss([FakeWindow(1, processes=[['htop']])])
        s = Sn.Snapshotter(min_interval=0.0)
        self.assertIsNotNone(s.take(boss))
        self.assertIsNone(s.take(boss, force=True))

    def test_changed_scrollback_is_written(self):
        window = FakeWindow(1, text='one\ntwo\nthree\n')
        boss = FakeBoss([window])
        s = Sn.Snapshotter(min_interval=0.0)
        s.take(boss)
        window._text = 'one\ntwo\nthree\nfour\n'
        self.assertIsNotNone(s.take(boss))


if __name__ == '__main__':
    unittest.main()
