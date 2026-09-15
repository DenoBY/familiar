import unittest

import kittymock  # noqa: F401
from modules.vcs.tabstops import Tabstops


class TabstopsTest(unittest.TestCase):
    def test_typing_in_current_keeps_the_next_in_place(self):
        lines = ['where(column, value)']
        stops = Tabstops([(0, 6, 12), (0, 14, 19), (0, 20, 20)], lines)
        new = ["where('email', value)"]
        self.assertTrue(stops.update(lines, new))
        self.assertEqual(stops.jump(1, new), (0, 15, 20))
        self.assertEqual(new[0][15:20], 'value')

    def test_previous_stays_when_typing_after_it(self):
        lines = ["where('email', value)"]
        stops = Tabstops([(0, 6, 13), (0, 15, 20)], lines)
        stops.jump(1, lines)
        new = ["where('email', $email)"]
        self.assertTrue(stops.update(lines, new))
        self.assertEqual(stops.jump(-1, new), (0, 6, 13))

    def test_lines_below_shift_with_inserted_lines(self):
        lines = ['f() {', '    ${body}', '}']
        stops = Tabstops([(0, 2, 2), (1, 4, 11)], lines)
        new = ['f(a,', '  b) {', '    ${body}', '}']
        self.assertTrue(stops.update(lines, new))
        self.assertEqual(stops.places(new)[1], (2, 4, 11))

    def test_enter_inside_placeholder_moves_the_tail_down(self):
        lines = ['call(a, b)']
        stops = Tabstops([(0, 5, 6), (0, 8, 9)], lines)
        new = ['call(a', ', b)']
        self.assertTrue(stops.update(lines, new))
        self.assertEqual(stops.places(new)[1], (1, 2, 3))

    def test_edit_over_a_placeholder_breaks_the_session(self):
        lines = ['one', 'two', 'three']
        stops = Tabstops([(0, 0, 1), (1, 0, 3)], lines)
        self.assertFalse(stops.update(lines, ['one', 'three']))

    def test_jump_past_the_ends(self):
        lines = ['ab']
        stops = Tabstops([(0, 0, 1), (0, 2, 2)], lines)
        self.assertIsNone(stops.jump(-1, lines))
        self.assertEqual(stops.jump(1, lines), (0, 2, 2))
        self.assertIsNone(stops.jump(1, lines))


if __name__ == '__main__':
    unittest.main()
