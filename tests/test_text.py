import os
import unittest

import kittymock  # noqa: F401  (регистрирует мок kitty и путь к модулям кита)
import modules.text as T


class TestTruncate(unittest.TestCase):
    def test_shorter_or_equal_unchanged(self):
        self.assertEqual(T.truncate('hi', 5), 'hi')
        self.assertEqual(T.truncate('abcdef', 6), 'abcdef')

    def test_longer_gets_ellipsis(self):
        self.assertEqual(T.truncate('hello', 3), 'he…')
        self.assertEqual(T.truncate('abcdefg', 6), 'abcde…')

    def test_width_one_is_single_ellipsis(self):
        self.assertEqual(T.truncate('abc', 1), '…')

    def test_nonpositive_width_empty(self):
        self.assertEqual(T.truncate('x', 0), '')
        self.assertEqual(T.truncate('x', -3), '')

    def test_unicode(self):
        self.assertEqual(T.truncate('абвгд', 3), 'аб…')


class TestPad(unittest.TestCase):
    def test_pads_to_width(self):
        self.assertEqual(T.pad('hi', 5), 'hi   ')
        self.assertEqual(len(T.pad('hi', 5)), 5)

    def test_empty(self):
        self.assertEqual(T.pad('', 3), '   ')

    def test_too_long_truncated_no_overflow(self):
        r = T.pad('hello', 3)
        self.assertEqual(r, 'he…')
        self.assertEqual(len(r), 3)


class TestShortPath(unittest.TestCase):
    def test_home_collapsed(self):
        home = os.path.expanduser('~')
        self.assertEqual(T.short_path(home + '/proj/x'), '~/proj/x')

    def test_non_home_unchanged(self):
        self.assertEqual(T.short_path('/tmp/x'), '/tmp/x')


class TestPlural(unittest.TestCase):
    def test_singular_and_plural(self):
        self.assertEqual(T.plural(1, 'line'), '1 line')
        self.assertEqual(T.plural(2, 'line'), '2 lines')
        self.assertEqual(T.plural(0, 'line'), '0 lines')

    def test_irregular_form(self):
        self.assertEqual(T.plural(2, 'match', 'matches'), '2 matches')
        self.assertEqual(T.plural(1, 'match', 'matches'), '1 match')


class TestWrapText(unittest.TestCase):
    def test_word_wrap(self):
        self.assertEqual(T.wrap_text('one two three four five', 8),
                         ['one two', 'three', 'four', 'five'])

    def test_hard_cut_long_token(self):
        self.assertEqual(T.wrap_text('supercalifragilistic word', 6),
                         ['superc', 'alifra', 'gilist', 'ic', 'word'])

    def test_empty(self):
        self.assertEqual(T.wrap_text('', 5), [''])

    def test_fits_on_one_line(self):
        self.assertEqual(T.wrap_text('a b c', 20), ['a b c'])

    def test_width_floor_one(self):
        # width<1 приводится к 1 — не должно зацикливаться/падать
        self.assertEqual(T.wrap_text('ab', 0), ['a', 'b'])


class TestWrapWords(unittest.TestCase):
    """Движок переноса над произвольными токенами: им пользуются и
    wrap_text, и разметка markdown, и раскладка строки ввода.
    """

    @staticmethod
    def _space(prev, nxt):
        return ' '

    def test_keeps_token_identity(self):
        words = [[('a', 0), ('b', 1)], [('c', 2)]]
        self.assertEqual(T.wrap_words(words, 10, lambda p, n: (' ', -1)),
                         [[('a', 0), ('b', 1), (' ', -1), ('c', 2)]])

    def test_breaks_between_words(self):
        words = [list('one'), list('two')]
        self.assertEqual(T.wrap_words(words, 3, self._space),
                         [list('one'), list('two')])


if __name__ == '__main__':
    unittest.main()
