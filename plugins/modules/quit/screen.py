"""Экран подтверждения выхода: что закроется и вернётся ли.

Кнопки и разбор ввода — общий диалог китов (modules.confirm), чтобы
вопрос при выходе выглядел ровно так же, как вопрос при закрытии
оверлея. Своё здесь только содержимое над кнопками.
"""

import os

from kittens.tui.operations import styled

from ..confirm import ConfirmScreen
from ..session.data import running_sessions
from ..text import plural, truncate


# Больше в списке не нужно: экран о решении «выходить или нет», а не
# журнал сессий — остальные сворачиваются в счётчик.
MAX_LISTED = 8


def live_sessions() -> list[tuple[str, str]]:
    """Живые сессии Claude Code как (проект, статус)."""
    out = []
    for info in running_sessions().values():
        cwd = info.get('cwd') or ''
        name = os.path.basename(cwd.rstrip('/')) or cwd or '?'
        out.append((name, info.get('status') or 'idle'))
    return sorted(out)


def body(sessions: list[tuple[str, str]], width: int) -> list[tuple[str, str]]:
    """Строки над кнопками: (текст, оформленный текст)."""
    rows = [('Quit kitty?', styled('Quit kitty?', bold=True)), ('', '')]
    if sessions:
        head = plural(len(sessions), 'Claude Code session') + ' running:'
        rows.append((head, head))
        for name, status in sessions[:MAX_LISTED]:
            # Режем имя, а не собранную строку: статус должен остаться
            # целым, его отдельно гасим dim.
            name = truncate(name, max(8, width - len(status) - 5))
            rows.append((f'  {name} · {status}',
                         f'  {name} · ' + styled(status, dim=True)))
        hidden = len(sessions) - MAX_LISTED
        if hidden > 0:
            more = f'  … and {plural(hidden, "more session")}'
            rows.append((more, more))
        rows.append(('', ''))
    tail = 'Windows and tabs come back on the next start.'
    rows.append((tail, styled(tail, dim=True)))
    return rows


class QuitScreen(ConfirmScreen):
    """Вопрос на весь экран; ответ читает handle_result кита."""

    def __init__(self) -> None:
        super().__init__()
        self.sessions: list[tuple[str, str]] = []

    def initialize(self) -> None:
        self.sessions = live_sessions()
        super().initialize()

    def confirm_rows(self) -> list[tuple[str, str]]:
        return body(self.sessions, self.screen_size.cols - 4)
