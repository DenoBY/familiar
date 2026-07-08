import os
import unittest

import kittymock  # noqa: F401  (регистрирует мок kitty и путь к модулям кита)
import modules.vcs.util as U


class TestTruncate(unittest.TestCase):
    def test_shorter_or_equal_unchanged(self):
        self.assertEqual(U.truncate('hi', 5), 'hi')
        self.assertEqual(U.truncate('abcdef', 6), 'abcdef')

    def test_longer_gets_ellipsis(self):
        self.assertEqual(U.truncate('hello', 3), 'he…')
        self.assertEqual(U.truncate('abcdefg', 6), 'abcde…')

    def test_width_one_is_single_ellipsis(self):
        self.assertEqual(U.truncate('abc', 1), '…')

    def test_nonpositive_width_empty(self):
        self.assertEqual(U.truncate('x', 0), '')
        self.assertEqual(U.truncate('x', -3), '')

    def test_unicode(self):
        self.assertEqual(U.truncate('абвгд', 3), 'аб…')


class TestPad(unittest.TestCase):
    def test_pads_to_width(self):
        self.assertEqual(U.pad('hi', 5), 'hi   ')
        self.assertEqual(len(U.pad('hi', 5)), 5)

    def test_empty(self):
        self.assertEqual(U.pad('', 3), '   ')

    def test_too_long_truncated_no_overflow(self):
        r = U.pad('hello', 3)
        self.assertEqual(r, 'he…')
        self.assertEqual(len(r), 3)


class TestCompose(unittest.TestCase):
    def test_assembled_and_padded_to_width(self):
        segs = [('ab', {'fg': 'red'}), ('cd', {})]
        r = U.compose(segs, 10)
        self.assertEqual(len(r), 10)
        self.assertEqual(r, 'abcd      ')

    def test_narrow_truncates_across_segments(self):
        segs = [('ab', {'fg': 'red'}), ('cd', {}), ('ef', {'bold': True})]
        self.assertEqual(U.compose(segs, 3), 'ab…')

    def test_empty_segments_just_padding(self):
        self.assertEqual(U.compose([], 4), '    ')


class TestShortPath(unittest.TestCase):
    def test_home_collapsed(self):
        home = os.path.expanduser('~')
        self.assertEqual(U.short_path(home + '/proj/x'), '~/proj/x')

    def test_non_home_unchanged(self):
        self.assertEqual(U.short_path('/tmp/x'), '/tmp/x')


class TestToLatin(unittest.TestCase):
    def test_ru_to_en(self):
        self.assertEqual(U.to_latin('й'), 'q')
        self.assertEqual(U.to_latin('ц'), 'w')
        self.assertEqual(U.to_latin('ф'), 'a')

    def test_uppercase(self):
        self.assertEqual(U.to_latin('Й'), 'Q')

    def test_passthrough(self):
        self.assertEqual(U.to_latin('a'), 'a')
        self.assertEqual(U.to_latin('1'), '1')


class TestIsNoise(unittest.TestCase):
    def test_noise_dirs(self):
        self.assertTrue(U.is_noise('a/node_modules/b'))
        self.assertTrue(U.is_noise('.idea/workspace.xml'))
        self.assertTrue(U.is_noise('__pycache__'))

    def test_clean_paths(self):
        self.assertFalse(U.is_noise('src/main.py'))
        self.assertFalse(U.is_noise('a/b/c'))


class TestStatusStyle(unittest.TestCase):
    def test_known_statuses(self):
        self.assertEqual(U.STATUS_STYLE['added'], ('A', 'green'))
        self.assertEqual(U.STATUS_STYLE['deleted'], ('D', 'gray'))
        self.assertEqual(U.STATUS_STYLE['untracked'], ('?', 'red'))


if __name__ == '__main__':
    unittest.main()
