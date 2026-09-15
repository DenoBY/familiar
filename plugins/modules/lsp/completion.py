"""Ответ `textDocument/completion` → пункты списка и правки, которые
делает принятый пункт.

Чистые функции: колонки здесь — индексы символов строки, а LSP-юниты
(UTF-16 по умолчанию) переводятся на входе. Буфер и экран — забота
редактора.
"""

import re
from typing import NamedTuple

from .position import decode_character
from .snippet import Stop, expand


# CompletionItemKind → метка в две клетки
KIND_ABBR = {
    1: 'tx', 2: 'm', 3: 'ƒ', 4: 'ƒ', 5: 'fd', 6: 'v', 7: 'C', 8: 'I', 9: 'M',
    10: 'p', 11: 'u', 12: 'vl', 13: 'E', 14: 'kw', 15: 'sn', 16: 'cl', 17: 'fl',
    18: 'rf', 19: 'dr', 20: 'e', 21: 'c', 22: 'S', 23: 'ev', 24: 'op', 25: 'T',
}
WORD_KIND = 'ab'

SNIPPET = 2
INSERT_AS_IS = 1

_WORD_TAIL = re.compile(r'[\w$]*$')

Span = 'tuple[int, int, int]'      # (строка LSP, начало, конец) в юнитах сервера
Pos = 'tuple[int, int]'


class Item:
    """Пункт списка. Изменяемый: resolve дописывает в тот же объект, и
    плашка, которая его держит, видит документацию без пересборки.
    """

    __slots__ = ('label', 'label_detail', 'description', 'kind', 'deprecated',
                 'preselect', 'sort_text', 'filter_text', 'new_text', 'snippet',
                 'keep_indent', 'insert', 'replace', 'additional', 'command',
                 'detail', 'documentation', 'raw', 'resolved')

    def __init__(self, raw: dict) -> None:
        self.raw = raw
        self.resolved = False
        self._fill(raw)

    def _fill(self, raw: dict) -> None:
        details = raw.get('labelDetails') if isinstance(raw.get('labelDetails'), dict) else {}
        self.label = _text(raw.get('label'))
        self.label_detail = _text(details.get('detail'))
        self.detail = _text(raw.get('detail'))
        self.description = _text(details.get('description')) or self.detail
        self.kind = KIND_ABBR.get(raw.get('kind'), '')
        tags = raw.get('tags') if isinstance(raw.get('tags'), list) else []
        self.deprecated = bool(raw.get('deprecated')) or 1 in tags
        self.preselect = bool(raw.get('preselect'))
        self.sort_text = _text(raw.get('sortText')) or self.label
        self.filter_text = _text(raw.get('filterText')) or self.label
        self.snippet = raw.get('insertTextFormat') == SNIPPET
        self.keep_indent = raw.get('insertTextMode') == INSERT_AS_IS
        edit = raw.get('textEdit') if isinstance(raw.get('textEdit'), dict) else None
        self.insert = self.replace = None
        if edit is not None:
            self.insert = _span(edit.get('insert') or edit.get('range'))
            self.replace = _span(edit.get('replace') or edit.get('range'))
            self.new_text = _text(edit.get('newText'))
        else:
            self.new_text = _text(raw.get('insertText')) or self.label
        edits = raw.get('additionalTextEdits')
        edits = edits if isinstance(edits, list) else []
        self.additional = [e for e in edits if isinstance(e, dict)]
        self.command = raw.get('command') if isinstance(raw.get('command'), dict) else None
        self.documentation = _doc(raw.get('documentation'))

    @property
    def valid(self) -> bool:
        """Диапазон правки обязан быть однострочным и один на оба
        варианта (спека); иначе пункт непонятно куда вставлять.
        """
        if not self.label:
            return False
        raw = self.raw.get('textEdit')
        if raw is None:
            return True
        spans = [self.insert, self.replace]
        return all(spans) and self.insert[0] == self.replace[0]

    def merge_resolved(self, resolved: object) -> None:
        if isinstance(resolved, dict):
            self.raw = {**self.raw, **resolved}
            self._fill(self.raw)
        self.resolved = True


def parse_completion(result: object) -> 'tuple[list[Item], bool]':
    """(пункты в порядке сервера, isIncomplete)."""
    if isinstance(result, list):
        raw_items, incomplete, defaults = result, False, {}
    elif isinstance(result, dict):
        raw_items = result.get('items') if isinstance(result.get('items'), list) else []
        incomplete = bool(result.get('isIncomplete'))
        defaults = result.get('itemDefaults')
        defaults = defaults if isinstance(defaults, dict) else {}
    else:
        return [], False
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item = Item(_with_defaults(raw, defaults))
        if item.valid:
            items.append(item)
    return items, incomplete


def _with_defaults(raw: dict, defaults: dict) -> dict:
    """Слить itemDefaults в пункт до всего остального: pyright кладёт
    туда `data`, без которого resolve не найдёт пункт.
    """
    if not defaults:
        return raw
    merged = dict(raw)
    for key in ('commitCharacters', 'insertTextFormat', 'insertTextMode', 'data'):
        if key in defaults and key not in merged:
            merged[key] = defaults[key]
    rng = defaults.get('editRange')
    if isinstance(rng, dict) and 'textEdit' not in merged:
        text = merged.get('textEditText') or merged.get('insertText') or merged.get('label')
        if 'start' in rng:
            merged['textEdit'] = {'range': rng, 'newText': text}
        else:
            merged['textEdit'] = {'insert': rng.get('insert'), 'replace': rng.get('replace'),
                                  'newText': text}
    return merged


def _span(rng: object) -> 'Span | None':
    if not isinstance(rng, dict):
        return None
    start, end = rng.get('start'), rng.get('end')
    if not (isinstance(start, dict) and isinstance(end, dict)):
        return None
    line, a, end_line, b = (start.get('line'), start.get('character'),
                            end.get('line'), end.get('character'))
    if not all(isinstance(x, int) for x in (line, a, end_line, b)) or line != end_line:
        return None
    return line, a, max(a, b)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ''


def _doc(value: object) -> str:
    if isinstance(value, dict):
        return _text(value.get('value'))
    return _text(value)


def word_start(line: str, caret: int) -> int:
    """Начало слова перед кареткой — для пунктов без диапазона."""
    return caret - len(_WORD_TAIL.search(line[:caret]).group())


def item_start(item: Item, line: str, caret: int, encoding: str) -> int:
    """Колонка, от которой пункт заменяет текст: от неё до каретки —
    слово, по которому фильтруем. У intelephense оно включает `$`, у
    ts — точку, поэтому регэксп слова здесь не годится.
    """
    if item.insert is None:
        return word_start(line, caret)
    return min(decode_character(line, item.insert[1], encoding), caret)


class Edit(NamedTuple):
    start: int
    end: int
    text: str
    stops: 'list[Stop]'      # смещения внутри text


def main_edit(item: Item, line: str, caret: int, asked_line: str, asked_caret: int,
              encoding: str, unit: str) -> Edit:
    """Правка строки каретки, которую делает принятый пункт.

    Сервер считал диапазон по тексту на момент запроса; набранное с
    тех пор лежит между началом и кареткой и заменяется вместе со
    словом. Хвост после каретки (replace) заменяем, только если
    вставка им и кончается — иначе съели бы чужой код.
    """
    start = item_start(item, line, caret, encoding)
    end = caret
    if item.replace is not None:
        tail = decode_character(asked_line, item.replace[2], encoding) - asked_caret
        if tail > 0 and item.new_text.endswith(line[caret:caret + tail]):
            end = min(len(line), caret + tail)
    raw = item.new_text
    if '\n' in raw and not item.keep_indent:
        raw = _reindent(raw, line[:len(line) - len(line.lstrip())], unit)
    text, stops = expand(raw) if item.snippet else (raw, [])
    text, stops = _drop_call_parens(text, stops, line[end:])
    return Edit(start, end, text, stops)


def _reindent(text: str, indent: str, unit: str) -> str:
    """Многострочная вставка — под отступ строки, куда вставляют;
    табы в начале её строк — в единицу отступа файла.
    """
    lines = text.split('\n')
    out = [lines[0]]
    for ln in lines[1:]:
        body = ln.lstrip('\t')
        out.append(indent + unit * (len(ln) - len(body)) + body)
    return '\n'.join(out)


def _drop_call_parens(text: str, stops: 'list[Stop]', after: str) -> 'tuple[str, list[Stop]]':
    """`is_dir()('/p')`: пункт-функция со скобками поверх уже стоящего
    вызова — скобки пункта лишние (bmewburn/vscode-intelephense#606).
    """
    if not (after.startswith('(') and text.endswith('()')):
        return text, stops
    cut = len(text) - 2
    return text[:cut], [s for s in stops if s.start <= cut and s.end <= cut]


def additional_edits(item: Item, lines: 'list[str]', encoding: str,
                     main: 'tuple[int, int, int]') -> 'list[tuple[Pos, Pos, str]]':
    """Правки auto-import в колонках буфера. Пересекающиеся с основной
    правкой спека запрещает — такие отбрасываем, а не чиним.
    """
    main_line, main_start, main_end = main
    out = []
    for edit in item.additional:
        rng = edit.get('range') if isinstance(edit.get('range'), dict) else {}
        start, end = rng.get('start'), rng.get('end')
        if not (isinstance(start, dict) and isinstance(end, dict)):
            continue
        a = _pos(lines, start, encoding)
        b = _pos(lines, end, encoding)
        if a is None or b is None or b < a:
            continue
        overlaps = a < (main_line, main_end) and b > (main_line, main_start)
        if overlaps or a == b == (main_line, main_start):
            continue
        out.append((a, b, _text(edit.get('newText'))))
    return out


def _pos(lines: 'list[str]', pos: dict, encoding: str) -> 'Pos | None':
    line, ch = pos.get('line'), pos.get('character')
    if not (isinstance(line, int) and isinstance(ch, int)) or line < 0:
        return None
    if line >= len(lines):
        # позиция за последней строкой — конец документа
        return len(lines) - 1, len(lines[-1])
    return line, decode_character(lines[line], ch, encoding)
