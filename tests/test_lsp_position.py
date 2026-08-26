import unittest

import kittymock  # noqa: F401
from modules.lsp.position import (
    Target,
    collapse_overloads,
    encode_character,
    location_target,
    locations,
    path_from_uri,
    prefer_sources,
    rank_symbols,
    raw_index,
    rel_or_abs,
    uri_from_path,
)


def _preview(rel: str, line: int) -> str:
    return f'{rel}#{line}'


def _loc(path: str, line: int) -> dict:
    return {'uri': f'file://{path}',
            'range': {'start': {'line': line, 'character': 0}}}


def _sym(name: str, path: str, line: int, kind: int) -> dict:
    return {'name': name, 'kind': kind, 'location': _loc(path, line)}


class RawIndexTest(unittest.TestCase):
    def test_no_tabs_is_identity(self):
        self.assertEqual(raw_index('return $x;', 7), 7)

    def test_leading_tab_counts_as_four(self):
        self.assertEqual(raw_index('\tfoo', 4), 1)

    def test_click_inside_tab_gives_tab_itself(self):
        self.assertEqual(raw_index('\tfoo', 2), 0)

    def test_two_tabs(self):
        self.assertEqual(raw_index('\t\tbar', 8), 2)

    def test_tab_in_the_middle(self):
        self.assertEqual(raw_index('a\tb', 5), 2)

    def test_zero_and_negative(self):
        self.assertEqual(raw_index('\tx', 0), 0)
        self.assertEqual(raw_index('\tx', -3), 0)

    def test_past_end_clamps_to_length(self):
        self.assertEqual(raw_index('abc', 99), 3)


class EncodeCharacterTest(unittest.TestCase):
    def test_ascii_matches_index(self):
        self.assertEqual(encode_character('return x', 7), 7)

    def test_cyrillic_is_one_utf16_unit(self):
        # каждая буква — один code unit, поэтому счёт не сдвигается
        self.assertEqual(encode_character('# привет x', 9), 9)

    def test_astral_char_is_two_utf16_units(self):
        self.assertEqual(encode_character('🙂x', 1), 2)
        self.assertEqual(encode_character('🙂x', 2), 3)

    def test_utf8_counts_bytes(self):
        self.assertEqual(encode_character('привет', 3, 'utf-8'), 6)

    def test_utf32_counts_code_points(self):
        self.assertEqual(encode_character('🙂x', 2, 'utf-32'), 2)


class UriTest(unittest.TestCase):
    def test_roundtrip(self):
        self.assertEqual(path_from_uri(uri_from_path('/tmp/a b/c.php')),
                         '/tmp/a b/c.php')

    def test_percent_decoding(self):
        self.assertEqual(path_from_uri('file:///a/b%20c.php'), '/a/b c.php')

    def test_non_file_scheme_is_empty(self):
        self.assertEqual(path_from_uri('git:/a/b.php?ref=HEAD'), '')
        self.assertEqual(path_from_uri('untitled:1'), '')

    def test_rel_inside_root(self):
        self.assertEqual(rel_or_abs('/repo/app/X.php', '/repo'), 'app/X.php')

    def test_abs_outside_root(self):
        self.assertEqual(rel_or_abs('/usr/lib/x.php', '/repo'), '/usr/lib/x.php')

    def test_sibling_prefix_is_not_inside(self):
        self.assertEqual(rel_or_abs('/repo2/x.php', '/repo'), '/repo2/x.php')


class LocationTest(unittest.TestCase):
    def test_location_to_target(self):
        target = location_target(_loc('/repo/a.php', 9), '/repo', _preview)
        self.assertEqual(target, Target('a.php', 10, 'def', 'a.php#10'))

    def test_location_link_is_understood(self):
        link = {'targetUri': 'file:///repo/b.php',
                'targetSelectionRange': {'start': {'line': 2, 'character': 4}}}
        self.assertEqual(location_target(link, '/repo', _preview).line, 3)

    def test_unknown_scheme_gives_none(self):
        self.assertIsNone(location_target({'uri': 'git:/x'}, '/repo', _preview))

    def test_locations_accepts_single_list_or_null(self):
        self.assertEqual(len(locations(_loc('/a', 0))), 1)
        self.assertEqual(len(locations([_loc('/a', 0), _loc('/b', 1)])), 2)
        self.assertEqual(locations(None), [])


class CollapseOverloadsTest(unittest.TestCase):
    def test_neighbours_in_one_file_collapse(self):
        # getattr в typeshed объявлен шестью @overload подряд
        raw = [Target('builtins.pyi', line, 'def', '') for line in
               (1779, 1785, 1787, 1789, 1791, 1793)]
        self.assertEqual([t.line for t in collapse_overloads(raw)], [1779])

    def test_distant_definitions_in_one_file_are_kept(self):
        raw = [Target('a.py', 10, 'def', ''), Target('a.py', 900, 'def', '')]
        self.assertEqual(len(collapse_overloads(raw)), 2)

    def test_other_files_are_kept(self):
        raw = [Target('a.py', 10, 'def', ''), Target('b.py', 12, 'def', '')]
        self.assertEqual(len(collapse_overloads(raw)), 2)

    def test_empty(self):
        self.assertEqual(collapse_overloads([]), [])


class PreferSourcesTest(unittest.TestCase):
    def test_stub_dropped_when_source_is_there(self):
        # pyright на quote отдаёт и parse.pyi, и parse.py
        raw = [Target('/stubs/stdlib/urllib/parse.pyi', 177, 'def', ''),
               Target('/py3.9/urllib/parse.py', 819, 'def', '')]
        got = prefer_sources(raw)
        self.assertEqual([t.path for t in got], ['/py3.9/urllib/parse.py'])

    def test_stub_kept_when_alone(self):
        # у встроенных функций исходника нет вовсе
        raw = [Target('/stubs/builtins.pyi', 1779, 'def', '')]
        self.assertEqual(prefer_sources(raw), raw)

    def test_typescript_declarations_too(self):
        raw = [Target('/types/lib.d.ts', 5, 'def', ''),
               Target('/src/lib.ts', 12, 'def', '')]
        self.assertEqual([t.path for t in prefer_sources(raw)], ['/src/lib.ts'])

    def test_unrelated_stub_survives(self):
        raw = [Target('/stubs/other.pyi', 5, 'def', ''),
               Target('/src/parse.py', 12, 'def', '')]
        self.assertEqual(len(prefer_sources(raw)), 2)


class RankSymbolsTest(unittest.TestCase):
    def test_exact_name_wins(self):
        raw = [_sym('shopExtra', '/repo/a.php', 1, 12),
               _sym('shop', '/repo/b.php', 1, 12)]
        got = rank_symbols(raw, 'shop', None, '/repo', _preview)
        self.assertEqual(got[0].path, 'b.php')

    def test_inexact_matches_are_dropped_entirely(self):
        # иначе рядом с классом в пикер приезжают стабы сервера, и
        # единственное настоящее определение приходится выбирать руками
        raw = [_sym('shop', '/repo/b.php', 1, 12),
               _sym('shopExtra', '/repo/a.php', 1, 12),
               _sym('Exception', '/stubs/Core_c.php', 300, 5)]
        got = rank_symbols(raw, 'shop', None, '/repo', _preview)
        self.assertEqual([t.path for t in got], ['b.php'])

    def test_inexact_kept_when_nothing_matches_exactly(self):
        raw = [_sym('shopExtra', '/repo/a.php', 1, 12)]
        got = rank_symbols(raw, 'shop', None, '/repo', _preview)
        self.assertEqual(len(got), 1)

    def test_declaration_beats_variable(self):
        raw = [_sym('shop', '/repo/a.php', 1, 13),      # переменная
               _sym('shop', '/repo/b.php', 1, 6)]       # метод
        got = rank_symbols(raw, 'shop', None, '/repo', _preview)
        self.assertEqual(got[0].kind, 'method')

    def test_current_file_beats_stranger(self):
        raw = [_sym('shop', '/repo/other.php', 1, 12),
               _sym('shop', '/repo/cur.php', 1, 12)]
        got = rank_symbols(raw, 'shop', 'cur.php', '/repo', _preview)
        self.assertEqual(got[0].path, 'cur.php')

    def test_duplicates_collapse(self):
        raw = [_sym('shop', '/repo/a.php', 3, 12)] * 3
        self.assertEqual(len(rank_symbols(raw, 'shop', None, '/repo', _preview)), 1)

    def test_garbage_entries_skipped(self):
        raw = [None, {'name': 'x'}, _sym('shop', '/repo/a.php', 1, 12)]
        self.assertEqual(len(rank_symbols(raw, 'shop', None, '/repo', _preview)), 1)


if __name__ == '__main__':
    unittest.main()
