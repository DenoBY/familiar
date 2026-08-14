"""Подготовка текста экрана к записи в снимок.

Восстановленное окно печатает этот текст обычным `cat`, поэтому из
ANSI оставляем только цвет и начертание (CSI … m). Всё остальное
исполнилось бы заново в живом терминале: alt-screen, очистка экрана,
запросы, ответ на которые ушёл бы в stdin шелла.
"""

import re


# Хвост, который восстанавливаем. Дефолтный scrollback_lines в kitty —
# 2000 строк; больше и не нужно, а снимок не должен пухнуть.
MAX_LINES = 2000
MAX_BYTES = 512 * 1024

# Экран, где не набрали ни одной команды, — это один промпт: печатать
# его при восстановлении бессмысленно, шелл нарисует свой, и выйдет
# два промпта подряд. Три строки — промпт, команда, её вывод.
MIN_LINES = 3

_SGR_RE = re.compile(r'\x1b\[[0-9;:]*m')
_ESCAPE_RE = re.compile(
    r'\x1b\[[0-?]*[ -/]*[@-~]'              # CSI
    r'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)'   # OSC
    r'|\x1b[@-Z\\-_0-9#()*+./=>]'           # прочие escape
)
# \x1b исключён: SGR-последовательности переживают эту чистку.
_CTRL_RE = re.compile('[\x00-\x08\x0b-\x1a\x1c-\x1f\x7f]')


def _sgr_only(text: str) -> str:
    return _ESCAPE_RE.sub(
        lambda m: m.group(0) if _SGR_RE.fullmatch(m.group(0)) else '', text)


def prepare(text: str, max_lines: int = MAX_LINES, max_bytes: int = MAX_BYTES,
            min_lines: int = MIN_LINES) -> str:
    """Хвост экрана в виде, который безопасно вывести через cat.

    Пустая строка — восстанавливать нечего.
    """
    lines = _CTRL_RE.sub('', _sgr_only(text)).splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if sum(1 for line in lines if line.strip()) < min_lines:
        return ''
    out = '\n'.join(lines[-max_lines:])
    data = out.encode('utf-8')
    if len(data) > max_bytes:
        # Резать по границе строки: обрубок первой строки в выводе
        # выглядит мусором, а лишний escape-хвост ещё и красит остаток.
        out = data[-max_bytes:].decode('utf-8', 'ignore').partition('\n')[2]
        if not out:
            return ''
    # Сброс атрибутов: незакрытый цвет из последней строки протёк бы
    # на промпт восстановленного шелла.
    return out + '\x1b[m\n'
