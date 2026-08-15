"""Что работает в панели — строкой для вопроса о закрытии."""

import os


CLAUDE_HINT = 'The conversation is saved — reopen it from the session list.'

# Сколько аргументов команды оставить: `nvim src/main.py` объясняет,
# что закрывается, а полная строка сборки — уже стена текста.
MAX_ARGS = 3


def program_label(cmdline: list[str]) -> str:
    """Команда без пути к бинарнику: `/opt/bin/nvim` → `nvim`."""
    if not cmdline:
        return ''
    head = os.path.basename(cmdline[0]) or cmdline[0]
    return ' '.join([head, *cmdline[1:MAX_ARGS + 1]])


def claude_label(info: dict) -> str:
    cwd = info.get('cwd') or ''
    project = os.path.basename(cwd.rstrip('/')) or cwd or '?'
    return f'claude · {project} · {info.get("status") or "idle"}'


def describe(session: 'dict | None', cmdline: list[str]) -> tuple[str, str]:
    """Строка «что закрывается» и подсказка под ней.

    Сессия claude важнее команды: в окне с ней foreground-процессом
    может оказаться любой потомок (caffeinate, MCP-сервер), а человеку
    нужно знать проект и статус.
    """
    if session is not None:
        return claude_label(session), CLAUDE_HINT
    return program_label(cmdline), ''
