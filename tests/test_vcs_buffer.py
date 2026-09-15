"""Модель текста режима правки: каретка, выделение, история и то, что
строки буфера один в один совпадают со строками экрана.
"""

import unittest

import kittymock  # noqa: F401
from modules.vcs.buffer import TextBuffer, decode_editable, line_splice


def buf_at(text, line=0, col=0):
    b = TextBuffer(text)
    b.set_caret(line, col)
    return b


class TextRoundTripTest(unittest.TestCase):
    def test_trailing_newline_kept_as_is(self):
        for text in ('a\nb\n', 'a\nb', '', '\n', 'x'):
            with self.subTest(text=text):
                b = TextBuffer(text)
                self.assertEqual(b.text(), text)
                self.assertFalse(b.modified)

    def test_display_text_gives_a_row_per_buffer_line(self):
        for text in ('', 'a', 'a\n', 'a\n\n'):
            with self.subTest(text=text):
                b = TextBuffer(text)
                self.assertEqual(b.display_text().splitlines(), b.lines)


class InsertTest(unittest.TestCase):
    def test_typing_inserts_at_caret(self):
        b = buf_at('hello\n', 0, 2)
        b.type_char('X')
        self.assertEqual(b.text(), 'heXllo\n')
        self.assertEqual(b.caret, (0, 3))

    def test_multiline_paste_moves_caret_to_its_end(self):
        b = buf_at('ab\n', 0, 1)
        b.insert('1\n2\r\n3')
        self.assertEqual(b.lines, ['a1', '2', '3b'])
        self.assertEqual(b.caret, (2, 1))

    def test_foreign_line_breaks_are_dropped(self):
        b = buf_at('')
        b.insert('a\u2028b\x0cc')
        self.assertEqual(b.lines, ['abc'])

    def test_typing_replaces_selection(self):
        b = TextBuffer('one two\n')
        b.select((0, 0), (0, 3))
        b.type_char('1')
        self.assertEqual(b.text(), '1 two\n')

    def test_newline_copies_indent(self):
        b = buf_at('    x = 1\n', 0, 9)
        b.newline()
        self.assertEqual(b.lines, ['    x = 1', '    '])
        self.assertEqual(b.caret, (1, 4))


class DeleteTest(unittest.TestCase):
    def test_backspace_at_line_start_joins_lines(self):
        b = buf_at('ab\ncd\n', 1, 0)
        b.backspace()
        self.assertEqual(b.lines, ['abcd'])
        self.assertEqual(b.caret, (0, 2))

    def test_delete_at_line_end_joins_next(self):
        b = buf_at('ab\ncd\n', 0, 2)
        b.delete()
        self.assertEqual(b.lines, ['abcd'])

    def test_multiline_selection_deleted_at_once(self):
        b = TextBuffer('abc\ndef\nghi\n')
        b.select((0, 1), (2, 2))
        self.assertEqual(b.selected_text(), 'bc\ndef\ngh')
        b.backspace()
        self.assertEqual(b.lines, ['ai'])

    def test_deleting_everything_leaves_an_empty_file(self):
        b = TextBuffer('a\nb\n')
        b.select_all()
        b.backspace()
        self.assertEqual(b.text(), '')
        b.undo()
        self.assertEqual(b.text(), 'a\nb\n')

    def test_text_typed_over_everything_keeps_the_trailing_newline(self):
        b = TextBuffer('abc\ndef\n')
        b.select_all()
        b.type_char('x')
        self.assertEqual(b.text(), 'x\n')
        b.backspace()
        self.assertEqual(b.text(), '')
        b.type_char('y')
        self.assertEqual(b.text(), 'y\n')

    def test_file_without_trailing_newline_stays_without_it(self):
        b = TextBuffer('abc')
        b.select_all()
        b.type_char('x')
        self.assertEqual(b.text(), 'x')

    def test_cutting_the_only_line_empties_the_file(self):
        b = TextBuffer('x\n')
        b.replace_lines(0, 1, [])
        self.assertEqual(b.text(), '')
        b.replace_lines(0, 1, ['y'])
        self.assertEqual(b.text(), 'y\n')

    def test_delete_word_left(self):
        b = buf_at('foo bar_baz\n', 0, 11)
        b.delete_word_left()
        self.assertEqual(b.lines, ['foo '])


class MoveTest(unittest.TestCase):
    def test_vertical_move_keeps_goal_column_across_short_line(self):
        b = buf_at('abcdef\nab\nabcdef\n', 0, 5)
        b.move('down')
        self.assertEqual(b.caret, (1, 2))
        b.move('down')
        self.assertEqual(b.caret, (2, 5))

    def test_vertical_move_counts_tabs_as_screen_cells(self):
        b = buf_at('\tx\n12345\n', 1, 4)
        b.move('up')
        self.assertEqual(b.caret, (0, 1))

    def test_smart_home_toggles_between_indent_and_line_start(self):
        b = buf_at('    code\n', 0, 7)
        b.move('home')
        self.assertEqual(b.col, 4)
        b.move('home')
        self.assertEqual(b.col, 0)

    def test_word_moves(self):
        b = buf_at('foo.bar baz\n', 0, 0)
        b.move('word_right')
        self.assertEqual(b.col, 3)
        b.move('word_right')
        self.assertEqual(b.col, 4)
        b.move('word_left')
        self.assertEqual(b.col, 3)
        b.move('word_left')
        self.assertEqual(b.col, 0)

    def test_document_start_and_end(self):
        b = buf_at('ab\ncd\nef\n', 1, 1)
        b.move('doc_end', extend=True)
        self.assertEqual(b.selection(), ((1, 1), (2, 2)))
        b.move('doc_start')
        self.assertEqual(b.caret, (0, 0))

    def test_arrow_collapses_selection_to_its_edge(self):
        b = TextBuffer('abcdef\n')
        b.select((0, 1), (0, 4))
        b.move('left')
        self.assertEqual(b.caret, (0, 1))
        self.assertIsNone(b.selection())

    def test_shift_move_extends_selection(self):
        b = buf_at('abc\ndef\n', 0, 1)
        b.move('down', extend=True)
        self.assertEqual(b.selection(), ((0, 1), (1, 1)))


class IndentTest(unittest.TestCase):
    def test_unit_follows_the_file(self):
        self.assertEqual(TextBuffer('\tx\n').unit, '\t')
        self.assertEqual(TextBuffer('  x\n').unit, '    ')

    def test_indent_and_outdent_selected_lines(self):
        b = TextBuffer('a\n\nb\nc\n')
        b.select((0, 0), (3, 0))
        b.indent()
        self.assertEqual(b.lines, ['    a', '', '    b', 'c'])
        b.outdent()
        self.assertEqual(b.lines, ['a', '', 'b', 'c'])


class UndoTest(unittest.TestCase):
    def test_typed_word_undone_at_once(self):
        b = buf_at('\n')
        for ch in 'foo bar':
            b.type_char(ch)
        b.undo()
        self.assertEqual(b.lines, ['foo '])
        b.undo()
        b.undo()
        self.assertEqual(b.lines, [''])
        self.assertFalse(b.modified)

    def test_redo_restores_and_new_edit_drops_redo(self):
        b = buf_at('\n')
        b.type_char('x')
        b.undo()
        self.assertTrue(b.redo())
        self.assertEqual(b.lines, ['x'])
        b.undo()
        b.type_char('y')
        self.assertFalse(b.redo())

    def test_move_splits_typing_groups(self):
        b = buf_at('\n')
        b.type_char('a')
        b.move('left')
        b.type_char('b')
        b.undo()
        self.assertEqual(b.lines, ['a'])

    def test_replace_lines_is_undoable(self):
        b = TextBuffer('a\nb\nc\n')
        b.replace_lines(1, 2, ['B1', 'B2'])
        self.assertEqual(b.lines, ['a', 'B1', 'B2', 'c'])
        b.undo()
        self.assertEqual(b.lines, ['a', 'b', 'c'])


class HelpersTest(unittest.TestCase):
    def test_decode_editable_refuses_what_would_be_corrupted(self):
        self.assertEqual(decode_editable('ёж\n'.encode()), 'ёж\n')
        self.assertIsNone(decode_editable(b'a\r\nb'))
        self.assertIsNone(decode_editable(b'\xff\xfe'))
        self.assertIsNone(decode_editable('a\u2028b'.encode()))

    def test_line_splice(self):
        self.assertEqual(line_splice(['a', 'b', 'c'], ['a', 'X', 'Y', 'c']), (1, 1, 2))
        self.assertEqual(line_splice(['a', 'b'], ['a', 'b']), (2, 0, 0))
        self.assertEqual(line_splice([], ['']), (0, 0, 1))
        self.assertEqual(line_splice(['a', 'a'], ['a']), (1, 1, 0))


if __name__ == '__main__':
    unittest.main()
