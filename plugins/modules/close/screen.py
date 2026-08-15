"""Экран вопроса о закрытии панели.

Кнопки и разбор ввода — общий диалог китов (modules.confirm), тот же,
что у quit: вопрос про панель и вопрос про весь терминал должны
выглядеть одинаково. Своё здесь только содержимое над кнопками.
"""

from kittens.tui.operations import styled

from ..confirm import ConfirmScreen
from ..text import truncate


TITLE = 'Close this pane?'


def body(label: str, hint: str, width: int) -> list[tuple[str, str]]:
    """Строки над кнопками: (текст, оформленный текст)."""
    rows = [(TITLE, styled(TITLE, bold=True))]
    if label:
        text = truncate(label, max(8, width))
        rows += [('', ''), (text, text)]
    if hint:
        text = truncate(hint, max(8, width))
        rows += [('', ''), (text, styled(text, dim=True))]
    return rows


class CloseScreen(ConfirmScreen):
    """Вопрос в размер панели; ответ читает handle_result кита."""

    def __init__(self, label: str = '', hint: str = '') -> None:
        super().__init__()
        self.label = label
        self.hint = hint

    def confirm_rows(self) -> list[tuple[str, str]]:
        return body(self.label, self.hint, self.screen_size.cols - 4)
