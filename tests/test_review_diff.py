import unittest

import kittymock  # noqa: F401
import modules.vcs.diff as D
from modules import highlight as H


def unified(before, after, ext, width, **kw):
    return D.unified_rows(D.DiffSource(before, after), ext, width, **kw)


def final(before, after, ext, width, **kw):
    return D.final_rows(D.DiffSource(before, after), ext, width, **kw)


def marks(plains):
    """Символ маркера на полях каждой строки final-вида."""
    return [p[D._NUMW + 1] for p in plains]


class TestFgMap(unittest.TestCase):
    def test_highlights_by_token_kind(self):
        code = 'return 42 "s" # note'
        fg = H._fg_map(code, '.py')
        self.assertEqual(fg[code.index('r')], 'magenta')      # keyword
        self.assertEqual(fg[code.index('4')], 'cyan')         # number
        self.assertEqual(fg[code.index('"')], 'yellow')       # string
        self.assertEqual(fg[code.index('#')], 'gray')         # comment

    def test_comment_prefix_by_ext(self):
        code = 'x -- tail'
        fg = H._fg_map(code, '.sql')                          # в .sql комментарий это --
        self.assertEqual(fg[code.index('-')], 'gray')

    def test_plain_chars_have_no_color(self):
        fg = H._fg_map('xy zz', '.txt')
        self.assertTrue(all(c is None for c in fg))


class TestRenderCode(unittest.TestCase):
    def test_returns_full_text(self):
        # styled — тождество в моке, поэтому
        # результат = исходный код целиком
        code = 'def f(): return "a" # c'
        self.assertEqual(H.render_code(code, '.py'), code)
        self.assertEqual(H.render_code(code, '.py', 22, {0, 1, 2}, 28), code)


class TestWordRanges(unittest.TestCase):
    def test_replace_marks_changed_words(self):
        dset, aset, ratio = H.word_ranges('foo bar baz', 'foo qux baz')
        self.assertEqual(dset, {4, 5, 6})
        self.assertEqual(aset, {4, 5, 6})
        self.assertAlmostEqual(ratio, 0.8)

    def test_identical_full_ratio(self):
        dset, aset, ratio = H.word_ranges('same', 'same')
        self.assertEqual(dset, set())
        self.assertEqual(aset, set())
        self.assertEqual(ratio, 1.0)


class TestStrongSet(unittest.TestCase):
    def test_small_change_is_highlighted(self):
        line = 'foo qux baz'
        self.assertEqual(H.strong_set({4, 5, 6}, 0.8, line), {4, 5, 6})

    def test_dissimilar_pair_not_highlighted(self):
        self.assertIsNone(H.strong_set({0, 1, 2}, 0.1, 'abc'))

    def test_change_covering_most_of_the_line_not_highlighted(self):
        # позиционное спаривание внутри блока может свести комментарий
        # с кодом: ratio проходит на пробелах, а подсветка накрывает всё
        line = '    return DiffModel(rows, plains, hunks)'
        self.assertIsNone(H.strong_set(set(range(4, 40)), 0.35, line))

    def test_indent_does_not_dilute_coverage(self):
        # доля считается от кода, а не от строки с отступом
        deep = ' ' * 40 + 'x = 1'
        self.assertIsNone(H.strong_set(set(range(40, 45)), 0.5, deep))

    def test_blank_line_never_highlighted(self):
        self.assertIsNone(H.strong_set(set(), 1.0, '    '))


class TestUnifiedRows(unittest.TestCase):
    def test_single_line_modify(self):
        rows, plains, hunks, linenos, scopes, gaps, kinds, vis = unified(
            'l1\nl2\nl3\n', 'l1\nX2\nl3\n', '.py', 40)
        self.assertEqual(len(rows), 4)
        self.assertEqual(hunks, [1])
        self.assertEqual(linenos, [1, 2, 2, 3])
        self.assertEqual(kinds, [None, H.DEL_BG, H.ADD_BG, None])
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
        rows, plains, hunks, linenos, scopes, gaps, kinds, vis = unified(
            before, after, '.py', 40, context=3)
        self.assertTrue(any(g is not None for g in gaps))
        sep = [p for p in plains if 'hidden' in p]
        self.assertEqual(len(sep), 1)
        self.assertIn('4 lines hidden', sep[0])

    def test_expand_all_no_gap(self):
        before, after = self._big()
        rows, plains, hunks, linenos, scopes, gaps, kinds, vis = unified(
            before, after, '.py', 40, context=3, expand_all=True)
        self.assertTrue(all(g is None for g in gaps))
        self.assertFalse(any('hidden' in p for p in plains))

    def test_expanded_gap_shows_context(self):
        before, after = self._big()
        rows, plains, *_ , gaps, kinds = unified(
            before, after, '.py', 40, context=3, expanded={0})
        self.assertFalse(any('hidden' in p for p in plains))

    def test_added_file_one_column_all_adds(self):
        rows, plains, hunks, linenos, scopes, gaps, kinds, vis = unified(
            '', 'n1\nn2\nn3\n', '.py', 40)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(k == H.ADD_BG for k in kinds))
        self.assertEqual(linenos, [1, 2, 3])

    def test_deleted_file_one_column_all_dels(self):
        rows, plains, hunks, linenos, scopes, gaps, kinds, vis = unified(
            'o1\no2\n', '', '.py', 40)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(k == H.DEL_BG for k in kinds))

    def test_scope_tracks_enclosing_def(self):
        rows, plains, hunks, linenos, scopes, gaps, kinds, vis = unified(
            'def foo():\n    x = 1\n', 'def foo():\n    x = 2\n', '.py', 40)
        self.assertIn('def foo():', scopes)

    def test_vis_tracks_hscroll_plains_stay_full(self):
        # plains — полный текст (поиск/копирование),
        # vis — видимый срез по hscroll
        before, after = 'x\n', 'x0123456789abcdef\n'
        r0 = unified(before, after, '.py', 40, hscroll=0)
        r5 = unified(before, after, '.py', 40, hscroll=5)
        rows0, plains0, vis0 = r0[0], r0[1], r0[7]
        plains5, vis5 = r5[1], r5[7]
        self.assertEqual(len(vis0), len(rows0))       # vis параллелен rows
        self.assertEqual(plains0, plains5)            # полный текст не зависит от hscroll
        addv0 = [v for v in vis0 if '456789abcdef' in v][0]
        addv5 = [v for v in vis5 if '456789abcdef' in v][0]
        self.assertIn('0123456789abcdef', addv0)      # hscroll=0 — виден весь код
        self.assertNotIn('0123456789abcdef', addv5)   # hscroll=5 — начало ушло влево

    def test_max_hscroll_caps_at_longest_line(self):
        # два столбца: gutter_w=10, codew=width-12;
        # предел = longest - codew
        self.assertEqual(D.max_hscroll(D.DiffSource('short\n', 'x' * 50 + '\n'), 40), 22)
        # короткие строки целиком видны → скроллить вправо некуда
        self.assertEqual(D.max_hscroll(D.DiffSource('a\n', 'b\n'), 40), 0)
        # один столбец (added file): gutter_w=5, codew=width-7
        self.assertEqual(D.max_hscroll(D.DiffSource('', 'y' * 50 + '\n'), 40), 17)


class TestFinalRows(unittest.TestCase):
    def test_modified_line_marked_others_clean(self):
        rows, plains, hunks, linenos, scopes, gaps, kinds, vis = final(
            'l1\nl2\nl3\n', 'l1\nX2\nl3\n', '.py', 40)
        self.assertEqual(len(rows), 3)                 # только строки нового файла
        self.assertEqual(linenos, [1, 2, 3])
        self.assertEqual(hunks, [1])
        self.assertTrue(all(k is None for k in kinds))  # фона на всю строку нет
        self.assertTrue(all(g is None for g in gaps))
        self.assertEqual(marks(plains), [' ', D._MARK_CHANGE, ' '])
        self.assertTrue(plains[1].endswith('X2'))
        self.assertNotIn('l2', ''.join(plains))         # старой строки в файле нет

    def test_whole_file_shown_without_gaps(self):
        before = 'x\n' + '\n'.join(f'e{i}' for i in range(20)) + '\n'
        after = 'X\n' + '\n'.join(f'e{i}' for i in range(20)) + '\n'
        rows, plains, *_ = final(before, after, '.py', 40)
        self.assertEqual(len(rows), 21)                 # свёртки контекста нет
        self.assertFalse(any('hidden' in p for p in plains))

    def test_marks_distinguish_add_mod_delete(self):
        src = D.DiffSource('a\nold\nb\ngone\nc\n', 'a\nnew\nb\nc\nextra\n')
        mk, hunks = D.line_marks(src)
        self.assertEqual(mk[1], 'mod')                  # old → new
        self.assertEqual(mk[3], 'del')                  # перед 'c' вырезано 'gone'
        self.assertEqual(mk[4], 'add')                  # 'extra' дописана
        self.assertEqual(mk[0], None)
        self.assertEqual(hunks, [1, 3, 4])

    def test_delete_at_end_marks_last_line(self):
        src = D.DiffSource('a\nb\ngone\n', 'a\nb\n')
        mk, hunks = D.line_marks(src)
        self.assertEqual(mk, [None, 'del'])
        self.assertEqual(hunks, [1])

    def test_delete_of_whole_file_has_no_rows(self):
        rows, *_ = final('a\nb\n', '', '.py', 40)
        self.assertEqual(rows, [])                      # плейсхолдер ставит view

    def test_added_file_all_lines_marked_add(self):
        src = D.DiffSource('', 'n1\nn2\n')
        mk, hunks = D.line_marks(src)
        self.assertEqual(mk, ['add', 'add'])
        self.assertEqual(hunks, [0])

    def test_no_word_highlight_inside_lines(self):
        # изменение видно маркером на полях; заливки внутри строки нет —
        # с ней код не читается (styled в моке — тождество, значит
        # строка рендера совпадает с самим кодом)
        rows, plains, *_ = final('foo bar baz\n', 'foo qux baz\n', '.py', 40)
        self.assertEqual(rows[0], plains[0])
        self.assertEqual(marks(plains), [D._MARK_CHANGE])

    def test_replace_growing_block_tail_is_add(self):
        mk, _ = D.line_marks(D.DiffSource('a\nold\n', 'a\nnew1\nnew2\n'))
        self.assertEqual(mk, [None, 'mod', 'add'])

    def test_replace_shrinking_block_marks_cut_tail(self):
        # 3→1: своей строки у вырезанного хвоста нет, иначе правка
        # выглядела бы обычным 'mod' и удаление осталось бы незаметным
        mk, hunks = D.line_marks(D.DiffSource('a\nx1\nx2\nx3\nz\n', 'a\ny\nz\n'))
        self.assertEqual(mk, [None, 'mod', 'del'])
        self.assertEqual(hunks, [1, 2])

    def test_replace_shrinking_at_eof_marks_last_line(self):
        mk, _ = D.line_marks(D.DiffSource('a\nx1\nx2\n', 'a\ny\n'))
        self.assertEqual(mk, [None, 'mod'])   # строки за блоком нет — метить некуда

    def test_scope_tracks_enclosing_def(self):
        *_, scopes, _, _, _ = final(
            'def foo():\n    x = 1\n', 'def foo():\n    x = 2\n', '.py', 40)
        self.assertEqual(scopes, ['def foo():', 'def foo():'])

    def test_vis_tracks_hscroll_plains_stay_full(self):
        before, after = 'x\n', 'x0123456789abcdef\n'
        plains0, vis0 = final(before, after, '.py', 40)[1], final(before, after, '.py', 40)[7]
        plains5, vis5 = (final(before, after, '.py', 40, hscroll=5)[1],
                         final(before, after, '.py', 40, hscroll=5)[7])
        self.assertEqual(plains0, plains5)              # полный текст не зависит от hscroll
        self.assertIn('0123456789abcdef', vis0[0])
        self.assertNotIn('0123456789abcdef', vis5[0])

    def test_max_hscroll_ignores_removed_long_line(self):
        # final показывает только новый файл: длинная удалённая строка
        # не должна расширять предел скролла (gutter_w=5, codew=width-7)
        src = D.DiffSource('z' * 90 + '\n', 'y' * 50 + '\n')
        self.assertEqual(D.max_hscroll(src, 40, final=True), 17)
        # unified видит обе строки: две колонки номеров, codew=28
        self.assertEqual(D.max_hscroll(src, 40), 62)


class TestChangeMap(unittest.TestCase):
    def test_marks_projected_onto_bar_cells(self):
        marks = [None] * 10 + ['add'] * 10 + [None] * 20
        self.assertEqual(D.change_map(marks, 4), [None, 'add', None, None])

    def test_most_visible_mark_wins_a_shared_cell(self):
        # удаление своей строки не имеет — теряться в ячейке оно
        # не должно; добавление заметно и так
        self.assertEqual(D.change_map(['add', 'del', 'mod'], 1), ['del'])
        self.assertEqual(D.change_map(['add', 'mod'], 1), ['mod'])

    def test_last_line_lands_inside_the_bar(self):
        self.assertEqual(D.change_map([None, None, 'add'], 3)[-1], 'add')

    def test_empty_inputs(self):
        self.assertEqual(D.change_map([], 10), [])
        self.assertEqual(D.change_map(['add'], 0), [])

    def test_short_diff_is_not_stretched_over_the_bar(self):
        # дифф влезает в окно — риска стоит напротив своей строки,
        # а не уезжает вниз вместе с растянутой картой
        self.assertEqual(D.change_map([None, 'add', None], 9)[:3], [None, 'add', None])
        self.assertEqual(D.change_map(['add', 'del'], 4), ['add', 'del', None, None])

    def test_unified_kinds_become_the_same_marks(self):
        self.assertEqual(D.kinds_to_marks([H.ADD_BG, H.DEL_BG, None]),
                         ['add', 'del', None])


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

    def _grouped(self):
        return self._items() + [{'rel': 'a/g.py', 'kind': 'untracked',
                                 'stat': (1, 0), 'group': 'Unversioned Files'}]

    def test_group_node_last_and_namespaced(self):
        rows = D.build_tree(self._grouped(), set())
        grp = rows[4]
        self.assertEqual((grp['type'], grp['name'], grp['count'], grp['depth']),
                         ('dir', 'Unversioned Files', 1, 0))
        self.assertIsNone(grp['path'])
        inner = rows[5]
        self.assertEqual((inner['name'], inner['depth'], inner['path']), ('a', 1, 'a'))
        self.assertNotEqual(inner['key'], rows[0]['key'])
        self.assertEqual(rows[6]['name'], 'g.py')

    def test_group_collapsed_hides_children(self):
        rows = D.build_tree(self._grouped(), {D.group_key('Unversioned Files')})
        self.assertEqual(len(rows), 5)
        self.assertTrue(rows[4]['collapsed'])

    def test_collapsing_group_leaves_namesake_dir_outside_expanded(self):
        rows = D.build_tree(self._grouped(), {D.group_key('Unversioned Files')})
        dir_a = rows[0]
        self.assertEqual(dir_a['name'], 'a')
        self.assertFalse(dir_a['collapsed'])


class DiffCellTest(unittest.TestCase):
    """Общее ядро отрисовки строки диффа.

    styled в моке — тождество, маркеры видны.
    """

    def _arrays(self):
        return dict(
            rows=['row-ctx', 'row-add'],
            plains=['  1   ctx', '  2 + add'],
            linenos=[1, 2],
            kind_bg=[None, H.ADD_BG],
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
        # styled=тождество, поэтому подсветка невидима,
        # но текст сохранён целиком
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
        # подсвечивается только текущая строка-совпадение
        # (cur_match), не все
        a = self._arrays()
        a['cur_match'] = 1
        a['query'] = 'add'
        out = D.render_diff_cell(1, 40, False, 0, None, False, **a)
        self.assertIn('add', out)

    def test_cell_non_current_match_not_highlighted(self):
        # строка с совпадением, но не в фокусе
        # (cur_match != di) — обычная строка
        a = self._arrays()
        a['cur_match'] = 0
        a['query'] = 'add'
        out = D.render_diff_cell(1, 40, False, 0, None, False, **a)
        self.assertEqual(out, 'row-add')

    def test_cell_char_selection_from_plain(self):
        # char_sel рисует строку из plains (с подсветкой
        # куска), а не готовую rows[di]
        out = D.render_diff_cell(0, 40, True, 0, None, False, char_sel=(0, 6, 9),
                                 **self._arrays())
        self.assertIn('ctx', out)                 # текст из plain
        self.assertNotEqual(out.strip(), 'row-ctx')

    def test_cell_cursor_uses_vis_not_plains(self):
        # фон курсора рисуется по видимому тексту (vis),
        # чтобы ехать с hscroll
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
