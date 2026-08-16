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


class TallDummy(Dummy):
    """Вопрос с подписью и подсказкой — как у кита close."""

    def confirm_rows(self):
        rows = [('Close this pane?', 'Close this pane?')]
        for text in ('claude · familiar · busy', 'The conversation is saved.'):
            rows += [('', ''), (text, text)]
        return rows


class FitToPaneTest(unittest.TestCase):
    """Оверлей close открыт в размер панели, а она бывает крошечной."""

    def screen(self, rows, cols=60):
        h = wire(TallDummy(), rows=rows, cols=cols)
        h.start_quit_confirm()
        return h

    def test_tall_pane_keeps_the_framed_buttons(self):
        text = draw_text(self.screen(24))
        self.assertIn('│ Yes │', text)
        self.assertIn('The conversation is saved.', text)

    def test_low_pane_drops_blank_lines_and_button_frames(self):
        h = self.screen(6)
        self.assertLessEqual(len(h.out), 6)
        text = draw_text(h)
        self.assertIn('Close this pane?', text)
        self.assertIn('claude · familiar · busy', text)
        self.assertIn('[ Yes ]', text)

    def test_the_question_survives_even_in_three_rows(self):
        h = self.screen(3)
        self.assertLessEqual(len(h.out), 3)
        self.assertIn('Close this pane?', draw_text(h))
        self.assertIn('[ Yes ]', draw_text(h))

    def test_narrow_pane_does_not_wrap_the_buttons(self):
        """Перенос развалил бы рамку — вместо этого жмётся зазор.

        Содержимое здесь длиннее панели, но его режет сам подкласс
        (у close это truncate по ширине в body).
        """
        h = self.screen(24, cols=14)
        row = h._confirm_hitboxes[0][0]
        self.assertLessEqual(len(draw_text(h).split('\n')[row]), 14)

    def test_buttons_stay_clickable_after_shrinking(self):
        h = self.screen(6)
        (row, x0, _), _ = h._confirm_hitboxes
        self.assertEqual(draw_text(h).split('\n')[row][x0:x0 + 7], '[ Yes ]')


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

    def test_paste_is_not_an_answer(self):
        self.h.start_quit_confirm()
        self.assertTrue(self.h.confirm_text('yes, please', in_bracketed_paste=True))
        self.assertTrue(self.h.confirm_text('\x03', in_bracketed_paste=True))
        self.assertTrue(self.h.confirm_active)
        self.assertEqual(self.h.quits, [])

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
