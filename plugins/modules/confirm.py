"""Центрированный диалог подтверждения выхода в стиле kitty.

Esc на дне каскада не должен молча закрывать оверлей (см. коммит
«Esc no longer closes overlays»), но и тишина в ответ на Esc
дезориентирует. Компромисс — вопрос с кнопками Yes/No, как у kitty
при закрытии окна с живыми процессами.

Подкласс должен первым делом отдавать ввод диалогу:
confirm_key/confirm_text/confirm_click в начале on_key/on_text/
on_mouse_event, draw_quit_confirm — в начале _draw_frame. Флаг
in_bracketed_paste доводить до confirm_text обязательно: иначе
вставленный текст диалог примет за ответ.
"""

from kittens.tui.handler import Handler
from kittens.tui.loop import EventType as MouseEventType
from kittens.tui.loop import MouseButton
from kittens.tui.operations import MouseTracking, styled

from .draw import AtomicDraw
from .keylayout import chord, ctrl_letter, to_latin
from .pointer import PointerCursor


_GAP = 3   # пробелы между кнопками
_BUTTONS = (('Yes', 'green'), ('No', 'red'))

# Высота под пустой строкой-разделителем: рамка кнопок или их же
# однострочный вариант.
_FRAMED_H = 1 + 3
_COMPACT_H = 1 + 1


def _button_rows(label: str, focus: bool, fg: str) -> tuple[str, str, str]:
    border = 'yellow' if focus else 'gray'
    bar = '─' * (len(label) + 2)
    return (styled(f'╭{bar}╮', fg=border),
            styled('│ ', fg=border) + styled(label, fg=fg, bold=focus)
            + styled(' │', fg=border),
            styled(f'╰{bar}╯', fg=border))


def _button_row(label: str, focus: bool, fg: str) -> str:
    border = 'yellow' if focus else 'gray'
    return (styled('[ ', fg=border) + styled(label, fg=fg, bold=focus)
            + styled(' ]', fg=border))


def _fit_rows(lines: list[tuple[str, str]],
              rows: int) -> tuple[list[tuple[str, str]], bool]:
    """Содержимое и признак «кнопки в одну строку» под высоту панели.

    Оверлей открыт в размер панели, а панель бывает в несколько строк:
    сначала выкидываем пустые разделители, потом сжимаем кнопки, потом
    режем хвост (подсказку) — вопрос остаётся всегда.
    """
    if len(lines) + _FRAMED_H <= rows:
        return lines, False
    lines = [row for row in lines if row[0]]
    if len(lines) + _FRAMED_H <= rows:
        return lines, False
    while len(lines) > 1 and len(lines) + _COMPACT_H > rows:
        lines.pop()
    return lines, True


class ConfirmQuit:

    QUIT_CONFIRM_MSG = 'Are you sure you want to close?'

    # Что значит ⌃c в открытом диалоге. У оверлеев — «да»: клавиша и
    # так закрывает кит, диалог ей не преграда. Кит, который сам
    # является вопросом (ConfirmScreen), отвечает ею «нет» — там
    # закрытие кита и есть ответ, а безопасный ответ — отказ.
    CONFIRM_CTRL_C = True

    confirm_active = False
    _confirm_focus = 0          # 0 — Yes, 1 — No
    # (row, x_from, x_to) кнопок последнего кадра — для кликов
    _confirm_hitboxes: 'tuple[tuple[int, int, int], ...]' = ()

    def start_quit_confirm(self) -> None:
        self.confirm_active = True
        self._confirm_focus = 0
        self.draw_screen()

    def _confirm_done(self, yes: bool) -> None:
        self.confirm_active = False
        if yes:
            self.quit_loop(0)
        else:
            self.draw_screen()

    # --- ввод ---

    def confirm_key(self, key_event) -> bool:
        if not self.confirm_active:
            return False
        if chord(key_event, 'ctrl', 'c'):
            self._confirm_done(self.CONFIRM_CTRL_C)
            return True
        k = key_event.key
        if k == 'ENTER':
            self._confirm_done(self._confirm_focus == 0)
        elif k == 'ESCAPE':
            self._confirm_done(False)
        elif k in ('LEFT', 'RIGHT', 'TAB'):
            self._confirm_focus ^= 1
            self.draw_screen()
        return True   # прочие клавиши глотаем: под диалогом ничего не живёт

    def confirm_text(self, text: str, in_bracketed_paste: bool = False) -> bool:
        if not self.confirm_active:
            return False
        if in_bracketed_paste:
            # Вставка — содержимое, а не ответ: буфер, начинающийся с
            # «y» (или несущий \x03), закрывал бы окно за пользователя.
            return True
        if ctrl_letter(text) == 'c':        # на кириллице ⌃c приходит C0-байтом
            self._confirm_done(self.CONFIRM_CTRL_C)
            return True
        c = to_latin(text[:1]).lower()
        if c == 'y':
            self._confirm_done(True)
        elif c == 'n':
            self._confirm_done(False)
        return True

    def confirm_click(self, ev) -> bool:
        if not self.confirm_active:
            return False
        self.update_pointer(ev)   # рука над кнопками (PointerCursor)
        press = getattr(ev, 'type', None) == MouseEventType.PRESS
        # ровно ЛКМ: у колеса buttons отрицательный и битово
        # пересекается с LEFT
        if press and ev.buttons == MouseButton.LEFT:
            for i, (row, x0, x1) in enumerate(self._confirm_hitboxes):
                if ev.cell_y == row and x0 <= ev.cell_x < x1:
                    self._confirm_done(i == 0)
                    break
        return True   # мимо кнопок — просто глотаем, как kitty

    def confirm_pointer(self, ev) -> 'str | None':
        """Ветка _wanted_pointer на время диалога."""
        for row, x0, x1 in self._confirm_hitboxes:
            if ev.cell_y == row and x0 <= ev.cell_x < x1:
                return 'pointer'
        return None

    # --- отрисовка ---

    def confirm_rows(self) -> list[tuple[str, str]]:
        """Строки над кнопками: (текст, оформленный текст).

        Обе версии нужны вместе: ширину для центрирования считаем по
        чистому тексту, печатаем оформленный — в нём escape. Подкласс
        переопределяет, когда вопроса в одну строку мало.
        """
        return [(self.QUIT_CONFIRM_MSG, styled(self.QUIT_CONFIRM_MSG, bold=True))]

    def draw_quit_confirm(self) -> bool:
        if not self.confirm_active:
            return False
        self.cmd.clear_screen()
        rows, cols = self.screen_size.rows, self.screen_size.cols
        lines, compact = _fit_rows(self.confirm_rows(), rows)
        height = len(lines) + (_COMPACT_H if compact else _FRAMED_H)
        top = max(0, (rows - height) // 2)
        for _ in range(top):
            self.print()
        for plain, decorated in lines:
            self.print(' ' * max(0, (cols - len(plain)) // 2) + decorated)
        self.print()
        self._draw_buttons(top + len(lines) + 1, cols, compact)
        for _ in range(max(0, rows - top - height - 1)):
            self.print()
        return True

    def _draw_buttons(self, first_row: int, cols: int, compact: bool) -> None:
        widths = [len(label) + 4 for label, _ in _BUTTONS]   # '│ Yes │'
        # Перенос строки развалил бы рамку, поэтому в тесной панели
        # промежуток жмётся до пробела; уже 14 колонок кнопки всё
        # равно не помещаются.
        gap = _GAP if sum(widths) + _GAP <= cols else 1
        left = max(0, (cols - sum(widths) - gap) // 2)
        hitboxes = []
        x = left
        for w in widths:
            hitboxes.append((first_row + (0 if compact else 1), x, x + w))
            x += w + gap
        self._confirm_hitboxes = tuple(hitboxes)

        focus = self._confirm_focus
        if compact:
            self.print(' ' * left + (' ' * gap).join(
                _button_row(label, focus == i, fg)
                for i, (label, fg) in enumerate(_BUTTONS)))
            return
        yes, no = (_button_rows(label, focus == i, fg)
                   for i, (label, fg) in enumerate(_BUTTONS))
        for part in range(3):
            self.print(' ' * left + yes[part] + ' ' * gap + no[part])


class ConfirmScreen(ConfirmQuit, AtomicDraw, PointerCursor, Handler):
    """Кит, который целиком является одним вопросом (quit, close).

    Подкласс задаёт содержимое — confirm_rows(); ответ читает его
    handle_result по флагу confirmed.
    """

    mouse_tracking = MouseTracking.full
    CONFIRM_CTRL_C = False

    def __init__(self) -> None:
        super().__init__()
        self.confirmed = False

    def initialize(self) -> None:
        self.cmd.set_cursor_visible(False)
        self.start_quit_confirm()

    def finalize(self) -> None:
        self.cmd.set_cursor_visible(True)
        self.reset_pointer()

    def _confirm_done(self, yes: bool) -> None:
        self.confirmed = yes
        self.quit_loop(0)

    def on_key(self, key_event) -> None:
        self.confirm_key(key_event)

    def on_text(self, text: str, in_bracketed_paste: bool = False) -> None:
        self.confirm_text(text, in_bracketed_paste)

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
