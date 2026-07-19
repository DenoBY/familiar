import unittest

import kittymock  # noqa: F401
from modules import highlight as H
from modules.vcs.diff import DiffSource


def roles(code, ext='.py', line=0):
    """Цвет каждого непробельного токена строки — как список пар."""
    cols = H.text_colors(code, ext)
    if cols is None:
        return None
    fg, out, i = cols[line], [], 0
    text = code.split('\n')[line]
    while i < len(text):
        j = i
        while j < len(text) and fg[j] == fg[i]:
            j += 1
        if text[i:j].strip():
            out.append((text[i:j], fg[i]))
        i = j
    return out


class TestPygmentsAvailable(unittest.TestCase):
    def test_vendored_pygments_loads(self):
        # подсветка уровня IDE держится на нём; без него киты работают,
        # но подсветка падает до встроенного лексера — это надо знать
        self.assertIsNotNone(H._PYGMENTS, 'pygments не найден в plugins/vendor')

    def test_lexer_missing_for_unknown_extension(self):
        self.assertIsNone(H._pygments_lexer('.qwerty'))
        self.assertIsNone(H.text_colors('x = 1', '.qwerty'))   # зовущий уйдёт в _fg_map

    def test_huge_file_skips_pygments(self):
        self.assertIsNone(H.text_colors('x = 1\n' * 80_000, '.py'))


class TestTextColors(unittest.TestCase):
    def test_colors_cover_every_character_of_every_line(self):
        code = 'def f():\n    return 1\n'
        cols = H.text_colors(code, '.py')
        for line, fg in zip(code.split('\n'), cols):
            self.assertEqual(len(fg), len(line))

    def test_keyword_string_number_comment(self):
        got = dict(roles("x = 'a' + 1  # note"))
        self.assertEqual(got["'a'"], H.C_STRING)
        self.assertEqual(got['1'], H.C_NUMBER)
        self.assertEqual(got['# note'], H.C_COMMENT)

    def test_function_call_is_colored_like_a_definition(self):
        # лексер метит Name.Function только в объявлении; вызов узнаём
        # по следующей скобке — иначе весь вызывающий код был бы серым
        self.assertEqual(dict(roles('def run(self):'))['run'], H.C_FUNC)
        self.assertEqual(dict(roles('plural(delta)'))['plural'], H.C_FUNC)

    def test_self_and_class_and_constant(self):
        self.assertEqual(dict(roles('self.x'))['self'], H.C_SELF)
        # по колонкам, не через roles(): в теме darcula C_CLASS
        # совпадает с цветом пунктуации, и roles() склеила бы
        # 'Foo(Base):' в одну группу
        cols = H.text_colors('class Foo(Base):', '.py')[0]
        self.assertEqual(cols[6], H.C_CLASS)      # F в Foo
        self.assertEqual(cols[10], H.C_CLASS)     # B в Base
        self.assertEqual(dict(roles('MAX = 1'))['MAX'], H.C_CONST)
        self.assertEqual(dict(roles('x = None'))['None'], H.C_KWCONST)

    def test_member_access_is_punctuation_not_operator(self):
        self.assertEqual(dict(roles('self.x'))['.'], H.C_PUNCT)
        self.assertEqual(dict(roles('$a->b();', '.php'))['->'], H.C_PUNCT)

    def test_docstring_spans_lines(self):
        code = 'def f():\n    """doc\n    more"""\n    pass'
        cols = H.text_colors(code, '.py')
        # C_DOC, не C_STRING: токен — String.Doc (в дефолтной теме цвета
        # совпадают, в darcula различаются)
        self.assertEqual(cols[2][4], H.C_DOC)         # вторая строка докстринга
        # встроенный лексер многострочную строку не видит — ради этого
        # и лексим файл целиком
        self.assertIsNone(H._fg_map('    more"""', '.py')[4])

    def test_php_without_open_tag_still_highlighted(self):
        self.assertEqual(dict(roles('$x = 1;', '.php'))['$x'], H.C_SELF)

    def test_blade_hash_is_text_not_php_comment(self):
        # #{{ }} в blade — обычный текст; inline-php лексер съел бы '#'
        # как комментарий до конца строки и покрасил серым всё правее
        code = "<td>#{{ $r['id'] }}</td>"
        cols = H.text_colors(code, '.php')
        tail = cols[0][code.index('#'):]
        self.assertFalse(all(c == H.C_COMMENT for c in tail))

    def test_php_file_with_open_tag_highlights_as_php(self):
        code = "<?php\nclass Foo {}"
        self.assertEqual(dict(roles(code, '.php', line=1))['class'], H.C_KEYWORD)

    def test_exotic_line_separators_do_not_shift_colors(self):
        # цвета обязаны ложиться на те же строки, что режет splitlines
        # у DiffSource, иначе ниже такого символа подсветка съезжает
        for sep in ('\f', '\r', '\u2028'):
            code = f"x = 1\n{sep}y = 'hi'\n"
            lines = code.splitlines()
            cols = H.text_colors(code, '.py')
            i = lines.index("y = 'hi'")
            self.assertEqual(cols[i][lines[i].index("'")], H.C_STRING, sep)


class TestFitFgs(unittest.TestCase):
    def test_slice_matches_visible_text(self):
        self.assertEqual(H.fit_fgs([1, 2, 3, 4], 1, 2), [2, 3])

    def test_pads_when_line_is_shorter(self):
        # truncate мог дописать многоточие — цветов на него нет
        self.assertEqual(H.fit_fgs([1, 2], 0, 4), [1, 2, None, None])

    def test_none_stays_none(self):
        self.assertIsNone(H.fit_fgs(None, 0, 3))


class TestRenderCodeWithColors(unittest.TestCase):
    def test_given_colors_are_used_as_is(self):
        # styled в моке — тождество, проверяем что текст не потерялся
        self.assertEqual(H.render_code('ab', '.py', fgs=[H.C_STRING, None]), 'ab')

    def test_falls_back_to_builtin_lexer_for_unknown_language(self):
        self.assertEqual(H.render_code('x = 1', '.qwerty'), 'x = 1')


class TestDiffSourceColors(unittest.TestCase):
    def test_colors_are_cached_per_side(self):
        src = DiffSource('a = 1\n', 'a = 2\n')
        self.assertIs(src.colors('.py', True), src.colors('.py', True))
        self.assertIsNot(src.colors('.py', True), src.colors('.py', False))

    def test_tabs_expanded_before_lexing(self):
        # цвета режутся по видимой строке, где таб уже стал пробелами
        src = DiffSource('', '\tx = 1\n')
        colors = src.colors('.py', new=True)
        self.assertEqual(len(colors[0]), len('    x = 1'))

    def test_unknown_language_yields_no_colors(self):
        self.assertIsNone(DiffSource('a\n', 'b\n').colors('.qwerty', new=True))


if __name__ == '__main__':
    unittest.main()
