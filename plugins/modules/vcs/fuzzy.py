"""Нечёткое сопоставление набранного слова с пунктами автодополнения.

Подсчёт — fzf v1 (junegunn/fzf, src/algo/algo.go): жадное окно
совпадения и бонусы за начала слов. Сверху правило VS Code
`matchOnWordStartOnly`: первая буква запроса обязана попасть в начало
слова кандидата, иначе `ab` находил бы `grab` раньше, чем `aBc`.
"""

import re
from typing import NamedTuple


class Match(NamedTuple):
    tier: int                      # 0 — точное, 1 — префикс, 2 — нечёткое
    score: int                     # больше — лучше
    positions: 'tuple[int, ...]'   # совпавшие символы кандидата


EXACT, PREFIX, FUZZY = 0, 1, 2

SCORE_MATCH = 16
GAP_START = -3
GAP_EXTENSION = -1
BONUS_BOUNDARY = 8
BONUS_CAMEL = 7
BONUS_CONSECUTIVE = 4

# сколько начал слов пробовать под первую букву: дальше растёт цена,
# а не качество — лучшие окна почти всегда в первых словах
MAX_STARTS = 3

_LOWER, _UPPER, _DIGIT, _OTHER = range(4)
_SEPARATORS = frozenset('_-.:/\\$ ')

_IDENT = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')


def _cls(ch: str) -> int:
    if ch.islower():
        return _LOWER
    if ch.isupper():
        return _UPPER
    if ch.isdigit():
        return _DIGIT
    return _OTHER


def _bonus(prev: int, cur: int) -> int:
    if cur == _OTHER or prev == _OTHER:
        return BONUS_BOUNDARY
    if (prev == _LOWER and cur == _UPPER) or (prev != _DIGIT and cur == _DIGIT):
        return BONUS_CAMEL
    return 0


def _starts(candidate: str, first: str) -> 'list[int]':
    """Начала слов кандидата, где стоит первая буква запроса."""
    out = []
    prev = ''
    for i, ch in enumerate(candidate):
        boundary = (i == 0 or prev in _SEPARATORS or ch in _SEPARATORS
                    or (prev.islower() and ch.isupper())
                    or (not prev.isdigit() and ch.isdigit()))
        if boundary and ch.lower() == first:
            out.append(i)
            if len(out) == MAX_STARTS:
                break
        prev = ch
    return out


def match(query: str, candidate: str) -> 'Match | None':
    if not query:
        return Match(FUZZY, 0, ())
    ql, cl = query.lower(), candidate.lower()
    pos = -1
    for ch in ql:
        pos = cl.find(ch, pos + 1)
        if pos < 0:
            return None
    if cl.startswith(ql):
        # точное — с регистром: `Use` не должен ставить ключевое слово
        # use выше класса User
        tier = EXACT if candidate == query else PREFIX
        return Match(tier, _score(query, candidate, 0, len(ql) - 1)[0],
                     tuple(range(len(ql))))
    best = None
    for start in _starts(candidate, ql[0]):
        end = _window_end(ql, cl, start)
        if end is None:
            continue
        found = _score(query, candidate, _tighten(ql, cl, start, end), end)
        if best is None or found[0] > best[0]:
            best = found
    if best is None:
        return None
    return Match(FUZZY, best[0], best[1])


def _window_end(ql: str, cl: str, start: int) -> 'int | None':
    pos = start
    for ch in ql[1:]:
        pos = cl.find(ch, pos + 1)
        if pos < 0:
            return None
    return pos


def _tighten(ql: str, cl: str, start: int, end: int) -> int:
    """Самое правое начало окна, в которое ещё влезает весь запрос —
    проход fzf v1 назад от конца. Не левее найденного начала слова:
    иначе первая буква съехала бы с него.
    """
    pi = len(ql) - 1
    for i in range(end, start - 1, -1):
        if cl[i] == ql[pi]:
            pi -= 1
            if pi < 0:
                return i
    return start


def _score(query: str, candidate: str, start: int, end: int) -> 'tuple[int, tuple[int, ...]]':
    ql = query.lower()
    score = consecutive = first_bonus = 0
    in_gap = False
    pi = 0
    positions = []
    prev = _cls(candidate[start - 1]) if start else _OTHER
    for i in range(start, end + 1):
        ch = candidate[i]
        cur = _cls(ch)
        if pi < len(ql) and ch.lower() == ql[pi]:
            bonus = _bonus(prev, cur)
            if consecutive == 0:
                first_bonus = bonus
            else:
                if bonus >= BONUS_BOUNDARY and bonus > first_bonus:
                    first_bonus = bonus
                bonus = max(bonus, first_bonus, BONUS_CONSECUTIVE)
            score += SCORE_MATCH + (bonus * 2 if pi == 0 else bonus)
            if ch == query[pi]:
                score += 1        # регистр — только при прочих равных
            positions.append(i)
            consecutive += 1
            in_gap = False
            pi += 1
        else:
            score += GAP_EXTENSION if in_gap else GAP_START
            in_gap = True
            consecutive = first_bonus = 0
        prev = cur
    return score, tuple(positions)


def buffer_words(lines: 'list[str]', line: int, exclude: str, min_len: int = 3) -> 'list[str]':
    """Идентификаторы файла, ближние к строке каретки — первыми.

    Запасной список, пока сервера нет или он молчит: по нему хотя бы
    не перепечатывать имена, уже набранные в этом же файле.
    """
    seen: 'dict[str, int]' = {}
    for i, text in enumerate(lines):
        dist = abs(i - line)
        for word in _IDENT.findall(text):
            if len(word) >= min_len and word != exclude:
                known = seen.get(word)
                if known is None or dist < known:
                    seen[word] = dist
    return sorted(seen, key=lambda w: (seen[w], w))
