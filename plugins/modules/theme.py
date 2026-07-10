"""Палитры китов: цвет для каждой роли — токена подсветки и фона
строки диффа.

Палитра — data-файл config/palette/<имя>.conf («роль значение»
построчно); список тем — это файлы этой директории, отдельного
реестра нет. Тему выбирает `familiar enable --theme`: она пишет в
kitty.conf `env FAMILIAR_THEME=<имя>`, и kitty передаёт переменную
процессу kitten. Китен не должен падать из-за опечатки в конфиге:
неизвестное имя молча даёт DEFAULT, пропущенная или кривая роль
наследует цвет из палитры DEFAULT.

Значение роли — либо номер в 256-цветной палитре (int), либо
'#rrggbb'; hex превращается в kitty.fast_data_types.Color, который
styled() выводит как truecolor. Точные оттенки IDE иначе не
передать: 256-цветный куб их огрубляет.
"""

import os
import re
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from kitty.fast_data_types import Color


DEFAULT = 'ghostty'

# От __file__, а не от FAMILIAR_ROOT: у модулей пакета __file__ есть
# в обоих процессах kitty (в отличие от входных файлов китов), а
# переменную окружения kitty китенам не передаёт. Раскладка brew
# (libexec/{plugins,config}) повторяет раскладку репозитория.
PALETTE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'config', 'palette')

_HEX = re.compile(r'#[0-9a-f]{6}$', re.IGNORECASE)

_cache: 'dict[str, dict[str, int | str]]' = {}


def _scan() -> 'tuple[str, ...]':
    try:
        found = sorted(os.path.splitext(f)[0] for f in os.listdir(PALETTE_DIR)
                       if f.endswith('.conf'))
    except OSError:
        return (DEFAULT,)
    return (DEFAULT,) + tuple(n for n in found if n != DEFAULT)


NAMES = _scan()


def _parse(path: str) -> 'dict[str, int | str]':
    roles: 'dict[str, int | str]' = {}
    try:
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
    except OSError:
        return roles
    for line in lines:
        parts = line.split()
        if len(parts) < 2 or parts[0].startswith('#'):
            continue
        role, value = parts[0], parts[1]
        if value.isdigit() and int(value) <= 255:
            roles[role] = int(value)
        elif _HEX.match(value):
            roles[role] = value
    return roles


def _raw(name: str) -> 'dict[str, int | str]':
    if name not in _cache:
        _cache[name] = _parse(os.path.join(PALETTE_DIR, f'{name}.conf'))
    return _cache[name]


def theme_name() -> str:
    return os.environ.get('FAMILIAR_THEME', '').strip().lower() or DEFAULT


def _rgb(spec: str) -> 'Color':
    # ленивый импорт: модуль должен импортироваться и без kitty
    # (тесты, дефолтная int-палитра)
    from kitty.fast_data_types import Color
    return Color(int(spec[1:3], 16), int(spec[3:5], 16), int(spec[5:7], 16))


def palette(name: 'str | None' = None) -> 'dict[str, int | Color]':
    """Роль токена → цвет для styled().

    Набор ролей задаёт палитра DEFAULT; лишние роли в файле темы
    игнорируются.
    """
    base = _raw(DEFAULT)
    raw = dict(base)
    chosen = name or theme_name()
    if chosen != DEFAULT:
        raw.update((role, v) for role, v in _raw(chosen).items() if role in base)
    return {role: _rgb(v) if isinstance(v, str) else v for role, v in raw.items()}
