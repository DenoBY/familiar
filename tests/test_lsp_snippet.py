import unittest

import kittymock  # noqa: F401
from modules.lsp.snippet import Stop, expand


class ExpandTest(unittest.TestCase):
    def test_plain_text_passes_through(self):
        self.assertEqual(expand('where'), ('where', []))

    def test_final_tabstop(self):
        self.assertEqual(expand('where($0)'), ('where()', [Stop(0, 6, 6)]))

    def test_placeholders_in_order_final_last(self):
        text, stops = expand('foo(${2:b}, ${1:a})$0')
        self.assertEqual(text, 'foo(b, a)')
        self.assertEqual(stops, [Stop(1, 7, 8), Stop(2, 4, 5), Stop(0, 9, 9)])

    def test_nested_placeholder(self):
        text, stops = expand('${1:a ${2:b}}')
        self.assertEqual(text, 'a b')
        self.assertEqual(stops, [Stop(1, 0, 3), Stop(2, 2, 3)])

    def test_choice_takes_first_option(self):
        self.assertEqual(expand('${1|one,two\\,x|}'), ('one', [Stop(1, 0, 3)]))

    def test_mirror_keeps_first_occurrence(self):
        text, stops = expand('${1:x} = $1')
        self.assertEqual(text, 'x = ')
        self.assertEqual(stops, [Stop(1, 0, 1)])

    def test_escapes(self):
        self.assertEqual(expand('a\\$b \\} \\\\ \\n')[0], 'a$b } \\ \\n')

    def test_unknown_variable_stays_literal_for_php(self):
        self.assertEqual(expand('$this->${1:name}')[0], '$this->name')

    def test_known_variable(self):
        text, _ = expand('${TM_FILENAME} ${TM_LINE_NUMBER:1} $TM_FILENAME_BASE',
                         {'TM_FILENAME': 'a.py', 'TM_FILENAME_BASE': 'a'})
        self.assertEqual(text, 'a.py 1 a')

    def test_variable_transform_without_value(self):
        self.assertEqual(expand('${TM_FILENAME/(.*)/${1:/upcase}/}x')[0], 'x')

    def test_intelephense_method_stub(self):
        raw = "__construct($1)\n{\n\t${2:throw new \\\\Exception('Not implemented');}$0\n\\}"
        text, stops = expand(raw)
        self.assertEqual(text, "__construct()\n{\n\tthrow new \\Exception('Not implemented');\n}")
        self.assertEqual([s.index for s in stops], [1, 2, 0])
        self.assertEqual(text[stops[1].start:stops[1].end],
                         "throw new \\Exception('Not implemented');")

    def test_broken_snippet_falls_back_to_text(self):
        self.assertEqual(expand('foo(${1:a'), ('foo(${1:a', []))
        self.assertEqual(expand('x \\$ ${'), ('x $ ${', []))

    def test_lone_dollar(self):
        self.assertEqual(expand('cost: $ 5'), ('cost: $ 5', []))


if __name__ == '__main__':
    unittest.main()
