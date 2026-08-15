import unittest

import kittymock  # noqa: F401
import modules.close.screen as Cs
from kittymock import KeyEvent, draw_text, wire


class TestBody(unittest.TestCase):
    def plain(self, label='', hint='', width=80):
        return [text for text, _ in Cs.body(label, hint, width)]

    def test_question_comes_first(self):
        self.assertEqual(self.plain()[0], 'Close this pane?')

    def test_label_and_hint_are_listed(self):
        lines = self.plain('claude · kitty · busy', 'Reopen it later.')
        self.assertIn('claude · kitty · busy', lines)
        self.assertIn('Reopen it later.', lines)

    def test_question_alone_without_a_label(self):
        self.assertEqual(self.plain(), ['Close this pane?'])

    def test_long_label_is_cut_to_the_pane_width(self):
        line = self.plain('nvim ' + 'a' * 200, width=30)[2]
        self.assertEqual(len(line), 30)
        self.assertTrue(line.endswith('…'))


class TestScreen(unittest.TestCase):
    def screen(self, label='claude · kitty · busy', hint='Saved.'):
        h = wire(Cs.CloseScreen(label, hint), rows=24, cols=80)
        h.start_quit_confirm()
        return h

    def test_buttons_match_the_other_kittens(self):
        text = draw_text(self.screen())
        self.assertIn('│ Yes │', text)
        self.assertIn('│ No │', text)

    def test_question_and_label_are_drawn(self):
        text = draw_text(self.screen())
        self.assertIn('Close this pane?', text)
        self.assertIn('claude · kitty · busy', text)

    def test_screen_fits_its_rows(self):
        h = self.screen()
        self.assertLessEqual(len(h.out), h.screen_size.rows)

    def test_enter_confirms(self):
        h = self.screen()
        h.on_key(KeyEvent('ENTER'))
        self.assertTrue(h.confirmed)
        self.assertEqual(h.quits, [0])

    def test_escape_cancels(self):
        h = self.screen()
        h.on_key(KeyEvent('ESCAPE'))
        self.assertFalse(h.confirmed)

    def test_y_confirms_n_cancels(self):
        h = self.screen()
        h.on_text('y')
        self.assertTrue(h.confirmed)
        h = self.screen()
        h.on_text('n')
        self.assertFalse(h.confirmed)

    def test_ctrl_c_cancels(self):
        h = self.screen()
        h.on_text('\x03')
        self.assertFalse(h.confirmed)
        self.assertEqual(h.quits, [0])


if __name__ == '__main__':
    unittest.main()
