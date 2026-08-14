#!/usr/bin/env python3
"""
quit — kitten для kitty.

Подтверждение выхода во весь таб: штатный вопрос kitty рисуется
оверлеем над активным окном и в сплите выходит размером со сплит,
хотя закрывает весь терминал.

Перед выходом снимает состояние окон (modules.restore) — kitty не
зовёт watcher'ы при завершении, и без этого терялось бы всё, что
случилось после последнего фонового снимка.

Подключение в ~/.config/kitty/kitty.conf (его пишет familiar enable):
    map cmd+q combine @ goto_layout stack @ kitten /path/plugins/quit.py
"""

import os
import sys

from kittens.tui.handler import result_handler
from kittens.tui.loop import Loop
from kitty.fast_data_types import (
    IMPERATIVE_CLOSE_REQUESTED,
    set_application_quit_request,
)


# Пакет modules лежит рядом с этим файлом; подробности — в session.py.
if '__file__' in globals():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.overlay import mark_overlay, restore_layout
from modules.quit.screen import QuitScreen
from modules.restore.snapshot import write_snapshot


def main(args: list[str]) -> dict:
    mark_overlay('quit')
    handler = QuitScreen()
    Loop().loop(handler)
    # Не None даже при отказе: без результата kitty не зовёт
    # handle_result, а layout вернуть надо всегда.
    return {'action': 'quit' if handler.confirmed else 'cancel'}


@result_handler()
def handle_result(args: list[str], result: 'dict | None',
                  target_window_id: int, boss) -> None:
    restore_layout(boss, target_window_id)
    if not result or result.get('action') != 'quit':
        return
    try:
        write_snapshot(boss)
    except OSError:
        pass   # снимок не важнее выхода: не смогли записать — выходим
    # Выход без второго вопроса: штатный диалог kitty уже заменён этим
    # китом, и confirm_os_window_close спросил бы поверх ответа.
    set_application_quit_request(IMPERATIVE_CLOSE_REQUESTED)


if __name__ == '__main__':
    main(sys.argv)
