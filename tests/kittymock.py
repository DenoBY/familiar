"""Мок окружения kitty для запуска тестов вне самого kitty.

Импорт этого модуля регистрирует поддельные пакеты kittens.*/kitty.* в sys.modules
и добавляет папку plugins/ в sys.path — после этого review/session и modules.*
импортируются обычным образом. styled здесь — тождество (возвращает текст как есть),
поэтому вывод хендлеров детерминирован и его можно проверять по подстрокам.
"""

import os
import sys
import types

_TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(_TESTS)
PLUGINS = os.path.join(REPO, 'plugins')


def styled(text, **kwargs):
    return text


class Handler:
    def on_mouse_event(self, ev):
        pass

    def write(self, *a, **k):
        pass

    def flush(self):
        pass


def result_handler(*a, **k):
    def deco(f):
        return f
    return deco


class Loop:
    def loop(self, handler):
        pass


class MouseButton:
    WHEEL_UP = 'WHEEL_UP'
    WHEEL_DOWN = 'WHEEL_DOWN'
    LEFT = 'LEFT'


class MouseTracking:
    buttons_only = 'buttons_only'
    buttons_and_drag = 'buttons_and_drag'


class EventType:
    PRESS = 'PRESS'
    REPEAT = 'REPEAT'
    RELEASE = 'RELEASE'
    MOVE = 'MOVE'


def _install():
    if 'kittens' in sys.modules:
        return
    kittens = types.ModuleType('kittens')
    tui = types.ModuleType('kittens.tui')
    handler = types.ModuleType('kittens.tui.handler')
    loop = types.ModuleType('kittens.tui.loop')
    operations = types.ModuleType('kittens.tui.operations')
    kitty = types.ModuleType('kitty')
    key_encoding = types.ModuleType('kitty.key_encoding')

    kittens.tui = tui
    tui.handler = handler
    tui.loop = loop
    tui.operations = operations
    kitty.key_encoding = key_encoding

    handler.Handler = Handler
    handler.result_handler = result_handler
    loop.Loop = Loop
    loop.MouseButton = MouseButton
    loop.EventType = EventType
    operations.styled = styled
    operations.MouseTracking = MouseTracking
    key_encoding.EventType = EventType

    for name, mod in [
        ('kittens', kittens), ('kittens.tui', tui),
        ('kittens.tui.handler', handler), ('kittens.tui.loop', loop),
        ('kittens.tui.operations', operations),
        ('kitty', kitty), ('kitty.key_encoding', key_encoding),
    ]:
        sys.modules[name] = mod

    if PLUGINS not in sys.path:
        sys.path.insert(0, PLUGINS)


_install()


# ─────────────── хелперы для тестов TUI-хендлеров ───────────────

class Size:
    def __init__(self, rows, cols):
        self.rows = rows
        self.cols = cols


class NoopCmd:
    def __getattr__(self, name):
        return lambda *a, **k: None


class KeyEvent:
    def __init__(self, key=None, type=EventType.PRESS, matches=()):
        self.key = key
        self.type = type
        self._matches = set(matches)

    def matches(self, spec):
        return spec in self._matches


class MouseEvent:
    def __init__(self, cell_x=0, cell_y=0, buttons=None):
        self.cell_x = cell_x
        self.cell_y = cell_y
        self.buttons = buttons


def wire(handler, rows=40, cols=120):
    """Подключить к хендлеру мок-экран/вывод: screen_size, cmd, буфер print, quit_loop."""
    handler.screen_size = Size(rows, cols)
    handler.cmd = NoopCmd()
    handler.out = []
    handler.print = lambda *a, **k: handler.out.append(a[0] if a else '')
    handler.quits = []
    handler.quit_loop = lambda code=0: handler.quits.append(code)
    return handler


def draw_text(handler):
    """Весь текст, «нарисованный» через print, одной строкой — для проверок по подстроке."""
    return '\n'.join(str(x) for x in handler.out)
