import unittest  # noqa: I001
from unittest import mock

import kittymock  # noqa: F401
import modules.close.pane as P


SESSION = {'pid': 500, 'cwd': '/x/familiar', 'status': 'busy'}


class FakeChild:
    def __init__(self, pid):
        self.pid = pid


class FakeWindow:
    def __init__(self, pid=500, overlay_parent=None):
        self.child = FakeChild(pid)
        self.overlay_parent = overlay_parent


class FakeBoss:
    def __init__(self):
        self.closed = []

    def mark_window_for_close(self, window):
        self.closed.append(window)


class CloseTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(P, 'claude_session', return_value=SESSION)
        self.session = patch.start()
        self.addCleanup(patch.stop)

    def run_close(self, window):
        boss = FakeBoss()
        P.close(boss, window)
        return boss

    def test_pane_without_a_session_is_closed_alone(self):
        self.session.return_value = None
        window = FakeWindow(overlay_parent=FakeWindow())
        self.assertEqual(self.run_close(window).closed, [window])

    def test_session_takes_the_shell_under_it_along(self):
        """Оверлей поверх шелла: закрыть только его — значит оставить
        на месте сессии пустую панель.
        """
        shell = FakeWindow()
        window = FakeWindow(overlay_parent=shell)
        self.assertEqual(self.run_close(window).closed, [shell, window])

    def test_whole_overlay_stack_goes(self):
        bottom = FakeWindow()
        middle = FakeWindow(overlay_parent=bottom)
        window = FakeWindow(overlay_parent=middle)
        self.assertEqual(self.run_close(window).closed,
                         [middle, bottom, window])

    def test_session_in_a_window_of_its_own(self):
        window = FakeWindow()
        self.assertEqual(self.run_close(window).closed, [window])


class SessionTest(unittest.TestCase):
    def running(self, sessions):
        return mock.patch.object(P, 'running_sessions', return_value=sessions)

    def test_session_is_found_by_the_window_pid(self):
        with self.running({'s': SESSION}):
            self.assertEqual(P.claude_session(FakeWindow()), SESSION)

    def test_window_without_a_pid_has_no_session(self):
        with self.running({'s': SESSION}):
            self.assertIsNone(P.claude_session(FakeWindow(pid=0)))

    def test_nothing_running_at_all(self):
        with self.running({}):
            self.assertIsNone(P.claude_session(FakeWindow()))

    def test_pane_with_another_program(self):
        with self.running({'s': SESSION}), \
                mock.patch.object(P, 'session_id_for_pid', return_value=None):
            self.assertIsNone(P.claude_session(FakeWindow(pid=7)))


if __name__ == '__main__':
    unittest.main()
