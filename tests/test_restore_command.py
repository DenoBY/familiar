import unittest

import kittymock  # noqa: F401
import modules.restore.command as Cmd


class TestSafeProgram(unittest.TestCase):
    def test_picks_whitelisted_by_basename(self):
        self.assertEqual(Cmd.safe_program([['/usr/bin/zsh'], ['/opt/bin/nvim', 'a.py']]),
                         ['/opt/bin/nvim', 'a.py'])

    def test_ignores_everything_else(self):
        self.assertIsNone(Cmd.safe_program([['zsh'], ['rm', '-rf', 'build']]))

    def test_empty_cmdlines(self):
        self.assertIsNone(Cmd.safe_program([[], []]))


class TestRestoreCommand(unittest.TestCase):
    def test_claude_session_wins_over_everything(self):
        cmd = Cmd.restore_command(session_id='abc-123', program=['nvim'],
                                  scrollback='/tmp/sb.txt')
        self.assertEqual(cmd, 'claude --resume abc-123')

    def test_session_id_is_quoted(self):
        cmd = Cmd.restore_command(session_id='a b; rm -rf /')
        self.assertEqual(cmd, "claude --resume 'a b; rm -rf /'")

    def test_program_wins_over_scrollback(self):
        # htop и подобные затирают напечатанный экран первым же кадром
        cmd = Cmd.restore_command(program=['nvim', 'a b.py'],
                                  scrollback='/tmp/s b.txt')
        self.assertEqual(cmd, "nvim 'a b.py'")

    def test_scrollback_alone(self):
        self.assertEqual(Cmd.restore_command(scrollback='/tmp/s b.txt'),
                         "cat '/tmp/s b.txt'")

    def test_nothing_to_restore(self):
        self.assertEqual(Cmd.restore_command(), '')


if __name__ == '__main__':
    unittest.main()
