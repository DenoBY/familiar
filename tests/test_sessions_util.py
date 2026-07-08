import os
import unittest

import kittymock  # noqa: F401
import modules.session.util as U


class TestTruncate(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(U.truncate('hi', 5), 'hi')
        self.assertEqual(U.truncate('hello', 3), 'he…')
        self.assertEqual(U.truncate('abc', 1), '…')
        self.assertEqual(U.truncate('x', 0), '')


class TestHumanAge(unittest.TestCase):
    def test_just_now(self):
        self.assertEqual(U.human_age(0), 'just now')
        self.assertEqual(U.human_age(30), 'just now')

    def test_minutes(self):
        self.assertEqual(U.human_age(90), '1m ago')
        self.assertEqual(U.human_age(59 * 60), '59m ago')

    def test_hours(self):
        self.assertEqual(U.human_age(3600), '1h ago')
        self.assertEqual(U.human_age(5 * 3600), '5h ago')

    def test_days(self):
        self.assertEqual(U.human_age(90000), '1d ago')

    def test_months(self):
        self.assertEqual(U.human_age(5_000_000), '1mo ago')


class TestToLatin(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(U.to_latin('й'), 'q')
        self.assertEqual(U.to_latin('о'), 'j')
        self.assertEqual(U.to_latin('z'), 'z')


class TestShortPath(unittest.TestCase):
    def test_home(self):
        home = os.path.expanduser('~')
        self.assertEqual(U.short_path(home + '/a'), '~/a')
        self.assertEqual(U.short_path('/x'), '/x')


class TestWrapText(unittest.TestCase):
    def test_word_wrap(self):
        self.assertEqual(U.wrap_text('one two three four five', 8),
                         ['one two', 'three', 'four', 'five'])

    def test_hard_cut_long_token(self):
        self.assertEqual(U.wrap_text('supercalifragilistic word', 6),
                         ['superc', 'alifra', 'gilist', 'ic', 'word'])

    def test_empty(self):
        self.assertEqual(U.wrap_text('', 5), [''])

    def test_fits_on_one_line(self):
        self.assertEqual(U.wrap_text('a b c', 20), ['a b c'])

    def test_width_floor_one(self):
        # width<1 приводится к 1 — не должно зацикливаться/падать
        self.assertEqual(U.wrap_text('ab', 0), ['a', 'b'])


if __name__ == '__main__':
    unittest.main()
