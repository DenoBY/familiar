import unittest

import kittymock  # noqa: F401  (регистрирует мок kitty и путь к модулям кита)
import modules.keylayout as K
from kittymock import KeyEvent


class TestToLatin(unittest.TestCase):
    def test_ru_to_en(self):
        self.assertEqual(K.to_latin('й'), 'q')
        self.assertEqual(K.to_latin('ц'), 'w')
        self.assertEqual(K.to_latin('ф'), 'a')
        self.assertEqual(K.to_latin('о'), 'j')

    def test_uppercase(self):
        self.assertEqual(K.to_latin('Й'), 'Q')

    def test_passthrough(self):
        self.assertEqual(K.to_latin('a'), 'a')
        self.assertEqual(K.to_latin('1'), '1')
        self.assertEqual(K.to_latin('z'), 'z')

    def test_layout_covers_the_whole_home_row(self):
        # разошедшаяся раскладка ломает хоткеи молча — сверяем длины
        self.assertEqual(len(K._RU), len(K._EN))


class TestCtrlLetter(unittest.TestCase):
    def test_control_byte_becomes_letter(self):
        self.assertEqual(K.ctrl_letter('\x0f'), 'o')
        self.assertEqual(K.ctrl_letter('\x01'), 'a')
        self.assertEqual(K.ctrl_letter('\x1a'), 'z')

    def test_printable_text_is_not_a_hotkey(self):
        self.assertIsNone(K.ctrl_letter('o'))
        self.assertIsNone(K.ctrl_letter(''))

    def test_multichar_is_not_a_hotkey(self):
        self.assertIsNone(K.ctrl_letter('\x0f\x0f'))

    def test_paste_content_is_not_a_hotkey(self):
        # \n и \t внутри вставки — содержимое, а не ⌃j/⌃i
        self.assertIsNone(K.ctrl_letter('\x0f', in_bracketed_paste=True))
        self.assertIsNone(K.ctrl_letter('\n', in_bracketed_paste=True))


class TestChord(unittest.TestCase):
    def test_matches_modifier_and_letter(self):
        self.assertTrue(K.chord(KeyEvent(key='c', ctrl=True), 'ctrl', 'c'))

    def test_cyrillic_layout(self):
        # физическая клавиша o на ЙЦУКЕН даёт «щ»
        self.assertTrue(K.chord(KeyEvent(key='щ', ctrl=True), 'ctrl', 'o'))

    def test_extra_modifier_rejected(self):
        # ctrl+alt+c — не ctrl+c: лишний модификатор закрывал кит
        self.assertFalse(K.chord(KeyEvent(key='c', ctrl=True, alt=True),
                                 'ctrl', 'c'))
        self.assertFalse(K.chord(KeyEvent(key='c', ctrl=True, super=True),
                                 'ctrl', 'c'))

    def test_multi_modifier_spec(self):
        ev = KeyEvent(key='c', super=True, shift=True)
        self.assertTrue(K.chord(ev, 'super+shift', 'c'))
        self.assertFalse(K.chord(ev, 'super', 'c'))

    def test_wrong_letter(self):
        self.assertFalse(K.chord(KeyEvent(key='x', ctrl=True), 'ctrl', 'c'))

    def test_missing_key_is_not_a_match(self):
        self.assertFalse(K.chord(KeyEvent(key=None, ctrl=True), 'ctrl', 'c'))


if __name__ == '__main__':
    unittest.main()
