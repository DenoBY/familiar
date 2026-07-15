import unittest

import kittymock  # noqa: F401
from kittymock import KeyEvent, MouseEvent, draw_text, wire
from modules.confirm import ConfirmQuit
from modules.pointer import PointerCursor


class Dummy(ConfirmQuit, PointerCursor):
    def draw_screen(self):
        self.out.clear()
        self.draw_quit_confirm()

    def _wanted_pointer(self, ev) -> 'str | None':
        return self.confirm_pointer(ev) if self.confirm_active else None


class ConfirmQuitTest(unittest.TestCase):
    def setUp(self):
        self.h = wire(Dummy(), rows=20, cols=80)

    def test_inactive_passes_input_through(self):
        self.assertFalse(self.h.confirm_key(KeyEvent('ESCAPE')))
        self.assertFalse(self.h.confirm_text('y'))
        self.assertFalse(self.h.confirm_click(MouseEvent()))

    def test_start_draws_question_and_buttons(self):
        self.h.start_quit_confirm()
        self.assertTrue(self.h.confirm_active)
        text = draw_text(self.h)
        self.assertIn(self.h.QUIT_CONFIRM_MSG, text)
        self.assertIn('│ Yes │', text)
        self.assertIn('│ No │', text)

    def test_enter_confirms_focused_yes(self):
        self.h.start_quit_confirm()
        self.assertTrue(self.h.confirm_key(KeyEvent('ENTER')))
        self.assertEqual(self.h.quits, [0])

    def test_arrow_moves_focus_to_no(self):
        self.h.start_quit_confirm()
        self.h.confirm_key(KeyEvent('RIGHT'))
        self.h.confirm_key(KeyEvent('ENTER'))
        self.assertFalse(self.h.confirm_active)
        self.assertEqual(self.h.quits, [])

    def test_y_confirms_n_cancels(self):
        self.h.start_quit_confirm()
        self.h.confirm_text('n')
        self.assertFalse(self.h.confirm_active)
        self.assertEqual(self.h.quits, [])
        self.h.start_quit_confirm()
        self.h.confirm_text('y')
        self.assertEqual(self.h.quits, [0])

    def test_russian_layout_yn(self):
        self.h.start_quit_confirm()
        self.h.confirm_text('т')            # клавиша n на ЙЦУКЕН
        self.assertFalse(self.h.confirm_active)
        self.h.start_quit_confirm()
        self.h.confirm_text('н')            # клавиша y на ЙЦУКЕН
        self.assertEqual(self.h.quits, [0])

    def test_escape_cancels(self):
        self.h.start_quit_confirm()
        self.h.confirm_key(KeyEvent('ESCAPE'))
        self.assertFalse(self.h.confirm_active)
        self.assertEqual(self.h.quits, [])

    def test_ctrl_c_always_quits(self):
        self.h.start_quit_confirm()
        self.h.confirm_key(KeyEvent('c', ctrl=True))
        self.assertEqual(self.h.quits, [0])
        self.h.quits.clear()
        self.h.start_quit_confirm()
        self.h.confirm_text('\x03')         # на кириллице ⌃c приходит C0-байтом
        self.assertEqual(self.h.quits, [0])

    def test_click_buttons(self):
        self.h.start_quit_confirm()
        (yrow, yx0, _), (nrow, nx0, _) = self.h._confirm_hitboxes
        self.h.confirm_click(MouseEvent(cell_x=nx0 + 1, cell_y=nrow, buttons=1))
        self.assertFalse(self.h.confirm_active)
        self.assertEqual(self.h.quits, [])
        self.h.start_quit_confirm()
        self.h.confirm_click(MouseEvent(cell_x=yx0 + 1, cell_y=yrow, buttons=1))
        self.assertEqual(self.h.quits, [0])

    def test_pointer_hand_over_buttons_only(self):
        self.h.start_quit_confirm()
        row, x0, _ = self.h._confirm_hitboxes[0]
        self.assertEqual(self.h.confirm_pointer(
            MouseEvent(cell_x=x0 + 1, cell_y=row)), 'pointer')
        self.assertIsNone(self.h.confirm_pointer(MouseEvent(cell_x=0, cell_y=0)))

    def test_click_outside_is_swallowed(self):
        self.h.start_quit_confirm()
        self.assertTrue(self.h.confirm_click(MouseEvent(cell_x=0, cell_y=0, buttons=1)))
        self.assertTrue(self.h.confirm_active)
        self.assertEqual(self.h.quits, [])

    def test_other_keys_are_swallowed(self):
        self.h.start_quit_confirm()
        self.assertTrue(self.h.confirm_key(KeyEvent('UP')))
        self.assertTrue(self.h.confirm_text('q'))
        self.assertTrue(self.h.confirm_active)
        self.assertEqual(self.h.quits, [])


if __name__ == '__main__':
    unittest.main()
