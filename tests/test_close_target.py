import unittest

import kittymock  # noqa: F401
import modules.close.target as T


class TestProgramLabel(unittest.TestCase):
    def test_binary_path_is_stripped(self):
        self.assertEqual(T.program_label(['/opt/homebrew/bin/nvim', 'a.py']),
                         'nvim a.py')

    def test_long_command_is_cut_to_first_arguments(self):
        label = T.program_label(['make', '-j8', 'all', 'CC=clang', 'V=1'])
        self.assertEqual(label, 'make -j8 all CC=clang')

    def test_bare_command(self):
        self.assertEqual(T.program_label(['htop']), 'htop')

    def test_nothing_running(self):
        self.assertEqual(T.program_label([]), '')


class TestClaudeLabel(unittest.TestCase):
    def test_project_and_status(self):
        self.assertEqual(T.claude_label({'cwd': '/Users/d/Projects/kitty',
                                         'status': 'busy'}),
                         'claude · kitty · busy')

    def test_missing_status_is_idle(self):
        self.assertEqual(T.claude_label({'cwd': '/x/wiki/'}),
                         'claude · wiki · idle')

    def test_no_cwd(self):
        self.assertEqual(T.claude_label({}), 'claude · ? · idle')


class TestDescribe(unittest.TestCase):
    def test_claude_session_wins_over_the_foreground_process(self):
        """В окне claude foreground-процессом бывает его потомок —
        caffeinate или MCP-сервер; штатный вопрос kitty показывает
        именно его, потому кит и появился.
        """
        label, hint = T.describe({'cwd': '/x/familiar', 'status': 'busy'},
                                 ['/usr/bin/caffeinate', '-i'])
        self.assertEqual(label, 'claude · familiar · busy')
        self.assertEqual(hint, T.CLAUDE_HINT)

    def test_plain_program_has_no_hint(self):
        self.assertEqual(T.describe(None, ['nvim', 'a.py']), ('nvim a.py', ''))

    def test_nothing_known(self):
        self.assertEqual(T.describe(None, []), ('', ''))


if __name__ == '__main__':
    unittest.main()
