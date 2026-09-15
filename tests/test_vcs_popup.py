import unittest

import kittymock  # noqa: F401
from modules.vcs.popup import (
    Box,
    MenuRow,
    Pane,
    doc_content,
    menu_lines,
    place_doc,
    place_menu,
    signature_line,
)


PANE = Pane(top=2, bottom=30, left=50, right=118)


class PlaceMenuTest(unittest.TestCase):
    def test_below_the_word_with_labels_under_it(self):
        box, above = place_menu(10, 70, 5, (12, 6), PANE, None)
        self.assertFalse(above)
        self.assertEqual((box.row, box.col, box.height), (11, 66, 5))

    def test_flips_above_when_there_is_more_room(self):
        box, above = place_menu(27, 70, 8, (12, 0), PANE, None)
        self.assertTrue(above)
        self.assertEqual(box.row + box.height, 27)

    def test_side_kept_even_if_the_other_fits_better(self):
        box, above = place_menu(27, 70, 2, (12, 0), PANE, True)
        self.assertTrue(above)

    def test_clamped_to_the_pane(self):
        box, _ = place_menu(10, 115, 3, (30, 10), PANE, None)
        self.assertEqual(box.col + box.width, PANE.right)
        box, _ = place_menu(10, 51, 3, (10, 0), PANE, None)
        self.assertEqual(box.col, PANE.left)

    def test_no_room_at_all(self):
        self.assertIsNone(place_menu(2, 70, 3, (10, 0), Pane(2, 3, 50, 118), None))


class MenuLinesTest(unittest.TestCase):
    def test_description_gives_way_to_the_label(self):
        rows = [MenuRow('m', 'where', '($column)', 'App\\Models\\Builder', (0,), False)]
        line = menu_lines(rows, Box(0, 0, 22, 1), (14, 18), 0, 0, 1)[0]
        self.assertIn('where($column)', line)
        self.assertNotIn('Builder', line)

    def test_scroll_thumb_only_when_list_is_longer(self):
        rows = [MenuRow('v', f'x{i}', '', '', (), False) for i in range(3)]
        self.assertNotIn('▐', ''.join(menu_lines(rows, Box(0, 0, 10, 3), (3, 0), 0, 0, 3)))
        self.assertIn('▐', ''.join(menu_lines(rows, Box(0, 0, 10, 3), (3, 0), 0, 0, 30)))

    def test_deprecated_is_struck_through(self):
        rows = [MenuRow('m', 'old', '', '', (), True)]
        self.assertIn('\x1b[9m', menu_lines(rows, Box(0, 0, 10, 1), (3, 0), 0, 0, 1)[0])


class DocTest(unittest.TestCase):
    def test_markdown_is_flattened(self):
        doc = ('__App\\\\Models\\\\User::where__\n\nAdd a `where` clause.\n\n'
               '```php\n<?php\npublic function where()\n```\n---\n_@return_ `static`')
        self.assertEqual(doc_content('', doc), [
            ('App\\Models\\User::where', False), ('', False), ('Add a where clause.', False),
            ('', False), ('public function where()', True), ('', False),
            ('@return static', False)])

    def test_detail_goes_first_as_code(self):
        self.assertEqual(doc_content('def f()', ''), [('def f()', True)])

    def test_beside_the_menu_right_then_left(self):
        content = [('x' * 40, False)]
        menu = Box(5, 60, 20, 4)
        self.assertEqual(place_doc(menu, PANE, content).col, 80)
        menu = Box(5, 96, 20, 4)
        self.assertEqual(place_doc(menu, PANE, content).col + 42, 96)
        self.assertIsNone(place_doc(Box(5, 55, 50, 4), PANE, content))


class SignatureLineTest(unittest.TestCase):
    def test_active_parameter_stays_visible_when_cut(self):
        label = '(first_parameter, second_parameter, x: int = 42)'
        start = label.index('x:')
        line = signature_line(label, (start, len(label) - 1), 20)
        self.assertIn('x: int = 42', line)
        self.assertIn('…', line)


if __name__ == '__main__':
    unittest.main()
