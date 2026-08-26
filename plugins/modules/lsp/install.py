"""Где взять бинарь сервера и что сказать, если его нет.

Кит ничего не ставит сам: он показывает готовую команду, а установку
делает `familiar lsp install`. Так пакетами управляет пользователь, а
не оверлей поверх его терминала.
"""

import glob
import json
import os
import shutil
import subprocess

from .registry import ServerSpec, bin_dir, lsp_home, server_home


# keg-only формулы (llvm с его clangd) в PATH не попадают, но лежат
# предсказуемо — иначе пришлось бы зашивать префикс brew в конфиг
_OPT_GLOBS = ('/opt/homebrew/opt/*/bin', '/usr/local/opt/*/bin')

# kitty, запущенная из Dock, наследует системный PATH без homebrew, а
# npm-серверы — скрипты с `#!/usr/bin/env node`: без node в PATH они
# падают ещё до первого сообщения протокола
_RUNTIME_PATH = ('/opt/homebrew/bin', '/usr/local/bin',
                 '~/.local/bin', '~/.cargo/bin', '~/go/bin')


def find_binary(name: str) -> 'str | None':
    if os.path.sep in name:
        return name if os.access(name, os.X_OK) else None
    found = shutil.which(name)
    if found:
        return found
    found = os.path.join(bin_dir(), name)
    if os.access(found, os.X_OK):
        return found
    for pattern in _OPT_GLOBS:
        for path in sorted(glob.glob(os.path.join(pattern, name))):
            if os.access(path, os.X_OK):
                return path
    return None


def resolve(spec: ServerSpec) -> 'list[str] | None':
    """Готовая командная строка либо None, если сервер не установлен."""
    if not spec.argv:
        return None
    found = find_binary(spec.argv[0])
    return [found, *spec.argv[1:]] if found else None


def install_hint(spec: ServerSpec) -> str:
    """Одна строка для футера кита: чего нет и что набрать."""
    name = spec.argv[0] if spec.argv else spec.lang
    return f'{name} not installed — run: familiar lsp install {spec.lang}'


def install_command(spec: ServerSpec) -> 'list[str] | None':
    """Команда установки из поля `install` реестра."""
    if len(spec.install) < 2:
        return None
    manager, package = spec.install[0], spec.install[1]
    if manager == 'brew':
        return ['brew', 'install', package]
    if manager == 'npm':
        # -g кладёт исполняемые файлы в <prefix>/bin; без него они
        # прячутся в node_modules/.bin, где их никто не ищет
        return ['npm', 'install', '-g', '--prefix', server_home(), package]
    return None


def receipts_path() -> str:
    """Что и чем поставлено: как receipt.json у mason.nvim — иначе
    `status` гадал бы по наличию файлов.
    """
    return os.path.join(lsp_home(), 'receipts.json')


def read_receipts() -> dict:
    try:
        with open(receipts_path(), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_receipt(lang: str, entry: dict) -> None:
    data = read_receipts()
    data[lang] = entry
    path = receipts_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, sort_keys=True)
    except OSError:
        pass          # квитанция — удобство, а не условие работы


def install(spec: ServerSpec) -> 'tuple[bool, str]':
    """Поставить сервер командой из реестра: (успех, что произошло)."""
    command = install_command(spec)
    if command is None:
        manager = spec.install[0] if spec.install else ''
        if manager == 'xcode':
            return False, f'{spec.lang}: ships with Xcode — install Xcode tools'
        return False, f'{spec.lang}: no install recipe in the registry'
    if shutil.which(command[0]) is None:
        return False, f'{command[0]} not found — install it first'
    if command[0] == 'npm':
        os.makedirs(server_home(), exist_ok=True)
    try:
        done = subprocess.run(command)
    except OSError as e:
        return False, f'{spec.lang}: {e}'
    if done.returncode != 0:
        return False, f'{spec.lang}: {" ".join(command)} failed'
    write_receipt(spec.lang, {'package': spec.install[1],
                              'manager': spec.install[0]})
    return True, f'{spec.lang}: installed {spec.install[1]}'


def runtime_env(extra: 'dict[str, str] | None' = None) -> 'dict[str, str]':
    """Окружение для сервера: PATH, дополненный местами, где живут
    интерпретаторы и сами серверы.
    """
    env = dict(os.environ)
    # пустой элемент PATH означает текущий каталог, а cwd сервера —
    # просматриваемый репозиторий: файл `node` в нём выиграл бы у node
    present = [p for p in env.get('PATH', '').split(os.pathsep) if p]
    additions = [os.path.expanduser(p) for p in (*_RUNTIME_PATH, bin_dir())]
    tail = [p for p in additions if p not in present and os.path.isdir(p)]
    env['PATH'] = os.pathsep.join([*present, *tail]) if tail else env.get('PATH', '')
    env.update(extra or {})
    return env
