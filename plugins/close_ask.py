#!/usr/bin/env python3
"""
close_ask — kitten для kitty.

Экран вопроса «закрыть панель?» — вторая фаза кита close.py: тот в
процессе kitty решает, нужен ли вопрос, и передаёт готовые строки
аргументами. Сам ничего о процессах окна не знает.

Оверлей открывается в размер панели, а не во весь таб: закрывается
именно панель, и layout трогать незачем (ср. цепочку `goto_layout
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
    if result and result.get('action') == 'close':
        boss.mark_window_for_close(target_window_id)


if __name__ == '__main__':
    main(sys.argv)
