import unittest

import kittymock  # noqa: F401
import modules.session.markdown as M


W = 40


def plains(text, width=W):
    return [p for p, _ in M.markdown_lines(text, width)]


def render(text, width=W):
    return [r for _, r in M.markdown_lines(text, width)]


class TestInline(unittest.TestCase):
    def test_markers_are_stripped(self):
        self.assertEqual(plains('**жирный** и `код` и *курсив*'),
                         ['жирный и код и курсив'])

    def test_styles_are_recorded(self):
        chars = M._styled_chars('a **b** `c`')
        styles = {s for _, s in chars if s}
        self.assertEqual(styles, {'bold', 'code'})

    def test_asterisk_inside_word_is_not_italic(self):
        self.assertEqual(plains('2*3*4'), ['2*3*4'])

    def test_bold_wrapped_across_source_lines(self):
        self.assertEqual(plains('корень **той копии,\nкоторую запустили**'),
                         ['корень той копии, которую запустили'])

    def test_plain_line_has_no_render(self):
        self.assertEqual(render('обычный текст'), [None])

    def test_styled_line_has_render(self):
        self.assertIsNotNone(render('**жирный**')[0])

    def test_space_inside_bold_keeps_style(self):
        chars = M._wrap_chars(M._styled_chars('**a b**'), W)[0]
        self.assertEqual([s for _, s in chars], ['bold'] * 3)


class TestBlocks(unittest.TestCase):
    def test_heading_loses_hashes(self):
        p, r = M.markdown_lines('## Что дальше', W)[0]
        self.assertEqual(p, 'Что дальше')
        self.assertIsNotNone(r)

    def test_heading_strips_inline_markers(self):
        self.assertEqual(plains('## `familiar status`: как быть'),
                         ['familiar status: как быть'])

    def test_bullet_becomes_dot(self):
        self.assertEqual(plains('- пункт'), ['• пункт'])

    def test_ordered_list_keeps_number(self):
        self.assertEqual(plains('1. первый'), ['1. первый'])

    def test_blank_lines_kept(self):
        self.assertEqual(plains('a\n\nb'), ['a', '', 'b'])

    def test_fenced_code_is_indented_and_fences_dropped(self):
        out = plains('```python\nx = 1\n```')
        self.assertEqual(out, ['  x = 1'])

    def test_fenced_code_is_highlighted(self):
        r = render('```python\ndef f():\n```')[0]
        self.assertIsNotNone(r)

    def test_unknown_language_still_renders(self):
        self.assertEqual(plains('```\nplain\n```'), ['  plain'])

    def test_markdown_inside_fence_is_literal(self):
        self.assertEqual(plains('```\n**not bold**\n```'), ['  **not bold**'])


class TestWrapping(unittest.TestCase):
    def test_lines_fit_width(self):
        text = 'слово ' * 40
        self.assertTrue(all(len(p) <= 20 for p in plains(text, 20)))

    def test_long_word_is_hard_split(self):
        self.assertTrue(all(len(p) <= 12 for p in plains('x' * 50, 12)))

    def test_bullet_continuation_is_indented(self):
        out = plains('- ' + 'слово ' * 10, 20)
        self.assertTrue(out[0].startswith('• '))
        self.assertTrue(all(p.startswith('  ') for p in out[1:]))


TABLE = ('| a | b |\n'
         '|---|---:|\n'
         '| 1 | 2 |')


class TestTable(unittest.TestCase):
    def test_framed_and_exact_width(self):
        out = plains(TABLE, 40)
        self.assertTrue(out[0].startswith('┌') and out[0].endswith('┐'))
        self.assertTrue(out[2].startswith('├'))
        self.assertTrue(out[-1].startswith('└') and out[-1].endswith('┘'))
        self.assertTrue(all(len(p) == 40 for p in out))

    def test_header_centered_body_aligned(self):
        out = plains(TABLE, 40)
        head, body = out[1], out[3]
        self.assertGreater(head.index('a'), body.index('1'))    # заголовок по центру
        self.assertGreater(body.rindex('2'), head.rindex('b'))  # колонка b — вправо

    def test_rule_between_every_body_row(self):
        md = '| a |\n|---|\n| 1 |\n| 2 |'
        out = plains(md, 20)
        self.assertEqual(sum(1 for p in out if p.startswith('├')), 2)

    def test_cells_wrap_instead_of_cutting(self):
        md = '| a | b |\n|---|---|\n| ' + 'слово ' * 6 + '| x |'
        out = plains(md, 40)
        self.assertTrue(all(len(p) == 40 for p in out))
        self.assertGreater(len(out), 5)          # ячейка развернулась в строки
        self.assertNotIn('…', '\n'.join(out))   # ничего не срезано

    def test_inline_markup_inside_cell(self):
        rows = M.markdown_lines('| a |\n|---|\n| **ж** |', 40)
        self.assertIsNotNone(rows[3][1])          # render со стилем есть
        self.assertNotIn('**', rows[3][0])

    def test_too_narrow_falls_back_to_paragraph(self):
        out = plains(TABLE, 10)
        self.assertFalse(any(p.startswith('┌') for p in out))

    def test_pipes_without_separator_are_prose(self):
        out = plains('| a | b |', 40)
        self.assertFalse(any(p.startswith('┌') for p in out))


if __name__ == '__main__':
    unittest.main()
