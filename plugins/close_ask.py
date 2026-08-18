#!/usr/bin/env python3
"""
close_ask — kitten для kitty.

Экран вопроса «закрыть панель?» — вторая фаза кита close.py: тот в
процессе kitty решает, нужен ли вопрос, и передаёт готовые строки
аргументами. Сам экран ничего о процессах окна не знает; что делать
с панелью после «да» — забота modules.close.pane.

Оверлей открывается в размер панели, а не во весь таб: дело касается
одной панели, и layout трогать незачем (ср. цепочку `goto_layout
stack` у остальных китов).

Подключение — через close.py, см. его docstring.
"""

import os
import sys

from kittens.tui.handler import result_handler
from kittens.tui.loop import Loop


# Пакет modules лежит рядом с этим файлом; подробности — в session.py.
if '__file__' in globals():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.close.pane import close as close_pane
from modules.close.screen import CloseScreen
from modules.overlay import mark_overlay


def main(args: list[str]) -> dict:
    # Метка cc_plugin нужна и для ⌘W: повторное нажатие в оверлее
    # familiar.conf превращает в ⌃c, то есть в отмену вопроса.
    mark_overlay('close')
    handler = CloseScreen(*args[1:3])
    Loop().loop(handler)
    # Не None даже при отказе: без результата kitty не зовёт
    # handle_result, а закрыть панель можно только оттуда.
    return {'action': 'close' if handler.confirmed else 'cancel'}


@result_handler()
def handle_result(args: list[str], result: 'dict | None',
                  target_window_id: int, boss) -> None:
    if not result or result.get('action') != 'close':
        return
    window = boss.window_id_map.get(target_window_id)
    if window is not None:
        close_pane(boss, window)


if __name__ == '__main__':
    main(sys.argv)
