import unittest

import kittymock  # noqa: F401  (регистрирует мок kitty и путь к модулям кита)
import modules.vcs.util as U


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
        self.assertEqual(U.STATUS_STYLE['added'], 'green')
        self.assertEqual(U.STATUS_STYLE['deleted'], 'gray')
        self.assertEqual(U.STATUS_STYLE['untracked'], 'red')


if __name__ == '__main__':
    unittest.main()
