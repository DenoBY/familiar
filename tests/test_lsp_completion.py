import unittest

import kittymock  # noqa: F401
from modules.lsp.completion import (
    Item,
    additional_edits,
    item_start,
    main_edit,
    parse_completion,
    word_start,
)
from modules.lsp.snippet import Stop


def _rng(line, a, b, end_line=None):
    return {'start': {'line': line, 'character': a},
            'end': {'line': line if end_line is None else end_line, 'character': b}}


# пункты живого intelephense: `$u->whe|` и `$u = Use|` без use
PHP_WHERE = {
    'label': 'where', 'kind': 2, 'detail': 'static',
    'labelDetails': {'detail': '($column, $operator, $value)'},
    'textEdit': {'newText': 'where($0)', 'insert': _rng(11, 12, 15), 'replace': _rng(11, 12, 15)},
    'insertTextFormat': 2, 'sortText': '0004where',
    'command': {'title': 'Trigger Parameter Hints',
                'command': 'editor.action.triggerParameterHints'},
    'data': '3x-hec-w'}
PHP_USER = {
    'label': 'User', 'kind': 7, 'detail': 'use App\\Models\\User',
    'labelDetails': {'description': 'App\\Models'},
    'textEdit': {'newText': 'User', 'insert': _rng(9, 13, 16), 'replace': _rng(9, 13, 16)},
    'additionalTextEdits': [{'range': _rng(2, 19, 0, end_line=5),
                             'newText': '\n\nuse App\\Models\\User;\n\n'}],
    'insertTextFormat': 1, 'sortText': '0004User', 'data': '3x-6bq-1'}
PHP_CTOR = {
    'label': '__construct', 'kind': 2,
    'textEdit': {'newText': "__construct($1)\n{\n\t${2:throw new \\\\Exception('x');}$0\n\\}",
                 'insert': _rng(11, 20, 25), 'replace': _rng(11, 20, 25)},
    'insertTextFormat': 2}
# pyright: без диапазона, auto-import — сразу в пункте
PY_JOIN = {'label': 'join', 'kind': 3, 'sortText': '11.9999.join',
           'data': {'symbolLabel': 'join'}}


class ParseTest(unittest.TestCase):
    def test_list_array_and_null(self):
        items, incomplete = parse_completion({'isIncomplete': True, 'items': [PHP_WHERE]})
        self.assertEqual(([i.label for i in items], incomplete), (['where'], True))
        self.assertEqual([i.label for i in parse_completion([PY_JOIN])[0]], ['join'])
        self.assertEqual(parse_completion(None), ([], False))

    def test_fields(self):
        where, user = parse_completion([PHP_WHERE, PHP_USER])[0]
        self.assertEqual((where.kind, where.label_detail, where.description),
                         ('m', '($column, $operator, $value)', 'static'))
        self.assertTrue(where.snippet)
        self.assertEqual(where.insert, (11, 12, 15))
        self.assertEqual(user.kind, 'C')
        self.assertEqual(user.description, 'App\\Models')
        self.assertEqual(len(user.additional), 1)

    def test_item_defaults_are_merged(self):
        result = {'items': [{'label': 'foo', 'textEditText': 'foo()'}, {'label': 'bar'}],
                  'itemDefaults': {'editRange': {'insert': _rng(0, 2, 4),
                                                 'replace': _rng(0, 2, 6)},
                                   'insertTextFormat': 2, 'data': {'id': 1}}}
        foo, bar = parse_completion(result)[0]
        self.assertEqual((foo.new_text, foo.insert, foo.replace), ('foo()', (0, 2, 4), (0, 2, 6)))
        self.assertTrue(foo.snippet)
        self.assertEqual(bar.raw['data'], {'id': 1})
        self.assertEqual(bar.new_text, 'bar')

    def test_default_format_is_plain_text(self):
        # intelephense шлёт пункты без insertTextFormat (neovim#14563)
        item = Item({'label': '$x', 'textEdit': {'newText': '$GLOBALS', 'range': _rng(0, 0, 1)}})
        self.assertFalse(item.snippet)

    def test_multiline_range_dropped(self):
        bad = {'label': 'x', 'textEdit': {'newText': 'x', 'range': _rng(0, 0, 1, end_line=1)}}
        self.assertEqual(parse_completion([bad])[0], [])

    def test_deprecated_by_flag_or_tag(self):
        self.assertTrue(Item({'label': 'a', 'deprecated': True}).deprecated)
        self.assertTrue(Item({'label': 'a', 'tags': [1]}).deprecated)
        self.assertFalse(Item({'label': 'a'}).deprecated)

    def test_resolve_fills_the_same_object(self):
        item = Item(dict(PY_JOIN))
        item.merge_resolved({**PY_JOIN, 'documentation': {'kind': 'markdown', 'value': 'Join.'}})
        self.assertEqual(item.documentation, 'Join.')
        self.assertTrue(item.resolved)

    def test_garbage_is_ignored(self):
        items, _ = parse_completion({'items': [1, None, {'label': 5},
                                               {'label': 'ok', 'kind': 'x'}]})
        self.assertEqual([i.label for i in items], ['ok'])


class StartTest(unittest.TestCase):
    def test_word_start(self):
        self.assertEqual(word_start('os.path.jo', 10), 8)
        self.assertEqual(word_start('    $us', 7), 4)

    def test_range_start_includes_dollar(self):
        item = Item({'label': '$users',
                     'textEdit': {'newText': '$users', 'range': _rng(0, 8, 11)}})
        line = '        $us'
        self.assertEqual(line[item_start(item, line, 11, 'utf-16'):11], '$us')

    def test_range_in_utf16_units(self):
        item = Item({'label': 'x', 'textEdit': {'newText': 'x', 'range': _rng(0, 3, 4)}})
        self.assertEqual(item_start(item, '🙂 x', 3, 'utf-16'), 2)


class MainEditTest(unittest.TestCase):
    def edit(self, raw, line, caret, asked_line=None, asked_caret=None, unit='    '):
        item = Item(raw)
        return main_edit(item, line, caret, asked_line if asked_line is not None else line,
                         caret if asked_caret is None else asked_caret, 'utf-16', unit)

    def test_snippet_caret_inside_parens(self):
        line = '        $u->whe'
        edit = self.edit(PHP_WHERE, line, len(line))
        self.assertEqual((edit.start, edit.end, edit.text), (12, 15, 'where()'))
        self.assertEqual(edit.stops, [Stop(0, 6, 6)])

    def test_text_typed_after_request_is_replaced(self):
        asked = '        $u->whe'
        now = '        $u->wher'
        edit = self.edit(PHP_WHERE, now, len(now), asked, len(asked))
        self.assertEqual((edit.start, edit.end), (12, 16))

    def test_replace_tail_only_when_insertion_ends_with_it(self):
        raw = {'label': 'where', 'textEdit': {'newText': 'where', 'insert': _rng(0, 0, 3),
                                              'replace': _rng(0, 0, 5)}}
        self.assertEqual(self.edit(raw, 'whe' + 're', 3).end, 5)
        self.assertEqual(self.edit(raw, 'whe' + 'xy', 3).end, 3)

    def test_existing_call_parens_not_doubled(self):
        raw = {'label': 'is_dir', 'insertTextFormat': 2,
               'textEdit': {'newText': 'is_dir($0)', 'range': _rng(0, 0, 7)}}
        edit = self.edit(raw, "is_file('/p')", 7)
        self.assertEqual(edit.text, 'is_dir')
        self.assertEqual(edit.stops, [])

    def test_multiline_snippet_follows_indent_and_unit(self):
        line = '    public function __con'
        edit = self.edit(PHP_CTOR, line, len(line), unit='  ')
        self.assertEqual(edit.text,
                         "__construct()\n    {\n      throw new \\Exception('x');\n    }")
        self.assertEqual([s.index for s in edit.stops], [1, 2, 0])

    def test_plain_without_range_uses_word(self):
        edit = self.edit(PY_JOIN, 'os.path.jo', 10)
        self.assertEqual((edit.start, edit.end, edit.text), (8, 10, 'join'))


class AdditionalEditsTest(unittest.TestCase):
    def test_php_use_statement(self):
        lines = ['<?php', '', 'namespace App\\Http;', '', '', 'class Controller']
        item = Item(PHP_USER)
        edits = additional_edits(item, lines, 'utf-16', (9, 13, 16))
        self.assertEqual(edits, [((2, 19), (5, 0), '\n\nuse App\\Models\\User;\n\n')])

    def test_overlap_with_main_edit_dropped(self):
        item = Item({'label': 'x', 'additionalTextEdits': [
            {'range': _rng(3, 2, 6), 'newText': 'bad'},
            {'range': _rng(3, 4, 4), 'newText': 'at start is bad too'},
            {'range': _rng(0, 0, 0), 'newText': 'import x\n'}]})
        edits = additional_edits(item, ['a', 'b', 'c', 'dddddddd'], 'utf-16', (3, 4, 8))
        self.assertEqual(edits, [((0, 0), (0, 0), 'import x\n')])

    def test_position_past_document_end(self):
        item = Item({'label': 'x', 'additionalTextEdits': [
            {'range': _rng(9, 0, 0), 'newText': '\nend'}]})
        self.assertEqual(additional_edits(item, ['a', 'bc'], 'utf-16', (0, 0, 1)),
                         [((1, 2), (1, 2), '\nend')])


if __name__ == '__main__':
    unittest.main()
