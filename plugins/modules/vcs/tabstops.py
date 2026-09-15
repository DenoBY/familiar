"""Плейсхолдеры вставленного сниппета: где они сейчас, пока пользователь
печатает в одном из них.

Правка всегда идёт в текущем плейсхолдере, поэтому позиция остальных
держится за неизменную сторону строки: у тех, что левее, — отступ от
начала строки, у тех, что правее, — от её конца. Строки ниже правки
просто сдвигаются на вставленные или удалённые.
"""

from ..lsp.position import line_splice


Place = 'tuple[int, int, int]'      # (строка, начало, конец)

_HEAD, _TAIL = 'head', 'tail'


class Tabstops:

    def __init__(self, places: 'list[Place]', lines: 'list[str]') -> None:
        self._anchors: 'list[tuple[str, int, int, int]]' = []
        self.index = 0
        self._anchor(places, lines)

    def __len__(self) -> int:
        return len(self._anchors)

    def places(self, lines: 'list[str]') -> 'list[Place]':
        out = []
        for mode, line, a, b in self._anchors:
            if mode == _TAIL:
                n = len(lines[line])
                a, b = n - a, n - b
            out.append((line, a, b))
        return out

    def current(self, lines: 'list[str]') -> Place:
        return self.places(lines)[self.index]

    def jump(self, step: int, lines: 'list[str]') -> 'Place | None':
        """Перейти к соседнему плейсхолдеру; None — дальше некуда."""
        target = self.index + step
        if not 0 <= target < len(self._anchors):
            return None
        places = self.places(lines)
        self.index = target
        self._anchor(places, lines)
        return places[target]

    def _anchor(self, places: 'list[Place]', lines: 'list[str]') -> None:
        here = places[self.index][:2] if places else (0, 0)
        self._anchors = []
        for i, (line, a, b) in enumerate(places):
            if i != self.index and (line, a) > here:
                n = len(lines[line])
                self._anchors.append((_TAIL, line, n - a, n - b))
            else:
                self._anchors.append((_HEAD, line, a, b))

    def update(self, old: 'list[str]', new: 'list[str]') -> bool:
        """Перенести позиции через правку old → new. False — правка
        задела плейсхолдеры так, что их место уже не восстановить.
        """
        lo, old_n, new_n = line_splice(old, new)
        moved = []
        for mode, line, a, b in self._anchors:
            if line >= lo + old_n:
                line += new_n - old_n
            elif line >= lo:
                # внутри правки уцелела только голова первой строки и
                # хвост последней — остальное переписано
                if mode == _HEAD and line == lo and new_n:
                    line = lo
                elif mode == _TAIL and line == lo + old_n - 1 and new_n:
                    line = lo + new_n - 1
                else:
                    return False
            moved.append((mode, line, a, b))
        self._anchors = moved
        for line, a, b in self.places(new):
            if not 0 <= a <= b <= len(new[line]):
                return False
        return True
