"""Раскладка ЙЦУКЕН → QWERTY: чтобы шорткаты китов срабатывали и на русской раскладке
(физическая клавиша j даёт «о», k — «л» и т.п.).

Общий для всех китов (review/log/session), поэтому лежит в корне пакета modules рядом с
overlay, а не внутри vcs/session — чтобы session не зависел от «vcs» ради клавиатуры.
"""

_RU = 'йцукенгшщзхъфывапролджэячсмитьбю'
_EN = "qwertyuiop[]asdfghjkl;'zxcvbnm,."
LAYOUT = {r: e for r, e in zip(_RU, _EN)}
LAYOUT.update({r.upper(): e.upper() for r, e in zip(_RU, _EN)})


def to_latin(ch: str) -> str:
    return LAYOUT.get(ch, ch)
