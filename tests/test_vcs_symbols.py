import unittest

import kittymock  # noqa: F401  (регистрирует путь к модулям кита)
from modules.vcs.symbols import find_identifier, symbol_at, word_span


class SymbolUnderCursorTest(unittest.TestCase):
    def test_middle_of_word(self):
        self.assertEqual(symbol_at('foo_bar baz', 2), 'foo_bar')

    def test_start_of_word(self):
        self.assertEqual(symbol_at('foo_bar baz', 0), 'foo_bar')

    def test_end_boundary_belongs_left(self):
        # курсор впритык справа к слову
        self.assertEqual(symbol_at('foo baz', 3), 'foo')

    def test_second_word(self):
        self.assertEqual(symbol_at('foo baz', 5), 'baz')

    def test_on_whitespace_none(self):
        self.assertIsNone(symbol_at('foo   baz', 4))

    def test_on_isolated_punctuation_none(self):
        self.assertIsNone(symbol_at('a . b', 2))

    def test_paren_after_name_picks_callee(self):
        # клик по '(' сразу за именем → само имя
        self.assertEqual(symbol_at('foo(x)', 3), 'foo')

    def test_dotted_call_picks_attr(self):
        self.assertEqual(symbol_at('self.render_diff(x)', 7), 'render_diff')

    def test_php_arrow_picks_method(self):
        self.assertEqual(symbol_at('$this->belongsTo($id);', 8), 'belongsTo')

    def test_php_double_colon(self):
        self.assertEqual(symbol_at('Order::create($d)', 8), 'create')

    def test_negative_col(self):
        self.assertIsNone(symbol_at('foo', -1))

    def test_leading_underscore_and_digits(self):
        self.assertEqual(symbol_at('_x1 = 2', 1), '_x1')

    def test_col_past_end(self):
        self.assertIsNone(symbol_at('foo', 99))


class WordSpanTest(unittest.TestCase):
    def test_span_of_word(self):
        self.assertEqual(word_span('ab cde f', 4), (3, 6))

    def test_none_on_space(self):
        self.assertIsNone(word_span('a  b', 2))

    def test_cyrillic_word(self):
        # выделение слова в комментарии — кириллица, не только ASCII
        self.assertEqual(word_span('# код тут', 3), (2, 5))


class FindIdentifierTest(unittest.TestCase):
    def test_finds_line_and_column(self):
        text = 'a = 1\nresult = helper(2)\n'
        self.assertEqual(find_identifier(text, 'helper'), (2, 9))

    def test_whole_word_only(self):
        # helper_extra не считается вхождением helper
        text = 'helper_extra = 1\nx = helper()\n'
        self.assertEqual(find_identifier(text, 'helper'), (2, 4))

    def test_absent_gives_none(self):
        self.assertIsNone(find_identifier('a = 1\n', 'helper'))

    def test_empty_name_gives_none(self):
        self.assertIsNone(find_identifier('a = 1\n', ''))

    def test_regex_chars_are_literal(self):
        self.assertIsNone(find_identifier('abc\n', 'a.c'))


if __name__ == '__main__':
    unittest.main()
