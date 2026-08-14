"""Каталог снимков: создание, публикация последнего, ротация.

Снимок — состояние, а не кэш: чистка ~/.cache не должна уносить
единственную копию закрытых табов, поэтому XDG_STATE_HOME.

Актуальный снимок доступен по стабильному пути last/session
(симлинк) — его запекают в kitty.conf startup_session и хоткей
goto_session, и он не должен меняться от снимка к снимку.
"""

import os
import re
import shutil


SNAPSHOTS = 'snapshots'
LAST = 'last'
SESSION_NAME = 'session.kitty-session'

# Сколько снимков держать. Ротация тут не украшение: без неё таймер
# через минуту перезапишет снимок уже без случайно закрытого таба, и
# откатиться будет некуда.
KEEP = 10

_NUM_RE = re.compile(r'^\d{4}$')


def state_dir() -> str:
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'familiar', 'restore')


def last_session_path() -> str:
    return os.path.join(state_dir(), LAST, SESSION_NAME)


def scrollback_path(snapshot_dir: str, token: str) -> str:
    return os.path.join(snapshot_dir, f'sb-{token}.txt')


def _numbers(root: str) -> list[int]:
    try:
        names = os.listdir(root)
    except OSError:
        return []
    return sorted(int(n) for n in names if _NUM_RE.match(n))


def new_snapshot_dir() -> str:
    """Создать папку под очередной снимок.

    Права 0700 на всё дерево: в снимке лежит вывод терминала.
    """
    root = os.path.join(state_dir(), SNAPSHOTS)
    os.makedirs(root, mode=0o700, exist_ok=True)
    nxt = (_numbers(root) or [0])[-1] + 1
    # Второй инстанс kitty пишет в тот же каталог — занятый номер это
    # не ошибка, а гонка: берём следующий свободный.
    for n in range(nxt, nxt + 100):
        path = os.path.join(root, f'{n:04d}')
        try:
            os.mkdir(path, mode=0o700)
        except FileExistsError:
            continue
        return path
    raise OSError(f'no free snapshot slot in {root}')


def write_text(path: str, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(text)


def publish(snapshot_dir: str) -> None:
    """Указать last на снимок и убрать лишние.

    Симлинк переставляется через rename — читатель (startup_session на
    старте kitty) видит либо прежний снимок, либо новый, но никогда
    полузаписанный.
    """
    link = os.path.join(state_dir(), LAST)
    tmp = link + '.tmp'
    if os.path.islink(tmp) or os.path.exists(tmp):
        os.remove(tmp)
    os.symlink(snapshot_dir, tmp)
    try:
        os.replace(tmp, link)
    except OSError:
        # link — не симлинк, а настоящая папка (наследие прошлой
        # версии или ручная правка): rename поверх неё не пройдёт.
        shutil.rmtree(link, ignore_errors=True)
        os.replace(tmp, link)
    rotate()


def rotate(keep: int = KEEP) -> None:
    root = os.path.join(state_dir(), SNAPSHOTS)
    numbers = _numbers(root)
    for n in numbers[:max(0, len(numbers) - keep)]:
        shutil.rmtree(os.path.join(root, f'{n:04d}'), ignore_errors=True)
