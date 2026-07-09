import unittest

import kittymock  # noqa: F401
import modules.session.transcript as T
from modules.session.data import Entry
from modules.text import HOME


W = 80


def texts(lines):
    return [ln.text for ln in lines]


class TestToolLabel(unittest.TestCase):
    def test_known_key_per_tool(self):
        self.assertEqual(T.tool_label('Bash', {'command': 'git status',
                                               'description': 'status'}),
                         'Bash(git status)')
        self.assertEqual(T.tool_label('Grep', {'pattern': 'def foo', 'glob': '*.py'}),
                         'Grep(def foo)')

    def test_path_is_shortened(self):
        label = T.tool_label('Read', {'file_path': HOME + '/x/y.py'})
        self.assertEqual(label, 'Read(~/x/y.py)')

    def test_multiline_command_collapses(self):
        self.assertEqual(T.tool_label('Bash', {'command': 'a\n  b'}), 'Bash(a b)')

    def test_unknown_tool_falls_back_to_first_string(self):
        self.assertEqual(T.tool_label('Weird', {'n': 1, 'q': 'text'}), 'Weird(text)')

    def test_no_input(self):
        self.assertEqual(T.tool_label('Weird', None), 'Weird()')

    def test_edit_is_named_update(self):
        self.assertEqual(T.tool_label('Edit', {'file_path': '/p/x.py'}, '/p'),
                         'Update(x.py)')

    def test_path_inside_project_is_relative(self):
        self.assertEqual(T.display_path('/p/a/b.py', '/p'), 'a/b.py')

    def test_path_outside_project_uses_tilde(self):
        self.assertEqual(T.display_path(HOME + '/z.py', '/p'), '~/z.py')

    def test_path_equal_to_root(self):
        self.assertEqual(T.display_path('/p', '/p'), '.')

    def test_sibling_of_root_is_not_relative(self):
        self.assertEqual(T.display_path('/proj-other/x.py', '/proj'),
                         '/proj-other/x.py')


PATCH = ((11, ' ', 'ctx'), (12, '-', 'old'), (12, '+', 'new'))


class TestEditResult(unittest.TestCase):
    def _lines(self, patch=PATCH, **kw):
        return T.transcript_lines(
            [Entry('result', 'The file … updated successfully.',
                   name='Edit', patch=patch, **kw)], W)

    def test_summary_replaces_technical_text(self):
        lines = self._lines()
        self.assertEqual(lines[0].text, '  ⎿  Added 1 line, removed 1 line')
        self.assertNotIn('updated successfully', texts(lines)[0])

    def test_rows_carry_line_numbers(self):
        stripped = [ln.text.strip() for ln in self._lines()]
        self.assertIn('11   ctx', stripped)
        self.assertIn('12 - old', stripped)
        self.assertIn('12 + new', stripped)

    def test_changed_rows_are_coloured(self):
        by_text = {ln.text.strip(): ln for ln in self._lines()}
        self.assertEqual(by_text['12 - old'].color, 'red')
        self.assertEqual(by_text['12 + new'].color, 'green')
        self.assertEqual(by_text['11   ctx'].color, T.DIM)   # контекст приглушён

    def test_code_is_syntax_highlighted(self):
        # с реальным styled фон и подсветка попадают в render;
        # в тестах styled — тождество, поэтому проверяем, что
        # render строится и совпадает с текстом
        lines = T.transcript_lines(
            [Entry('result', 'ok', name='Edit', tool_input={'file_path': '/a/b.py'},
                   patch=((1, '+', 'def f(): pass'),))], W)
        row = lines[1]
        self.assertIsNotNone(row.render)
        self.assertIn('def f(): pass', row.render)

    def test_pure_insert_omits_removed(self):
        lines = self._lines(patch=((1, '+', 'x'), (2, '+', 'y')))
        self.assertEqual(lines[0].text, '  ⎿  Added 2 lines')

    def test_pure_delete_omits_added(self):
        lines = self._lines(patch=((1, '-', 'x'),))
        self.assertEqual(lines[0].text, '  ⎿  Removed 1 line')

    def test_patch_is_never_folded(self):
        # правка — суть сообщения: показываем целиком, без «… +N lines»
        big = tuple((i, '+', f'line {i}') for i in range(T.FOLD_LINES + 20))
        lines = self._lines(patch=big)
        self.assertNotIn('…', ' '.join(texts(lines)))
        body = [ln for ln in lines if ln.text]
        self.assertEqual(len(body), len(big) + 1)    # заголовок + все строки
        self.assertEqual(lines[0].entry, -1)         # и сворачивать нечего

    def test_word_diff_marks_only_changed_tokens(self):
        rows = ((1, '-', 'x = old_name(a)'), (1, '+', 'x = new_name(a)'))
        dset, aset = T._patch_strong(rows)
        # подсвечен только изменившийся идентификатор, не «x = (a)»
        self.assertEqual(''.join(rows[0][2][i] for i in sorted(dset)), 'old_name')
        self.assertEqual(''.join(rows[1][2][i] for i in sorted(aset)), 'new_name')

    def test_word_diff_skips_unpaired_and_dissimilar(self):
        # 'ctx' без пары; следующая пара слишком непохожа —
        # красить нечего
        rows = ((1, ' ', 'ctx'), (2, '-', 'a'), (2, '+', 'zzz qqq www'))
        self.assertEqual(T._patch_strong(rows), [None, None, None])

    def test_word_diff_pairs_by_similarity_not_position(self):
        # порядок строк в блоке правки съезжает: удалённую тянем к самой
        # похожей добавленной, иначе подсвечивается мусор
        rows = ((1, '-', "prefix = '> ' if not out else '  '"),
                (2, '-', 'plain = prefix + wl'),
                (1, '+', 'first = not out'),
                (2, '+', "prefix = '> ' if first else '  '"))
        strong = T._patch_strong(rows)
        # спарились 1-я удалённая и 2-я добавленная:
        # изменилось «not out» → «first»
        self.assertEqual(''.join(rows[0][2][i] for i in sorted(strong[0])), 'not out')
        self.assertEqual(''.join(rows[3][2][i] for i in sorted(strong[3])), 'first')
        # оставшиеся друг другу не пара — не красим
        self.assertIsNone(strong[1])
        self.assertIsNone(strong[2])

    def test_write_reports_line_count(self):
        lines = T.transcript_lines(
            [Entry('result', 'ok', name='Write', tool_input={'content': 'a\nb\nc'},
                   patch=PATCH)], W)
        self.assertEqual(lines[0].text, '  ⎿  Wrote 3 lines')

    def test_failed_edit_keeps_error_text(self):
        lines = T.transcript_lines(
            [Entry('result', 'String not found', name='Edit', patch=PATCH,
                   error=True)], W)
        self.assertEqual(lines[0].text, '  ⎿  Error: String not found')
        self.assertEqual(lines[0].color, 'red')

    def test_edit_without_patch_falls_back_to_raw_text(self):
        lines = self._lines(patch=())
        self.assertEqual(lines[0].text, '  ⎿  The file … updated successfully.')

    def test_other_tools_keep_raw_output(self):
        lines = T.transcript_lines(
            [Entry('result', 'out', name='Bash', tool_input={'command': 'ls'})], W)
        self.assertEqual(lines[0].text, '  ⎿  out')


class TestPlan(unittest.TestCase):
    def _plan(self, plan, expanded=frozenset()):
        return T.transcript_lines(
            [Entry('tool', name='ExitPlanMode', tool_input={'plan': plan})],
            W, expanded=expanded)

    def test_header_has_no_argument(self):
        self.assertEqual(self._plan('# Заголовок')[0].text, '⏺ Updated plan')

    def test_plan_is_framed_and_rendered(self):
        out = [t for t in texts(self._plan('# Заголовок\n\n- пункт')) if t]
        self.assertTrue(out[1].startswith('  ┌─'))
        self.assertIn('  │ Заголовок', out)     # markdown: решётки убраны
        self.assertIn('  │ • пункт', out)
        self.assertTrue(out[-1].startswith('  └─'))

    def test_long_plan_is_folded_and_expandable(self):
        plan = '\n'.join(f'- пункт {i}' for i in range(40))
        folded = [t for t in texts(self._plan(plan)) if t]
        self.assertEqual(self._plan(plan)[0].entry, 0)
        self.assertTrue(folded[-1].strip().startswith('… +'))
        self.assertTrue(folded[-1].endswith(T.EXPAND_HINT))

        full = [t for t in texts(self._plan(plan, expanded={0})) if t]
        self.assertGreater(len(full), len(folded))
        self.assertTrue(full[-1].startswith('  └─'))

    def test_empty_plan_is_just_the_header(self):
        lines = [ln for ln in self._plan('') if ln.text]
        self.assertEqual(len(lines), 1)

    def test_result_says_approved_or_rejected(self):
        ok = T.transcript_lines([Entry('result', 'User has approved…',
                                       name='ExitPlanMode')], W)
        self.assertEqual(ok[0].text, '  ⎿  Plan approved')
        self.assertEqual(ok[0].color, T.DIM)

        no = T.transcript_lines([Entry('result', "The user doesn't want…",
                                       name='ExitPlanMode', error=True)], W)
        self.assertEqual(no[0].text, '  ⎿  Plan rejected')
        self.assertEqual(no[0].color, 'red')


class TestSummary(unittest.TestCase):
    def _read(self, expanded=frozenset()):
        body = '\n'.join(f'line {i}' for i in range(3))
        return T.transcript_lines(
            [Entry('result', body, name='Read', summary='Read 3 lines')],
            W, expanded=expanded)

    def test_collapsed_shows_summary_only(self):
        lines = [ln for ln in self._read() if ln.text]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].text, '  ⎿  Read 3 lines (ctrl+o to expand)')
        self.assertEqual(lines[0].entry, 0)     # раскрывается кликом

    def test_expanded_shows_body(self):
        self.assertIn('     line 2', texts(self._read(expanded={0})))

    def test_error_ignores_summary(self):
        lines = T.transcript_lines(
            [Entry('result', 'boom', name='Read', summary='Read 3 lines',
                   error=True)], W)
        self.assertEqual(lines[0].text, '  ⎿  Error: boom')


class TestTranscriptLines(unittest.TestCase):
    def test_user_and_assistant_prefixes(self):
        lines = T.transcript_lines([Entry('user', 'вопрос'),
                                    Entry('assistant', 'ответ')], W)
        self.assertIn('> вопрос', texts(lines))
        self.assertIn('⏺ ответ', texts(lines))

    def test_assistant_text_is_markdown(self):
        lines = T.transcript_lines(
            [Entry('assistant', 'вот **жирный**\n\n```python\nx = 1\n```')], W)
        self.assertEqual(lines[0].text, '⏺ вот жирный')
        self.assertIsNotNone(lines[0].render)
        self.assertIn('    x = 1', texts(lines))

    def test_user_text_is_not_markdown(self):
        lines = T.transcript_lines([Entry('user', 'звёзды **как есть**')], W)
        self.assertEqual(lines[0].text, '> звёзды **как есть**')

    def test_user_lines_get_a_full_width_background(self):
        # styled в тестах — тождество, поэтому render равен
        # видимому тексту:
        # плашка фона должна дотягиваться ровно до правого края
        lines = T.transcript_lines([Entry('user', 'вопрос\nвторая строка')], 40)
        self.assertEqual(texts(lines)[:2], ['> вопрос', '  вторая строка'])
        self.assertTrue(all(len(ln.render) == 40 for ln in lines[:2]))

    def test_attachment_hangs_under_its_prompt(self):
        lines = T.transcript_lines([Entry('user', 'смотри'),
                                    Entry('attach', '[Image #13]'),
                                    Entry('attach', '[Image #14]')], W)
        # вложения примыкают к реплике — без пустой строки между ними
        self.assertEqual(texts(lines)[:3],
                         ['> смотри', '  ⎿  [Image #13]', '  ⎿  [Image #14]'])
        self.assertEqual(lines[1].color, T.DIM)
        self.assertFalse(lines[1].prompt)

    def test_multiline_command_keeps_its_lines(self):
        lines = T.transcript_lines(
            [Entry('tool', name='Bash', tool_input={'command': "kitty +runpy '\nimport sys"})],
            W)
        self.assertEqual(texts(lines)[:2], ["⏺ Bash(kitty +runpy '", '    import sys)'])

    def test_command_longer_than_two_lines_is_cut(self):
        cmd = '\n'.join(f'line {i}' for i in range(5))
        lines = T.transcript_lines([Entry('tool', name='Bash',
                                          tool_input={'command': cmd})], W)
        self.assertEqual(texts(lines)[:2], ['⏺ Bash(line 0', '    line 1…)'])

    def test_tool_line_carries_render(self):
        lines = T.transcript_lines(
            [Entry('tool', name='Bash', tool_input={'command': 'ls -la'})], W)
        head = lines[0]
        self.assertEqual(head.text, '⏺ Bash(ls -la)')
        self.assertIsNotNone(head.render)

    def test_long_result_is_folded(self):
        body = '\n'.join(f'line {i}' for i in range(T.FOLD_LINES + 3))
        lines = T.transcript_lines([Entry('result', body)], W)
        self.assertEqual(lines[0].text, '  ⎿  line 0')
        self.assertEqual(lines[0].entry, 0)
        self.assertIn(f'     … +3 lines{T.EXPAND_HINT}', texts(lines))
        self.assertNotIn(f'     line {T.FOLD_LINES}', texts(lines))

    def test_expanded_result_shows_everything(self):
        body = '\n'.join(f'line {i}' for i in range(T.FOLD_LINES + 3))
        lines = T.transcript_lines([Entry('result', body)], W, expanded={0})
        self.assertIn(f'     line {T.FOLD_LINES + 2}', texts(lines))
        self.assertNotIn('     … +3 lines', texts(lines))
        self.assertEqual(lines[0].entry, 0)   # свернуть обратно можно с той же строки

    def test_short_result_is_not_foldable(self):
        lines = T.transcript_lines([Entry('result', 'ok')], W)
        self.assertEqual(lines[0].entry, -1)

    def test_error_result_is_red(self):
        lines = T.transcript_lines([Entry('result', 'boom', error=True)], W)
        self.assertEqual(lines[0].text, '  ⎿  Error: boom')
        self.assertEqual(lines[0].color, 'red')

    def test_prose_lines_fit_width(self):
        lines = T.transcript_lines([Entry('assistant', 'x' * 500)], 40)
        self.assertTrue(all(len(ln.text) <= 40 for ln in lines))

    def test_result_keeps_full_text_for_copying(self):
        # вывод не переносится и не режется: обрезает
        # отрисовка, а копируется целиком
        lines = T.transcript_lines([Entry('result', 'x' * 500)], 40)
        self.assertGreater(len(lines[0].text), 40)

    def test_long_tool_name_render_fits_width(self):
        # styled в тестах — тождество, render равен видимому тексту:
        # длинное имя инструмента не должно вылезать за ширину экрана
        name = 'mcp__tinkerwell__evaluate-remote-php-code'
        lines = T.transcript_lines(
            [Entry('tool', name=name, tool_input={'command': 'x'})], 30)
        self.assertLessEqual(len(lines[0].render), 30)

    def test_fold_marker_is_clickable(self):
        body = '\n'.join(f'line {i}' for i in range(T.FOLD_LINES + 3))
        lines = T.transcript_lines([Entry('result', body)], W)
        marker = next(ln for ln in lines if ln.text.strip().startswith('…'))
        self.assertEqual(marker.entry, 0)

    def test_patch_summary_uses_full_stat(self):
        # patch обрезан по MAX_RESULT_LINES — сводка берёт
        # честные счётчики
        lines = T.transcript_lines(
            [Entry('result', 'ok', name='Edit',
                   patch=((1, '+', 'x'),), patch_stat=(500, 0))], W)
        self.assertEqual(lines[0].text, '  ⎿  Added 500 lines')


if __name__ == '__main__':
    unittest.main()
