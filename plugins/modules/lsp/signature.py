"""Ответ `textDocument/signatureHelp` → строка сигнатуры и то, какой
параметр в ней сейчас набирают.
"""

from typing import NamedTuple

from .position import decode_character


class Signature(NamedTuple):
    label: str
    active: 'tuple[int, int] | None'   # [start, end) параметра в label


def parse_signature(result: object) -> 'Signature | None':
    if not isinstance(result, dict):
        return None
    signatures = [s for s in result.get('signatures') or [] if isinstance(s, dict)]
    if not signatures:
        return None
    index = result.get('activeSignature')
    index = index if isinstance(index, int) and 0 <= index < len(signatures) else 0
    sig = signatures[index]
    label = sig.get('label')
    if not isinstance(label, str) or not label:
        return None
    # activeParameter сигнатуры (3.16) важнее общего: pyright шлёт оба,
    # и общий у него на единицу больше
    active = sig.get('activeParameter')
    if not isinstance(active, int):
        active = result.get('activeParameter')
    params = [p for p in sig.get('parameters') or [] if isinstance(p, dict)]
    if not isinstance(active, int) or not 0 <= active < len(params):
        return Signature(label, None)
    return Signature(label, _span(label, params, active))


def _span(label: str, params: 'list[dict]', active: int) -> 'tuple[int, int] | None':
    name = params[active].get('label')
    if isinstance(name, list) and len(name) == 2 and all(isinstance(x, int) for x in name):
        # смещения — в UTF-16, как у Position
        return decode_character(label, name[0]), decode_character(label, name[1])
    if not isinstance(name, str) or not name:
        return None
    # строковая метка может повторяться (имя функции совпадает с
    # параметром): ищем по порядку, каждую — после предыдущей
    pos = label.find('(') + 1
    for i, param in enumerate(params[:active + 1]):
        text = param.get('label')
        if not isinstance(text, str) or not text:
            return None
        found = label.find(text, pos)
        if found < 0:
            return None
        if i == active:
            return found, found + len(text)
        pos = found + len(text)
    return None
