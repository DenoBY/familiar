"""Правка session-файла, сериализованного самой kitty.

Раскладку (OS-окна, табы, сплиты с их пропорциями, layout, фокус)
собирает kitty — повторить это руками нельзя, поэтому её вывод берём
как основу и дописываем только своё: команду запуска в строку нужного
окна. Окно узнаём по токену, который снимок кладёт в user var перед
сериализацией, — kitty сама выводит его как --var.

Строка kitty выглядит так:

    launch 'kitty-unserialize-data={"id": 1}' --var=cc_restore=w1 zsh -l
"""

import shlex


VAR = 'cc_restore'
RUN_ENV = 'KITTY_SI_RUN_COMMAND_AT_STARTUP'

_VAR_PREFIX = f'--var={VAR}='
_CWD_PREFIX = '--cwd='
_UNSERIALIZE_PREFIX = 'kitty-unserialize-data='


def _split(line: str) -> list[str]:
    try:
        return shlex.split(line)
    except ValueError:
        return []


def _token(args: list[str]) -> 'str | None':
    for arg in args:
        if arg.startswith(_VAR_PREFIX):
            return arg[len(_VAR_PREFIX):]
    return None


def _rebuild(line: str, args: list[str]) -> str:
    indent = line[:len(line) - len(line.lstrip())]
    return indent + shlex.join(args)


def _patch_line(line: str, patches: dict[str, dict]) -> str:
    original = _split(line)
    if not original or original[0] != 'launch':
        return line
    token = _token(original)
    patch = patches.get(token) if token else None

    # Своё окружение из прошлого снимка kitty сериализует обратно:
    # окно помнит, с каким --env его запустили. Без чистки команды
    # копились бы, и восстановление выполняло устаревшую (например,
    # cat уже сротированного дампа).
    args = [a for a in original if not a.startswith(f'--env={RUN_ENV}=')]
    if not patch:
        return _rebuild(line, args) if len(args) != len(original) else line

    # Свой токен в восстановленное окно не тащим: он нужен ровно на
    # время сериализации, а снимок следующего запуска проставит новый.
    args = [a for a in args if not a.startswith(_VAR_PREFIX)]
    cwd = patch.get('cwd')
    if cwd:
        args = [a for a in args if not a.startswith(_CWD_PREFIX)]

    # Флаги обязаны стоять до команды окна (…zsh -l), а служебный
    # аргумент kitty остаётся первым — он позиционный.
    at = 1
    if len(args) > at and args[at].startswith(_UNSERIALIZE_PREFIX):
        at += 1

    # Собственную команду окна отбрасываем: она перекрыла бы нашу.
    # Через RUN_ENV команду выполняет shell integration, а её в окне с
    # командой нет — `zsh -c …` не проходит интерактивную инициализацию.
    # Так окно кита session (шелл с `exec claude --continue`) вернётся
    # своей же сессией по id, а не последней в проекте.
    end = at
    while end < len(args) and args[end].startswith('-'):
        end += 1
    del args[end:]

    extra = [f'{_CWD_PREFIX}{cwd}'] if cwd else []
    extra.append(f'--env={RUN_ENV}={patch["run"]}')
    args[at:at] = extra
    return _rebuild(line, args)


def apply_patches(lines: list[str], patches: dict[str, dict]) -> list[str]:
    """Вписать в строки launch команду запуска (и cwd, если задан).

    patches: {токен: {'run': команда, 'cwd': папка или None}}.
    """
    return [_patch_line(line, patches) for line in lines]
