"""Раскладка ЙЦУКЕН → QWERTY: чтобы шорткаты китов срабатывали и
на русской раскладке (физическая клавиша j даёт «о», k — «л» и
т.п.).

Общий для всех китов (review/log/session), поэтому лежит в
корне пакета modules рядом с overlay, а не внутри vcs/session —
чтобы session не зависел от «vcs» ради клавиатуры.
"""

_RU = 'йцукенгшщзхъфывапролджэячсмитьбю'
_EN = "qwertyuiop[]asdfghjkl;'zxcvbnm,."
LAYOUT = {r: e for r, e in zip(_RU, _EN)}
LAYOUT.update({r.upper(): e.upper() for r, e in zip(_RU, _EN)})


def to_latin(ch: str) -> str:
    return LAYOUT.get(ch, ch)


def ctrl_letter(text: str, in_bracketed_paste: bool = False) -> 'str | None':
    """Буква ctrl-сочетания, пришедшего C0-байтом ('\\x0f' → 'o').

    Терминальный конфиг мапит ctrl+<кириллица> в send_text C0-байта
    (config/keys/russian-ctrl.conf), поэтому на русской раскладке
    ctrl+буква приходит хендлеру текстом, а не key-событием. Вставка
    не в счёт: её C0 (\\n, \\t) — содержимое, а не хоткеи.
    """
    if not in_bracketed_paste and len(text) == 1 and '\x01' <= text <= '\x1a':
        return chr(ord(text) + 96)
    return None


# Все модификаторы KeyEvent, кроме лок-клавиш: сочетание совпадает,
# только когда зажаты ровно запрошенные (как в KeyEvent.matches) —
# иначе ctrl+alt+c срабатывал бы как ctrl+c.
_MODS = ('shift', 'alt', 'ctrl', 'super', 'hyper', 'meta')


def chord(key_event, mods: str, letter: str) -> bool:
    """Сочетание модификаторы+буква ('ctrl', 'super+shift'),
    независимо от раскладки.

    KeyEvent.matches('ctrl+o') сверяет символ буквально, поэтому на
    ЙЦУКЕН приходит «щ» и совпадения нет. Сверяем модификаторы и
    букву через LAYOUT.
    """
    if to_latin((key_event.key or '').lower()) != letter:
        return False
    want = set(mods.split('+'))
    return all(bool(getattr(key_event, m, False)) == (m in want) for m in _MODS)
