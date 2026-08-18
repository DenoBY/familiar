import unittest  # noqa: I001
from unittest import mock

# kittymock ставит заглушки kitty и кладёт plugins/ в sys.path, без
# него точки входа китов не импортируются; сортировщик поставил бы
# его после `close` по алфавиту, поэтому блок закреплён.
import kittymock  # noqa: F401
import close as C
import close_ask as CA


ASK = '/root/plugins/close_ask.py'


class FakeChild:
    def __init__(self, pid=0, cmdline=()):
        self.pid = pid
        self.foreground_cmdline = list(cmdline)


class FakeWindow:
    def __init__(self, running=True, pid=0, cmdline=()):
        self.has_running_program = running
        self.child = FakeChild(pid, cmdline)
        self.overlay_parent = None


class FakeBoss:
    def __init__(self, window):
        self.window_id_map = {7: window} if window else {}
        self.closed = []
        self.kittens = []
        self.fallbacks = []

    def mark_window_for_close(self, window):
        self.closed.append(window)

    def kitten(self, path, *args):
        self.kittens.append((path, args))

    def close_window_with_confirmation(self, ignore_shell):
        self.fallbacks.append(ignore_shell)


class DecideTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(C, 'claude_session', return_value=None)
        self.session = patch.start()
        self.addCleanup(patch.stop)

    def run_close(self, window, args=('close', ASK)):
        boss = FakeBoss(window)
        C.handle_result(list(args), None, 7, boss)
        return boss

    def test_pane_at_the_prompt_closes_without_a_question(self):
        window = FakeWindow(running=False)
        boss = self.run_close(window)
        self.assertEqual(boss.closed, [window])
        self.assertEqual(boss.kittens, [])

    def test_running_program_opens_the_question(self):
        boss = self.run_close(FakeWindow(cmdline=['/opt/bin/nvim', 'a.py']))
        self.assertEqual(boss.closed, [])
        self.assertEqual(boss.kittens, [(ASK, ('nvim a.py', ''))])

    def test_claude_window_is_named_by_its_session(self):
        self.session.return_value = {'pid': 500, 'cwd': '/x/familiar',
                                     'status': 'busy'}
        boss = self.run_close(FakeWindow(pid=500, cmdline=['caffeinate', '-i']))
        path, (label, hint) = boss.kittens[0]
        self.assertEqual(label, 'claude · familiar · busy')
        self.assertTrue(hint)

    def test_missing_window_does_nothing(self):
        boss = self.run_close(None)
        self.assertEqual((boss.closed, boss.kittens), ([], []))

    def test_map_without_the_screen_path_falls_back_to_kitty(self):
        """Иначе клавиша молча не делала бы ничего."""
        boss = self.run_close(FakeWindow(cmdline=['htop']), args=('close',))
        self.assertEqual(boss.fallbacks, [True])
        self.assertEqual(boss.kittens, [])


class AnswerTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(CA, 'close_pane')
        self.close_pane = patch.start()
        self.addCleanup(patch.stop)

    def run_answer(self, result, window=None):
        boss = FakeBoss(window)
        CA.handle_result(['close_ask'], result, 7, boss)
        return boss

    def test_yes_closes_the_pane(self):
        window = FakeWindow()
        boss = self.run_answer({'action': 'close'}, window)
        self.close_pane.assert_called_once_with(boss, window)

    def test_no_keeps_it(self):
        self.run_answer({'action': 'cancel'}, FakeWindow())
        self.close_pane.assert_not_called()

    def test_missing_result_keeps_it(self):
        self.run_answer(None, FakeWindow())
        self.close_pane.assert_not_called()

    def test_pane_closed_while_the_question_was_up(self):
        self.run_answer({'action': 'close'})
        self.close_pane.assert_not_called()


class ScreenArgsTest(unittest.TestCase):
    def test_main_passes_the_argv_strings_to_the_screen(self):
        with mock.patch.object(CA, 'Loop') as loop, \
                mock.patch.object(CA, 'mark_overlay'):
            loop.return_value.loop.side_effect = lambda h: setattr(h, 'confirmed', True)
            result = CA.main(['close_ask', 'claude · kitty · busy', 'Saved.'])
        self.assertEqual(result, {'action': 'close'})

    def test_main_survives_a_call_without_arguments(self):
        with mock.patch.object(CA, 'Loop'), mock.patch.object(CA, 'mark_overlay'):
            self.assertEqual(CA.main(['close_ask']), {'action': 'cancel'})


if __name__ == '__main__':
    unittest.main()
