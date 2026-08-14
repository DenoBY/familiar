"""Экран подтверждения выхода: что закроется и вернётся ли.

Кнопки и разбор ввода — общий диалог китов (modules.confirm), чтобы
вопрос при выходе выглядел ровно так же, как вопрос при закрытии
оверлея. Своё здесь только содержимое над кнопками.
"""

import os

from kittens.tui.handler import Handler
from kittens.tui.operations import MouseTracking, styled

from ..confirm import ConfirmQuit
from ..draw import AtomicDraw
from ..keylayout import chord, ctrl_letter
from ..pointer import PointerCursor
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


class QuitScreen(ConfirmQuit, AtomicDraw, PointerCursor, Handler):
    """Вопрос на весь экран; ответ читает handle_result кита."""

    mouse_tracking = MouseTracking.full

    def __init__(self) -> None:
        super().__init__()
        self.confirmed = False
        self.sessions: list[tuple[str, str]] = []

    def initialize(self) -> None:
        self.cmd.set_cursor_visible(False)
        self.sessions = live_sessions()
        self.start_quit_confirm()

    def finalize(self) -> None:
        self.cmd.set_cursor_visible(True)
        self.reset_pointer()

    def confirm_rows(self) -> list[tuple[str, str]]:
        return body(self.sessions, self.screen_size.cols - 4)

    def _confirm_done(self, yes: bool) -> None:
        self.confirmed = yes
        self.quit_loop(0)

    # Ввод: ⌃c у оверлеев значит «закрыть кит», здесь бы значил «выйти
    # из kitty» — для аварийной клавиши слишком много власти.
    def on_key(self, key_event) -> None:
        if chord(key_event, 'ctrl', 'c'):
            self._confirm_done(False)
            return
        self.confirm_key(key_event)

    def on_text(self, text: str, in_bracketed_paste: bool = False) -> None:
        if ctrl_letter(text, in_bracketed_paste) == 'c':
            self._confirm_done(False)
            return
        self.confirm_text(text)

    def on_mouse_event(self, ev) -> None:
        self.confirm_click(ev)

    def _wanted_pointer(self, ev) -> 'str | None':
        return self.confirm_pointer(ev)

    def on_interrupt(self) -> None:
        self._confirm_done(False)

    def on_eot(self) -> None:
        self._confirm_done(False)

    def _draw_frame(self) -> None:
        self.draw_quit_confirm()
