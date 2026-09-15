import unittest

import kittymock  # noqa: F401
from modules.lsp.signature import Signature, parse_signature


# ответы живых серверов (intelephense 1.x, pyright)
INTELEPHENSE = {
    'activeParameter': 1, 'activeSignature': 0,
    'signatures': [{'label': '(string $column, ?mixed $operator = null, ?mixed $value = null)',
                    'parameters': [{'label': [1, 15]}, {'label': [17, 40]},
                                   {'label': [42, 62]}]}]}
PYRIGHT = {
    'signatures': [{'label': '(name: str, times: int = 1) -> str',
                    'parameters': [{'label': [1, 10]}, {'label': [12, 26]}],
                    'activeParameter': 1}],
    'activeSignature': 0, 'activeParameter': 2}


class ParseSignatureTest(unittest.TestCase):
    def test_offsets(self):
        sig = parse_signature(INTELEPHENSE)
        self.assertEqual(sig.label[sig.active[0]:sig.active[1]], '?mixed $operator = null')

    def test_signature_level_active_parameter_wins(self):
        sig = parse_signature(PYRIGHT)
        self.assertEqual(sig.label[sig.active[0]:sig.active[1]], 'times: int = 1')

    def test_string_labels_found_in_order(self):
        sig = parse_signature({'signatures': [{
            'label': 'x(x, y)', 'parameters': [{'label': 'x'}, {'label': 'y'}]}],
            'activeParameter': 0})
        self.assertEqual(sig.active, (2, 3))

    def test_utf16_offsets(self):
        sig = parse_signature({'signatures': [{
            'label': '(🙂a, b)', 'parameters': [{'label': [1, 4]}, {'label': [6, 7]}]}],
            'activeParameter': 1})
        self.assertEqual(sig.label[sig.active[0]:sig.active[1]], 'b')

    def test_active_out_of_range_has_no_span(self):
        sig = parse_signature({'signatures': [{'label': 'f()', 'parameters': []}],
                               'activeParameter': 3})
        self.assertEqual(sig, Signature('f()', None))

    def test_empty_or_null(self):
        self.assertIsNone(parse_signature(None))
        self.assertIsNone(parse_signature({'signatures': []}))

    def test_bad_active_signature_falls_back_to_first(self):
        sig = parse_signature({'signatures': [{'label': 'f(a)'}], 'activeSignature': 7})
        self.assertEqual(sig.label, 'f(a)')


if __name__ == '__main__':
    unittest.main()
