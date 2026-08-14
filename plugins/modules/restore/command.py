"""Что запускать в восстановленном окне.

Выбираем сами, а не штатным kitty --use-foreground-process: тот
перезапускает любой процесс окна, вплоть до случайно попавшего в
снимок `rm`. Здесь claude поднимается своей же сессией, интерактивные
программы — по белому списку, всё прочее не перезапускается вовсе.
"""

import os
import shlex


# Перезапуск безопасен: программа интерактивная, сама ничего не меняет
# и в худшем случае откроет пустой буфер.
SAFE_PROGRAMS = frozenset({
    'bat', 'btop', 'emacs', 'helix', 'htop', 'hx', 'k9s', 'lazydocker',
    'lazygit', 'less', 'man', 'more', 'nano', 'nvim', 'ranger', 'tig',
    'top', 'vi', 'vim', 'yazi',
})


def safe_program(cmdlines: list[list[str]]) -> 'list[str] | None':
    for cmdline in cmdlines:
        if cmdline and os.path.basename(cmdline[0]) in SAFE_PROGRAMS:
            return list(cmdline)
    return None


def restore_command(session_id: 'str | None' = None,
                    program: 'list[str] | None' = None,
                    scrollback: 'str | None' = None) -> str:
    """Команда для KITTY_SI_RUN_COMMAND_AT_STARTUP; пустая — не надо.

    Уходит в eval уже поднятого интерактивного шелла, поэтому PATH и
    переменные из ~/.zshrc на месте — claude ищется как обычно, без
    трюков с login-шеллом (ср. session.py, где шелл делает exec).

    Взаимоисключающе, по убыванию точности: своя сессия claude важнее
    экрана (переписку claude нарисует сам), запущенная программа —
    тоже: htop и ему подобные затирают напечатанный скроллбэк первым
    же кадром.
    """
    if session_id:
        return 'claude --resume ' + shlex.quote(session_id)
    if program:
        return shlex.join(program)
    if scrollback:
        return 'cat ' + shlex.quote(scrollback)
    return ''
