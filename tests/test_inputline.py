import unittest

import kittymock  # noqa: F401
from modules.inputline import InputLine


class Host(InputLine):
    multiline_modes = ('comment',)

    def draw_screen(self):
        pass


class InputLineCaretTest(unittest.TestCase):
    def setUp(self):
        self.h = Host()
        self.h.start_input('find', 'hello')

    def caret(self, width=80):
        _, row, col = self.h.input_layout(width)
        return row, col

    def test_start_puts_caret_at_end(self):
        self.assertEqual(self.h.input_pos, 5)
        # ' find: ' — 7 колонок префикса
        self.assertEqual(self.caret(), (0, 12))

    def test_left_right_clamped(self):
        for _ in range(10):
            self.h.input_key('LEFT')
        self.assertEqual(self.h.input_pos, 0)
        self.h.input_key('RIGHT')
        self.assertEqual(self.caret(), (0, 8))
        for _ in range(10):
            self.h.input_key('RIGHT')
        self.assertEqual(self.h.input_pos, 5)

    def test_home_end(self):
        self.h.input_key('HOME')
        self.assertEqual(self.h.input_pos, 0)
        self.h.input_key('END')
        self.assertEqual(self.h.input_pos, 5)

    def test_insert_at_caret(self):
        for _ in range(3):
            self.h.input_key('LEFT')
        self.h.input_text('XY')
        self.assertEqual(self.h.input_buffer, 'heXYllo')
        self.assertEqual(self.h.input_pos, 4)

    def test_backspace_at_caret(self):
        self.h.input_key('LEFT')
        self.h.input_key('BACKSPACE')
        self.assertEqual(self.h.input_buffer, 'helo')
        self.assertEqual(self.h.input_pos, 3)
        self.h.input_key('HOME')
        self.h.input_key('BACKSPACE')   # в начале — не срабатывает
        self.assertEqual(self.h.input_buffer, 'helo')

    def test_delete_at_caret(self):
        self.h.input_key('HOME')
        self.h.input_key('DELETE')
        self.assertEqual(self.h.input_buffer, 'ello')
        self.assertEqual(self.h.input_pos, 0)
        self.h.input_key('END')
        self.h.input_key('DELETE')      # в конце — не срабатывает
        self.assertEqual(self.h.input_buffer, 'ello')

    def test_kill_word_keeps_tail(self):
        self.h.start_input('find', 'one two tail')
        for _ in range(5):
            self.h.input_key('LEFT')    # каретка после «two»
        self.h.input_kill_word()
        self.assertEqual(self.h.input_buffer, 'one  tail')
        self.assertEqual(self.h.input_pos, 4)

    def test_kill_all_resets_caret(self):
        self.h.input_key('LEFT')
        self.h.input_kill_all()
        self.assertEqual(self.h.input_buffer, '')
        self.assertEqual(self.h.input_pos, 0)

    def test_layout_text_has_no_caret_glyph(self):
        # каретку рисует курсор терминала — текст не сдвигается
        self.assertEqual(self.h.input_lines(80), [' find: hello'])
        self.h.input_key('LEFT')
        self.assertEqual(self.h.input_lines(80), [' find: hello'])

    def test_caret_moves_inside_line(self):
        for _ in range(3):
            self.h.input_key('LEFT')
        self.assertEqual(self.caret(), (0, 9))

    def test_caret_on_lost_wrap_space(self):
        # ширина укладывает 'one'/'two' на разные строки — пробел
        # между ними исчезает из вёрстки; каретка на нём встаёт в
        # начало следующей визуальной строки
        self.h.start_input('find', 'one two')
        lines, _, _ = self.h.input_layout(12)   # w = 12-7-1 = 4
        self.assertEqual(lines, [' find: one', '       two'])
        self.h.input_pos = 3                    # на потерянном пробеле
        self.assertEqual(self.caret(12), (1, 7))
        self.h.input_pos = 5                    # внутри 'two'
        self.assertEqual(self.caret(12), (1, 8))

    def test_caret_at_newline_stays_on_its_line(self):
        self.h.start_input('comment', 'ab\ncd')
        self.h.input_pos = 2                    # на \n — конец первой строки
        lines, row, col = self.h.input_layout(80)
        self.assertEqual(lines, [' comment: ab', '          cd'])
        self.assertEqual((row, col), (0, 12))
        self.h.input_pos = 3                    # начало второй строки
        self.assertEqual(self.caret(), (1, 10))

    def test_newline_inserted_at_caret(self):
        self.h.start_input('comment', 'ab')
        self.h.input_key('LEFT')
        self.h.input_key('ENTER', shift=True)
        self.assertEqual(self.h.input_buffer, 'a\nb')
        self.assertEqual(self.h.input_pos, 2)

    def test_cancel_resets_caret(self):
        self.h.input_key('LEFT')
        self.h.cancel_input()
        self.assertEqual(self.h.input_pos, 0)
        self.assertEqual(self.h.input_buffer, '')


if __name__ == '__main__':
    unittest.main()
