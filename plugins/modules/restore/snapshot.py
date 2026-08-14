"""Снимок состояния kitty: обход окон, дампы экрана, session-файл.

Единственный модуль пакета, который работает с объектами kitty
(Boss/Window) — остальное чистые функции, тестируемые вне терминала.

Состояние читается и записывается в два приёма: сборка (`build`)
только читает, запись (`write`) создаёт папку снимка. Между ними
Snapshotter сверяет отпечаток, чтобы не писать на диск одно и то же
каждые сорок пять секунд.
"""

import hashlib
import os
import time

from ..session.data import parent_pids, running_sessions, session_id_for_pid
from . import store
from .command import restore_command, safe_program
from .scrollback import prepare
from .sessionfile import VAR, apply_patches


# Как часто снимать по таймеру. Закрытие окна и выход снимают сразу,
# минуя интервал.
MIN_INTERVAL = 20.0


def _cmdlines(window) -> list[list[str]]:
    try:
        processes = window.child.foreground_processes
    except (AttributeError, OSError):
        return []
    return [list(p['cmdline']) for p in processes if p.get('cmdline')]


def _session_id(window, running: dict[str, dict],
                parents: dict[int, int]) -> 'str | None':
    if not running:
        return None
    try:
        pid = window.child.pid
    except (AttributeError, OSError):
        return None
    return session_id_for_pid(pid, running, parents) if pid else None


def _scrollback(window) -> 'str | None':
    try:
        return prepare(window.as_text(as_ansi=True, add_history=True)) or None
    except (AttributeError, OSError, ValueError):
        return None


def _window_state(window, running: dict[str, dict],
                  parents: dict[int, int]) -> 'dict | None':
    """Что окно даст снимку, или None — если ничего.

    Экран снимаем, только если он и правда пойдёт в дело: своя сессия
    claude и перезапускаемая программа рисуют окно сами.
    """
    sid = _session_id(window, running, parents)
    program = None if sid else safe_program(_cmdlines(window))
    scrollback = None if sid or program else _scrollback(window)
    if not (sid or program or scrollback):
        return None
    # Папку claude-окна берём из реестра сессий, а не у kitty: окно с
    # claude не проходит через промпт, cwd ему неоткуда сообщить, и
    # kitty падает на эвристику «папка самого нового процесса» — рядом
    # с Claude Code это лотерея (ср. splits.conf).
    return {'session_id': sid, 'program': program, 'scrollback': scrollback,
            'cwd': running[sid].get('cwd') if sid else None}


def _digest(lines: list[str], states: dict[str, dict]) -> str:
    parts = list(lines)
    for token in sorted(states):
        state = states[token]
        parts += [token, str(state['session_id']), str(state['program']),
                  state['scrollback'] or '']
    return hashlib.sha256('\0'.join(parts).encode('utf-8')).hexdigest()


def build(boss) -> 'dict | None':
    """Прочитать состояние kitty, ничего не записывая.

    None — когда снимать нечего: без окон снимок затёр бы прошлый
    пустышкой, а именно он и нужен после случайного закрытия.
    """
    windows = [w for w in boss.all_windows if w is not None]
    if not windows:
        return None
    running = running_sessions()
    parents = parent_pids() if running else {}

    states, marked = {}, []
    for window in windows:
        state = _window_state(window, running, parents)
        if state is None:
            continue
        token = f'w{window.id}'
        states[token] = state
        window.set_user_var(VAR, token)
        marked.append(window)

    lines = list(boss.serialize_state_as_session())
    for window in marked:
        window.set_user_var(VAR, None)
    return {'lines': lines, 'states': states,
            'digest': _digest(lines, states)}


def write(snapshot: dict) -> str:
    """Записать собранное состояние; путь session-файла."""
    snapshot_dir = store.new_snapshot_dir()
    patches = {}
    for token, state in snapshot['states'].items():
        dump = None
        if state['scrollback']:
            dump = store.scrollback_path(snapshot_dir, token)
            store.write_text(dump, state['scrollback'])
        run = restore_command(session_id=state['session_id'],
                              program=state['program'], scrollback=dump)
        if run:
            patches[token] = {'run': run, 'cwd': state['cwd']}

    path = os.path.join(snapshot_dir, store.SESSION_NAME)
    text = '\n'.join(apply_patches(snapshot['lines'], patches))
    store.write_text(path, text.rstrip('\n') + '\n')
    store.publish(snapshot_dir)
    return path


def write_snapshot(boss) -> 'str | None':
    snapshot = build(boss)
    return write(snapshot) if snapshot else None


class Snapshotter:
    """Снимки с ограничением частоты — состояние для watcher'а."""

    def __init__(self, min_interval: float = MIN_INTERVAL) -> None:
        self.min_interval = min_interval
        # None, а не 0: монотонные часы отсчитывают от старта
        # процесса, и нулём первый снимок откладывался бы на
        # min_interval после запуска kitty.
        self._last: 'float | None' = None
        self._digest: 'str | None' = None

    def take(self, boss, force: bool = False) -> 'str | None':
        now = time.monotonic()
        if not force and self._last is not None and now - self._last < self.min_interval:
            return None
        self._last = now
        snapshot = build(boss)
        # Неизменное состояние не переписываем: иначе фоновый таймер
        # круглые сутки гонял бы на диск одно и то же, а ротация
        # вымывала бы снимки, которые ещё могут пригодиться.
        if snapshot is None or snapshot['digest'] == self._digest:
            return None
        self._digest = snapshot['digest']
        return write(snapshot)
