#!/usr/bin/env python3
"""
close — kitten для kitty.

Подтверждение закрытия панели вместо штатного вопроса kitty: тот
печатает argv[0] первого процесса переднего плана целиком, поэтому в
окне с claude показывает его потомков (caffeinate, MCP-сервер) с
полными путями.

UI у этого кита нет (no_ui): он выполняется сразу в процессе kitty,
где видно процессы окна, и решает — закрыть панель молча (она на
промпте шелла, как при ignore-shell у kitty) или показать вопрос.
Экран рисует отдельный кит close_ask.py: no_ui исключает main вовсе,
поэтому решатель и экран — разные точки входа. Путь к экрану
приходит аргументом, `__file__` в exec-пути кита нет.

Подключение (его пишет `familiar enable`):
    map cmd+w kitten /path/plugins/close.py /path/plugins/close_ask.py
"""

import os
import sys

from kittens.tui.handler import result_handler


# Пакет modules лежит рядом с этим файлом; подробности — в session.py.
if '__file__' in globals():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.close.pane import claude_session
from modules.close.target import describe


def _cmdline(window) -> list[str]:
    try:
        return list(window.child.foreground_cmdline or ())
    except (AttributeError, OSError):
        return []


def main(args: list[str]) -> None:
    """Не вызывается: при no_ui процесса кита нет вовсе.

    Нужна для загрузки — kitty достаёт `main` из модуля любого кита и
    падает на `Error: 'main'` ещё до чтения result_handler.
    """


@result_handler(no_ui=True)
def handle_result(args: list[str], result: 'dict | None',
                  target_window_id: int, boss) -> None:
    window = boss.window_id_map.get(target_window_id)
    if window is None:
        return
    if not window.has_running_program:
        boss.mark_window_for_close(window)
        return
    ask = args[1] if len(args) > 1 else ''
    if not ask:
        # Маппинг без пути к экрану: лучше вопрос kitty, чем клавиша,
        # которая молча ничего не делает.
        boss.close_window_with_confirmation(True)
        return
    label, hint = describe(claude_session(window), _cmdline(window))
    boss.kitten(ask, label, hint)
