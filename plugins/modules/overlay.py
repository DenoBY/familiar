"""Обвязка кита-оверлея: маркировка окна для взаимного вытеснения
(см. kittens.conf), прокрутка TUI-цикла и возврат layout после
полноэкранного оверлея.
"""

import base64
import sys

from kittens.tui.handler import Handler
from kittens.tui.loop import Loop


OVERLAY_VAR = 'cc_plugin'

STACK = 'stack'


def mark_overlay(name: str) -> None:
    """Пометить своё окно kitty user-var'ом cc_plugin=<name>
    (OSC 1337 SetUserVar) и дать окну заголовок по имени кита.

    Значение кодируется base64 (требование протокола). Вызывать
    в начале main() до старта TUI-Loop. Метка и заголовок исчезают
    вместе с окном kitten — чистить не нужно.
    """
    val = base64.b64encode(name.encode()).decode('ascii')
    sys.stdout.write(f'\033]1337;SetUserVar={OVERLAY_VAR}={val}\007')
    # Без своего заголовка окно kitten показывается в табе как «kitty».
    sys.stdout.write(f'\033]2;{name.title()}\007')
    sys.stdout.flush()


def run_loop(handler: Handler) -> None:
    """Прокрутить TUI-цикл кита, пережив обрыв кадра на выходе.

    Кадр уходит в tty через os.writev: тот пишет сколько влезло и
    режет буфер по байтам, а недописанный хвост kitty в конце
    Loop.loop декодирует строгим utf-8 (kittens/tui/loop.py, 0.47).
    Выход посреди кадра с многобайтовыми символами валит кит
    UnicodeDecodeError'ом уже после того, как выбор сделан, и
    действие теряется. Хвоста не жаль: экран всё равно закрывается.
    """
    try:
        Loop().loop(handler)
    except UnicodeDecodeError:
        pass


def _base_name(layout: 'str | None') -> str:
    # Имя layout в kitty может нести опции ('splits:split_axis=…') —
    # сравниваем по базовой части.
    return (layout or '').partition(':')[0]


def restore_layout(boss, target_window_id: int) -> None:
    """Вернуть layout таба после закрытия оверлея.

    Обратная часть `goto_layout stack` из map-цепочки familiar
    (кит открывается во весь таб). Вызывать первым делом в
    handle_result — процесс kitty, есть Boss.
    """
    w = boss.window_id_map.get(target_window_id)
    tab = w.tabref() if w is not None else None
    if tab is None:
        return
    if tab.current_layout.name != STACK:
        return
    # Имя прежнего layout kitty наружу не отдаёт — только приватный
    # _last_used_layout (tabs.py: _set_current_layout).
    prev = tab._last_used_layout
    if not prev or _base_name(prev) == STACK:
        # 'stack' в _last_used_layout — след повторного goto_layout
        # stack: при вытеснении кита китом убитый оверлей не получает
        # handle_result, и прежнее имя затёрто. Возвращаем первый
        # не-stack layout из включённых.
        prev = next((name for name in tab.enabled_layouts
                     if _base_name(name) != STACK), None)
    if prev:
        tab.goto_layout(prev)
