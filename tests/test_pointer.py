import unittest

import kittymock  # noqa: F401
from modules.pointer import pop_pointer, push_pointer


class PointerShapeTest(unittest.TestCase):
    def test_push_puts_shape_on_the_stack(self):
        # OSC 22, '>' — push; терминатор ST (ESC \)
        self.assertEqual(push_pointer('text'), '\x1b]22;>text\x1b\\')
        self.assertEqual(push_pointer('pointer'), '\x1b]22;>pointer\x1b\\')

    def test_pop_restores_previous_shape(self):
        self.assertEqual(pop_pointer(), '\x1b]22;<\x1b\\')


if __name__ == '__main__':
    unittest.main()
