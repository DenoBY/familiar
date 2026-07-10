import re
import unittest

import kittymock  # noqa: F401  (регистрирует путь к модулям кита)
import modules.vcs.navdef as N


class TestExtractSymbol(unittest.TestCase):
    def test_middle_of_word(self):
        self.assertEqual(N.extract_symbol('foo_bar baz', 2), 'foo_bar')

    def test_start_of_word(self):
        self.assertEqual(N.extract_symbol('foo_bar baz', 0), 'foo_bar')

    def test_end_boundary_belongs_left(self):
        # курсор впритык справа к слову
        self.assertEqual(N.extract_symbol('foo baz', 3), 'foo')

    def test_second_word(self):
        self.assertEqual(N.extract_symbol('foo baz', 5), 'baz')

    def test_on_whitespace_none(self):
        self.assertIsNone(N.extract_symbol('foo   baz', 4))

    def test_on_isolated_punctuation_none(self):
        # знак, не примыкающий к слову справа-слева — не идентификатор
        self.assertIsNone(N.extract_symbol('a . b', 2))  # '.'

    def test_paren_after_name_picks_callee(self):
        # клик по '(' сразу за именем → само имя (переход к вызову)
        self.assertEqual(N.extract_symbol('foo(x)', 3), 'foo')

    def test_dotted_call_picks_attr(self):
        s = 'self.render_diff(x)'
        self.assertEqual(N.extract_symbol(s, 7), 'render_diff')

    def test_negative_col(self):
        self.assertIsNone(N.extract_symbol('foo', -1))

    def test_leading_underscore_and_digits(self):
        self.assertEqual(N.extract_symbol('_x1 = 2', 1), '_x1')

    def test_col_past_end(self):
        self.assertIsNone(N.extract_symbol('foo', 99))


def _matches_any(patterns, line):
    return any(re.search(p, line) for p in patterns)


class TestDefPatterns(unittest.TestCase):
    def test_python_def_and_class(self):
        pats = N.def_patterns('.py', 'load_diff')
        self.assertTrue(_matches_any(pats, '    def load_diff(self) -> None:'))
        self.assertTrue(_matches_any(N.def_patterns('.py', 'Foo'), 'class Foo(Base):'))

    def test_python_ignores_call(self):
        pats = N.def_patterns('.py', 'load_diff')
        self.assertFalse(_matches_any(pats, '        self.other(load_diff)'))

    def test_python_assignment(self):
        pats = N.def_patterns('.py', 'CONFIG')
        self.assertTrue(_matches_any(pats, 'CONFIG = {}'))

    def test_word_boundary_no_substring(self):
        # 'load' не должен цепляться к 'load_diff'
        pats = N.def_patterns('.py', 'load')
        self.assertFalse(_matches_any(pats, '    def load_diff(self):'))

    def test_js_function_and_const_arrow(self):
        pats = N.def_patterns('.ts', 'handler')
        self.assertTrue(_matches_any(pats, 'function handler(ev) {'))
        self.assertTrue(_matches_any(pats, 'const handler = (ev) => {'))

    def test_php_function_and_class(self):
        pats = N.def_patterns('.php', 'render')
        self.assertTrue(_matches_any(pats, '    public function render()'))
        self.assertTrue(_matches_any(N.def_patterns('.php', 'View'), 'class View extends Base'))

    def test_go_func_with_receiver(self):
        pats = N.def_patterns('.go', 'Render')
        self.assertTrue(_matches_any(pats, 'func (v *View) Render() error {'))
        self.assertTrue(_matches_any(pats, 'func Render() {'))

    def test_unknown_ext_uses_generic(self):
        pats = N.def_patterns('.zig', 'thing')
        self.assertTrue(_matches_any(pats, 'fn thing() void {'))
        self.assertTrue(_matches_any(pats, 'struct thing {'))


class TestRankCandidates(unittest.TestCase):
    def test_def_before_assign(self):
        raw = [
            ('a/x.py', 10, 'foo = 1'),
            ('a/x.py', 3, 'def foo():'),
        ]
        out = N.rank_candidates(raw, None, 'foo')
        self.assertEqual((out[0].line, out[0].kind), (3, 'def'))
        self.assertEqual(out[1].kind, 'assign')

    def test_current_file_first(self):
        raw = [
            ('other/y.py', 5, 'def foo():'),
            ('cur/x.py', 20, 'def foo():'),
        ]
        out = N.rank_candidates(raw, 'cur/x.py', 'foo')
        self.assertEqual(out[0].path, 'cur/x.py')

    def test_shallower_path_first(self):
        raw = [
            ('a/b/c/deep.py', 1, 'def foo():'),
            ('top.py', 1, 'def foo():'),
        ]
        out = N.rank_candidates(raw, None, 'foo')
        self.assertEqual(out[0].path, 'top.py')

    def test_dedup_same_path_line(self):
        raw = [
            ('x.py', 7, 'def foo():'),
            ('x.py', 7, 'def foo():'),
        ]
        self.assertEqual(len(N.rank_candidates(raw, None, 'foo')), 1)

    def test_preview_stripped(self):
        out = N.rank_candidates([('x.py', 1, '   def foo():  ')], None, 'foo')
        self.assertEqual(out[0].preview, 'def foo():')


class TestWordSpan(unittest.TestCase):
    def test_span_of_word(self):
        self.assertEqual(N.word_span('ab cde f', 4), (3, 6))

    def test_none_on_space(self):
        self.assertIsNone(N.word_span('a  b', 2))

    def test_cyrillic_word(self):
        # выделение слова в комментарии — кириллица, не только ASCII
        self.assertEqual(N.word_span('# код тут', 3), (2, 5))


class TestSymbolAt(unittest.TestCase):
    def test_plain_word(self):
        self.assertEqual(N.symbol_at('foo bar', 1), ('foo', False, False, None))

    def test_attr_with_qualifier(self):
        # клик по render в self.render(x)
        self.assertEqual(N.symbol_at('self.render(x)', 6),
                         ('render', True, True, 'self'))

    def test_call_no_attr(self):
        self.assertEqual(N.symbol_at('helper()', 2), ('helper', False, True, None))

    def test_attr_not_call(self):
        self.assertEqual(N.symbol_at('obj.name = 1', 5), ('name', True, False, 'obj'))

    def test_none_on_space(self):
        self.assertIsNone(N.symbol_at('a  b', 2))


class TestClassifyRankContext(unittest.TestCase):
    def test_indented_def_is_method(self):
        out = N.rank_candidates([('x.py', 3, '    def foo(self):')], None, 'foo')
        self.assertEqual(out[0].kind, 'method')

    def test_toplevel_def_is_def(self):
        out = N.rank_candidates([('x.py', 3, 'def foo():')], None, 'foo')
        self.assertEqual(out[0].kind, 'def')

    def test_is_attr_prefers_method(self):
        raw = [('a.py', 1, 'def foo():'), ('b.py', 1, '    def foo(self):')]
        out = N.rank_candidates(raw, None, 'foo', is_attr=True)
        self.assertEqual(out[0].kind, 'method')

    def test_no_attr_prefers_free_def(self):
        raw = [('a.py', 1, 'def foo():'), ('b.py', 1, '    def foo(self):')]
        out = N.rank_candidates(raw, None, 'foo', is_attr=False)
        self.assertEqual(out[0].kind, 'def')

    def test_is_call_sinks_assignment(self):
        raw = [('a.py', 1, 'foo = 1'), ('b.py', 9, 'def foo():')]
        out = N.rank_candidates(raw, None, 'foo', is_call=True)
        self.assertEqual(out[0].kind, 'def')
        self.assertEqual(out[-1].kind, 'assign')


class TestImportParsers(unittest.TestCase):
    def test_import_names_aliases(self):
        self.assertEqual(N._import_names('a, b as c, *'),
                         [('a', 'a'), ('b', 'c')])

    def test_js_clause_named_and_alias(self):
        self.assertEqual(N._js_clause('{ foo, bar as baz }', 'baz'), 'bar')
        self.assertEqual(N._js_clause('{ foo }', 'foo'), 'foo')

    def test_js_clause_default_and_namespace(self):
        self.assertEqual(N._js_clause('Foo', 'Foo'), '')          # default → в начало
        self.assertEqual(N._js_clause('* as NS', 'NS'), '')
        self.assertIsNone(N._js_clause('{ a }', 'zzz'))

    def test_go_imports_block_and_single(self):
        src = 'import (\n\t"a/b/pkg"\n\talias "c/d"\n)\nimport "e/f"\n'
        self.assertEqual(set(N._go_imports(src)),
                         {('a/b/pkg', None), ('c/d', 'alias'), ('e/f', None)})


class TestPreferSelf(unittest.TestCase):
    def test_self_collapses_to_current_file(self):
        raw = [('view.py', 201, '    def foo(self):'),
               ('other.py', 5, '    def foo(self):')]
        ranked = N.rank_candidates(raw, 'view.py', 'foo', is_attr=True)
        out = N._prefer_self(ranked, 'view.py', 'self')
        self.assertEqual([t.path for t in out], ['view.py'])

    def test_self_keeps_all_when_absent_in_file(self):
        # метод унаследован — в текущем файле его нет, оставляем всех
        raw = [('base.py', 5, '    def foo(self):')]
        ranked = N.rank_candidates(raw, 'view.py', 'foo', is_attr=True)
        out = N._prefer_self(ranked, 'view.py', 'self')
        self.assertEqual([t.path for t in out], ['base.py'])

    def test_non_self_qualifier_untouched(self):
        raw = [('view.py', 1, 'def foo():'), ('other.py', 5, 'def foo():')]
        ranked = N.rank_candidates(raw, 'view.py', 'foo')
        out = N._prefer_self(ranked, 'view.py', 'obj')
        self.assertEqual(len(out), 2)


class TestParseGrep(unittest.TestCase):
    def test_parse_ok(self):
        out = N._parse_grep('a/x.py:12:def foo():\nb.py:3:foo = 1\n')
        self.assertEqual(out, [('a/x.py', 12, 'def foo():'), ('b.py', 3, 'foo = 1')])

    def test_skip_malformed(self):
        self.assertEqual(N._parse_grep('nonsense line\n'), [])

    def test_text_with_colons_preserved(self):
        out = N._parse_grep('x.py:5:a: int = url:port')
        self.assertEqual(out, [('x.py', 5, 'a: int = url:port')])


if __name__ == '__main__':
    unittest.main()
