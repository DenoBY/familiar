import unittest

import kittymock  # noqa: F401
import modules.vcs.diff as D


class TestFgMap(unittest.TestCase):
    def test_highlights_by_token_kind(self):
        code = 'return 42 "s" # note'
        fg = D._fg_map(code, '.py')
        self.assertEqual(fg[code.index('r')], 'magenta')      # keyword
        self.assertEqual(fg[code.index('4')], 'cyan')         # number
        self.assertEqual(fg[code.index('"')], 'yellow')       # string
        self.assertEqual(fg[code.index('#')], 'gray')         # comment

    def test_comment_prefix_by_ext(self):
        code = 'x -- tail'
        fg = D._fg_map(code, '.sql')                          # в .sql комментарий это --
        self.assertEqual(fg[code.index('-')], 'gray')

    def test_plain_chars_have_no_color(self):
        fg = D._fg_map('xy zz', '.txt')
        self.assertTrue(all(c is None for c in fg))


class TestRenderCode(unittest.TestCase):
    def test_returns_full_text(self):
        # styled — тождество в моке, поэтому результат = исходный код целиком
        code = 'def f(): return "a" # c'
        self.assertEqual(D.render_code(code, '.py'), code)
        self.assertEqual(D.render_code(code, '.py', 22, {0, 1, 2}, 28), code)


class TestWordRanges(unittest.TestCase):
    def test_replace_marks_changed_words(self):
        dset, aset, ratio = D._word_ranges('foo bar baz', 'foo qux baz')
        self.assertEqual(dset, {4, 5, 6})
        self.assertEqual(aset, {4, 5, 6})
        self.assertAlmostEqual(ratio, 0.8)

    def test_identical_full_ratio(self):
        dset, aset, ratio = D._word_ranges('same', 'same')
        self.assertEqual(dset, set())
        self.assertEqual(aset, set())
        self.assertEqual(ratio, 1.0)


class TestUnifiedRows(unittest.TestCase):
    def test_single_line_modify(self):
        rows, plains, hunks, linenos, scopes, gaps, kinds, vis = D.unified_rows(
            'l1\nl2\nl3\n', 'l1\nX2\nl3\n', '.py', 40)
        self.assertEqual(len(rows), 4)
        self.assertEqual(hunks, [1])
        self.assertEqual(linenos, [1, 2, 2, 3])
        self.assertEqual(kinds, [None, D.DEL_BG, D.ADD_BG, None])
        self.assertTrue(all(g is None for g in gaps))
        self.assertTrue(plains[0].endswith('l1'))
        self.assertTrue(plains[1].endswith('l2') and '-' in plains[1])
        self.assertTrue(plains[2].endswith('X2') and '+' in plains[2])
        self.assertTrue(plains[3].endswith('l3'))

    def _big(self):
        before = 'x\n' + '\n'.join(f'e{i}' for i in range(10)) + '\ny\n'
        after = 'X\n' + '\n'.join(f'e{i}' for i in range(10)) + '\nY\n'
        return before, after

    def test_context_folds_into_gap(self):
        before, after = self._big()
        rows, plains, hunks, linenos, scopes, gaps, kinds, vis = D.unified_rows(
            before, after, '.py', 40, context=3)
        self.assertTrue(any(g is not None for g in gaps))
        sep = [p for p in plains if 'hidden' in p]
        self.assertEqual(len(sep), 1)
        self.assertIn('4 lines hidden', sep[0])

    def test_expand_all_no_gap(self):
        before, after = self._big()
        rows, plains, hunks, linenos, scopes, gaps, kinds, vis = D.unified_rows(
            before, after, '.py', 40, context=3, expand_all=True)
        self.assertTrue(all(g is None for g in gaps))
        self.assertFalse(any('hidden' in p for p in plains))

    def test_expanded_gap_shows_context(self):
        before, after = self._big()
        rows, plains, *_ , gaps, kinds = D.unified_rows(
            before, after, '.py', 40, context=3, expanded={0})
        self.assertFalse(any('hidden' in p for p in plains))

    def test_added_file_one_column_all_adds(self):
        rows, plains, hunks, linenos, scopes, gaps, kinds, vis = D.unified_rows(
            '', 'n1\nn2\nn3\n', '.py', 40)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(k == D.ADD_BG for k in kinds))
        self.assertEqual(linenos, [1, 2, 3])

    def test_deleted_file_one_column_all_dels(self):
        rows, plains, hunks, linenos, scopes, gaps, kinds, vis = D.unified_rows(
            'o1\no2\n', '', '.py', 40)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(k == D.DEL_BG for k in kinds))

    def test_scope_tracks_enclosing_def(self):
        rows, plains, hunks, linenos, scopes, gaps, kinds, vis = D.unified_rows(
            'def foo():\n    x = 1\n', 'def foo():\n    x = 2\n', '.py', 40)
        self.assertIn('def foo():', scopes)

    def test_vis_tracks_hscroll_plains_stay_full(self):
        # plains — полный текст (поиск/копирование), vis — видимый срез по hscroll
        before, after = 'x\n', 'x0123456789abcdef\n'
        r0 = D.unified_rows(before, after, '.py', 40, hscroll=0)
        r5 = D.unified_rows(before, after, '.py', 40, hscroll=5)
        rows0, plains0, vis0 = r0[0], r0[1], r0[7]
        plains5, vis5 = r5[1], r5[7]
        self.assertEqual(len(vis0), len(rows0))       # vis параллелен rows
        self.assertEqual(plains0, plains5)            # полный текст не зависит от hscroll
        addv0 = [v for v in vis0 if '456789abcdef' in v][0]
        addv5 = [v for v in vis5 if '456789abcdef' in v][0]
        self.assertIn('0123456789abcdef', addv0)      # hscroll=0 — виден весь код
        self.assertNotIn('0123456789abcdef', addv5)   # hscroll=5 — начало ушло влево

    def test_max_hscroll_caps_at_longest_line(self):
        # два столбца: gutter_w=10, codew=width-12; предел = longest - codew
        self.assertEqual(D.max_hscroll('short\n', 'x' * 50 + '\n', 40), 22)
        # короткие строки целиком видны → скроллить вправо некуда
        self.assertEqual(D.max_hscroll('a\n', 'b\n', 40), 0)
        # один столбец (added file): gutter_w=5, codew=width-7
        self.assertEqual(D.max_hscroll('', 'y' * 50 + '\n', 40), 17)


class TestBuildTree(unittest.TestCase):
    def _items(self):
        return [{'rel': 'a/b.py', 'kind': 'modified', 'stat': (1, 2)},
                {'rel': 'a/c.py', 'kind': 'added', 'stat': (3, 0)},
                {'rel': 'd.py', 'kind': 'deleted', 'stat': None}]

    def test_expanded(self):
        rows = D.build_tree(self._items(), set())
        self.assertEqual(len(rows), 4)
        d = rows[0]
        self.assertEqual((d['type'], d['name'], d['count'], d['collapsed'], d['depth']),
                         ('dir', 'a', 2, False, 0))
        self.assertEqual((rows[1]['type'], rows[1]['name'], rows[1]['depth'], rows[1]['idx']),
                         ('file', 'b.py', 1, 0))
        self.assertEqual(rows[2]['name'], 'c.py')
        self.assertEqual((rows[3]['name'], rows[3]['depth'], rows[3]['kind']),
                         ('d.py', 0, 'deleted'))

    def test_collapsed_hides_children(self):
        rows = D.build_tree(self._items(), {'a'})
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]['collapsed'])
        self.assertEqual(rows[1]['name'], 'd.py')


class DiffCellTest(unittest.TestCase):
    """Общее ядро отрисовки строки диффа (styled в моке — тождество, маркеры видны)."""

    def _arrays(self):
        return dict(
            rows=['row-ctx', 'row-add'],
            plains=['  1   ctx', '  2 + add'],
            linenos=[1, 2],
            kind_bg=[None, D.ADD_BG],
            gaps=[None, None],
            cur_match=-1,
            query='')

    def test_is_code_row(self):
        self.assertTrue(D.is_code_row(0, [1, 0], [None, 5]))
        self.assertFalse(D.is_code_row(1, [1, 0], [None, 5]))   # lineno=0 → служебная
        self.assertFalse(D.is_code_row(0, [1], [5]))            # строка-гэп (gap id задан)
        self.assertFalse(D.is_code_row(5, [1, 2], [None, None]))  # вне диапазона

    def test_render_match_no_query_truncates(self):
        self.assertEqual(D.render_match('hello', 100, ''), 'hello')
        self.assertEqual(D.render_match('hello', 3, 'zzz'), 'he…')

    def test_render_match_returns_text_with_hit(self):
        # styled=тождество, поэтому подсветка невидима, но текст сохранён целиком
        self.assertEqual(D.render_match('abcabc', 100, 'bc'), 'abcabc')

    def test_cell_plain_when_not_focused(self):
        out = D.render_diff_cell(0, 40, False, 0, None, False, **self._arrays())
        self.assertEqual(out, 'row-ctx')                        # без фокуса — готовая строка

    def test_cell_cursor_marker(self):
        out = D.render_diff_cell(0, 40, True, 0, None, False, **self._arrays())
        self.assertIn('▎', out)                                 # курсор — вертикальная черта

    def test_cell_annotated_cursor_marker(self):
        out = D.render_diff_cell(0, 40, True, 0, None, True, **self._arrays())
        self.assertIn('●', out)                                 # аннотированный курсор — точка

    def test_cell_annotated_non_cursor(self):
        out = D.render_diff_cell(1, 40, True, 0, None, True, **self._arrays())
        self.assertTrue(out.startswith('●'))                    # маркер аннотации без курсора

    def test_cell_selection_no_marker(self):
        a = self._arrays()
        out = D.render_diff_cell(1, 40, True, 0, (0, 1), False, **a)
        self.assertNotIn('▎', out)                              # строка выделения без курсора
        self.assertIn('add', out)

    def test_cell_search_match(self):
        # подсвечивается только текущая строка-совпадение (cur_match), не все
        a = self._arrays()
        a['cur_match'] = 1
        a['query'] = 'add'
        out = D.render_diff_cell(1, 40, False, 0, None, False, **a)
        self.assertIn('add', out)

    def test_cell_non_current_match_not_highlighted(self):
        # строка с совпадением, но не в фокусе (cur_match != di) — обычная строка
        a = self._arrays()
        a['cur_match'] = 0
        a['query'] = 'add'
        out = D.render_diff_cell(1, 40, False, 0, None, False, **a)
        self.assertEqual(out, 'row-add')

    def test_cell_char_selection_from_plain(self):
        # char_sel рисует строку из plains (с подсветкой куска), а не готовую rows[di]
        out = D.render_diff_cell(0, 40, True, 0, None, False, char_sel=(0, 6, 9),
                                 **self._arrays())
        self.assertIn('ctx', out)                 # текст из plain
        self.assertNotEqual(out.strip(), 'row-ctx')

    def test_cell_cursor_uses_vis_not_plains(self):
        # фон курсора рисуется по видимому тексту (vis), чтобы ехать с hscroll
        a = self._arrays()
        a['vis'] = ['  1   VISIBLE', '  2 + add']
        out = D.render_diff_cell(0, 40, True, 0, None, False, **a)
        self.assertIn('VISIBLE', out)             # взят vis
        self.assertNotIn('ctx', out)              # полный plains не использован

    def test_cell_char_sel_uses_vis(self):
        a = self._arrays()
        a['vis'] = ['  1   XY', '  2 + add']
        out = D.render_diff_cell(0, 40, True, 0, None, False, char_sel=(0, 6, 8),
                                 hscroll=0, **a)
        self.assertIn('XY', out)                  # текст из vis
        self.assertNotIn('ctx', out)


if __name__ == '__main__':
    unittest.main()
