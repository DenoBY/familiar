#!/usr/bin/env python3
"""restore — watcher kitty: ведёт снимок состояния окон.

Не kitten: kitty грузит этот файл в своём процессе (runpy.run_path)
по опции `watcher` в kitty.conf и зовёт функции-события. Поэтому
здесь только события, а логика — в пакете modules.restore.

Подключение (его пишет `familiar enable`):
    watcher /path/to/plugins/watchers/restore.py
"""

import os
import sys


# Пакет modules лежит на уровень выше. В отличие от kitten'ов, папку
# watcher'а kitty в sys.path не кладёт, зато __file__ здесь настоящий:
# runpy.run_path задаёт его всегда.
if '__file__' in globals():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kitty.boss import get_boss
from kitty.fast_data_types import add_timer

from modules.restore.snapshot import Snapshotter


# Как часто снимать состояние в фоне. Реже — дешевле, но тем больше
# работы теряется между снимками.
INTERVAL = 45.0

_snapshotter = Snapshotter()


def _tick(timer_id: int) -> None:
    boss = get_boss()
    if boss is not None:
        _snapshotter.take(boss)


def on_load(boss, data: dict) -> None:
    add_timer(_tick, INTERVAL, True)


def on_close(boss, window, data: dict) -> None:
    # Закрываемое окно на этот момент ещё в состоянии kitty, поэтому
    # снимок ловит его последним — из него его и вернут.
    _snapshotter.take(boss, force=True)


def on_quit(boss, window, data: dict) -> None:
    # Сигнатура — как у on_close, с окном: kitty зовёт событие для
    # каждого окна, и (boss, data) роняет её выход с TypeError.
    _snapshotter.take(boss, force=True)
