import unittest

import kittymock  # noqa: F401
import modules.restore.scrollback as Sb


def screen(*lines):
    """Экран, который заведомо переживёт порог min_lines."""
    return '\n'.join(lines + ('a', 'b', 'c'))


class TestPrepare(unittest.TestCase):
    def test_keeps_colors(self):
        out = Sb.prepare(screen('\x1b[31mred\x1b[m'))
        self.assertIn('\x1b[31mred', out)

    def test_drops_escapes_that_would_be_executed(self):
        # alt-screen, очистка и запрос позиции курсора при `cat`
        # исполнились бы заново
        out = Sb.prepare(screen('\x1b[?1049hx', '\x1b[2Jy', '\x1b[6nz'))
        self.assertNotIn('1049', out)
        self.assertNotIn('\x1b[2J', out)
        self.assertNotIn('\x1b[6n', out)
        self.assertIn('x', out)

    def test_drops_osc(self):
        out = Sb.prepare(screen('\x1b]0;title\x07text'))
        self.assertNotIn('title', out)
        self.assertIn('text', out)

    def test_ends_with_sgr_reset(self):
        self.assertTrue(Sb.prepare(screen('\x1b[31mred')).endswith('\x1b[m\n'))

    def test_trailing_blank_lines_dropped(self):
        out = Sb.prepare(screen('x') + '\n\n\n   \n')
        self.assertEqual(out.splitlines(), ['x', 'a', 'b', 'c\x1b[m'])

    def test_bare_prompt_is_not_worth_restoring(self):
        self.assertEqual(Sb.prepare('➜  kitty git:(master)'), '')

    def test_min_lines_counts_only_non_blank(self):
        self.assertEqual(Sb.prepare('a\n\n\nb\n'), '')
        self.assertNotEqual(Sb.prepare('a\nb\nc\n'), '')

    def test_line_limit_keeps_the_tail(self):
        out = Sb.prepare('\n'.join(str(i) for i in range(100)), max_lines=3)
        self.assertEqual(out, '97\n98\n99\x1b[m\n')

    def test_byte_limit_cuts_on_a_line_boundary(self):
        out = Sb.prepare('\n'.join('x' * 20 for _ in range(50)), max_bytes=100)
        self.assertLessEqual(len(out.encode('utf-8')), 110)
        self.assertTrue(all(line == 'x' * 20 for line in out.splitlines()[:-1] if line))

    def test_empty_input(self):
        self.assertEqual(Sb.prepare(''), '')


if __name__ == '__main__':
    unittest.main()
