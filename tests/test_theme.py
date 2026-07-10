import os
import unittest

# Color берём из мока: он и зарегистрирован как
# kitty.fast_data_types.Color, а импорт kitty.* до kittymock невозможен.
from kittymock import Color
from modules import theme as T


class PaletteTests(unittest.TestCase):
    def test_every_theme_defines_every_role(self):
        roles = set(T.palette(T.DEFAULT))
        for name in T.NAMES:
            self.assertEqual(set(T.palette(name)), roles, name)

    def test_default_palette_is_256_color(self):
        for role, color in T.palette(T.DEFAULT).items():
            self.assertIsInstance(color, int, role)
            self.assertLessEqual(color, 255, role)

    def test_darcula_is_truecolor_from_the_jetbrains_scheme(self):
        p = T.palette('darcula')
        self.assertEqual(p['keyword'], Color(0xcc, 0x78, 0x32))    # DEFAULT_KEYWORD
        self.assertEqual(p['string'], Color(0x6a, 0x87, 0x59))     # DEFAULT_STRING
        self.assertEqual(p['number'], Color(0x68, 0x97, 0xbb))     # DEFAULT_NUMBER
        self.assertEqual(p['func'], Color(0xff, 0xc6, 0x6d))       # FUNCTION_DECLARATION

    def test_unknown_theme_falls_back_to_default(self):
        # опечатка в kitty.conf не должна ронять kitten
        self.assertEqual(T.palette('no-such-theme'), T.palette(T.DEFAULT))


class ThemeNameTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get('FAMILIAR_THEME')

    def tearDown(self):
        if self._saved is None:
            os.environ.pop('FAMILIAR_THEME', None)
        else:
            os.environ['FAMILIAR_THEME'] = self._saved

    def test_reads_env(self):
        os.environ['FAMILIAR_THEME'] = 'darcula'
        self.assertEqual(T.theme_name(), 'darcula')

    def test_case_and_spaces_ignored(self):
        os.environ['FAMILIAR_THEME'] = '  Darcula '
        self.assertEqual(T.theme_name(), 'darcula')

    def test_missing_or_empty_env_is_default(self):
        os.environ.pop('FAMILIAR_THEME', None)
        self.assertEqual(T.theme_name(), T.DEFAULT)
        os.environ['FAMILIAR_THEME'] = ''
        self.assertEqual(T.theme_name(), T.DEFAULT)


if __name__ == '__main__':
    unittest.main()
