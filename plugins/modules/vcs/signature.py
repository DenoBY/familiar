"""Подсказка сигнатуры в режиме правки: пока каретка в скобках вызова,
над строкой висит сигнатура с выделенным текущим параметром.

Миксин к ReviewHandler раньше CompletionMixin в MRO: Esc сначала
закрывает список дополнения, потом — подсказку. Запрос — по той же
схеме, что у списка: фоновый поток, один в пути, отмена новым.
"""

from typing import NamedTuple

from ..lsp.position import encode_character
from ..lsp.rpc import RpcError
from ..lsp.session import NoServer
from ..lsp.signature import Signature, parse_signature
from .popup import signature_line


# пока подсказка открыта, стрелки и набор внутри вызова переспрашивают
# её на паузе: параметр мог смениться, а спрашивать на каждое нажатие
# незачем
FOLLOW_DELAY = 0.08

INVOKED, TRIGGER_CHARACTER, CONTENT_CHANGE = 1, 2, 3

_FOLLOW_KEYS = frozenset({'LEFT', 'RIGHT', 'HOME', 'END', 'BACKSPACE', 'DELETE'})


class _Ask(NamedTuple):
    path: str
    line: int
    caret: int
    line_text: str
    text: str
    context: dict
    epoch: int


class SignatureMixin:

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._sig: 'Signature | None' = None
        self._sig_line = -1
        self._sig_epoch = 0
        self._sig_busy = False
        self._sig_next: 'tuple | None' = None
        self._sig_call = None
        self._sig_timer = None

    def _sig_close(self) -> None:
        self._sig = None
        self._sig_epoch += 1
        self._sig_next = None
        call = self._sig_call
        if call is not None:
            call.cancel()
        if self._sig_timer is not None:
            self._sig_timer.cancel()
            self._sig_timer = None

    def _signature_hint(self) -> None:
        self._sig_request(INVOKED)

    # --- ввод ---

    def _edit_text(self, text: str, in_bracketed_paste: bool) -> bool:
        was = self.editing
        handled = super()._edit_text(text, in_bracketed_paste)
        if not (was and self.editing and handled and text) or in_bracketed_paste:
            return handled
        ch = text[-1]
        session = self._cmp_session()
        if session is None:
            return handled
        if ch in session.signature_triggers:
            self._sig_request(TRIGGER_CHARACTER, ch)
        elif self._sig is not None and ch in session.signature_retriggers:
            self._sig_request(TRIGGER_CHARACTER, ch)
        elif self._sig is not None:
            self._sig_follow()
        return handled

    def _edit_key(self, key_event) -> bool:
        if (self.editing and self._sig is not None and key_event.key == 'ESCAPE'
                and not self._cmp_visible() and self._tabs is None
                and self.edit_buf.selection() is None
                and not any(getattr(key_event, m, False)
                            for m in ('shift', 'alt', 'ctrl', 'super'))):
            self._sig_close()
            self.draw_screen()
            return True
        handled = super()._edit_key(key_event)
        if self._sig is not None and key_event.key in _FOLLOW_KEYS:
            self._sig_follow()
        return handled

    def _sig_follow(self) -> None:
        loop = getattr(self, 'asyncio_loop', None)
        if loop is None:
            return
        if self._sig_timer is not None:
            self._sig_timer.cancel()
        self._sig_timer = loop.call_later(FOLLOW_DELAY, self._sig_follow_now)

    def _sig_follow_now(self) -> None:
        self._sig_timer = None
        if self._sig is not None:
            self._sig_request(CONTENT_CHANGE)

    # --- запрос ---

    def _sig_request(self, kind: int, trigger: str = '') -> None:
        buf = self.edit_buf
        target = self._edit_target()
        session = self._cmp_session()
        if buf is None or target is None or session is None or not self.editing:
            return
        context = {'triggerKind': kind, 'isRetrigger': self._sig is not None}
        if trigger:
            context['triggerCharacter'] = trigger
        ask = _Ask(self._edit_path, buf.line, buf.col, buf.lines[buf.line],
                   buf.display_text(), context, self._sig_epoch)
        if self._sig_busy:
            self._sig_next = (session, ask)
            call = self._sig_call
            if call is not None:
                call.cancel()
            return
        self._sig_start(session, ask)

    def _sig_start(self, session, ask: _Ask) -> None:
        self._sig_busy = True

        def work():
            try:
                session.open_doc(ask.path, ask.text)
                character = encode_character(ask.line_text, ask.caret, session.encoding)
                call = session.signature_call(ask.path, ask.line + 1, character, ask.context)
                self._sig_call = call
                try:
                    return ask, parse_signature(call.result(session.spec.timeout))
                finally:
                    self._sig_call = None
            except (NoServer, RpcError):
                return ask, None
            except Exception:                 # noqa: BLE001
                # граница фонового потока: иначе _sig_busy залип бы
                return ask, None

        self.run_background(work, self._sig_done)

    def _sig_done(self, res: tuple) -> None:
        self._sig_busy = False
        ask, sig = res
        pending, self._sig_next = self._sig_next, None
        buf = self.edit_buf
        fresh = (self.editing and buf is not None and ask.epoch == self._sig_epoch
                 and ask.path == self._edit_path and buf.line == ask.line)
        if fresh and pending is None:
            # пустой ответ — каретка ушла из вызова: подсказка гаснет
            self._sig = sig
            self._sig_line = ask.line
            self.schedule_draw()
        if pending is not None and self.editing:
            self._sig_start(*pending)

    # --- слежение и отрисовка ---

    def _edit_synced(self) -> None:
        super()._edit_synced()
        buf = self.edit_buf
        if self._sig is not None and (buf is None or buf.line != self._sig_line):
            self._sig_close()

    def _draw_review(self) -> None:
        super()._draw_review()
        sig = self._sig
        if (sig is None or not self.editing or self._sync_pending or self.input_mode
                or self._pending_active()):
            return
        caret = self._caret_cell()
        if caret is None:
            return
        row, x = caret
        pane = self._popup_pane()
        menu = self._cmp_box
        above_ok = row - 1 >= pane.top and not (menu is not None and menu.row < row)
        below_ok = row + 1 < pane.bottom and not (menu is not None and menu.row > row)
        if not (above_ok or below_ok):
            return
        at = row - 1 if above_ok else row + 1
        width = min(len(sig.label) + 2, pane.right - pane.left)
        col = max(pane.left, min(x - 2, pane.right - width))
        self.print(f'\x1b[{at + 1};{col + 1}H\x1b[m'
                   + signature_line(sig.label, sig.active, width), end='')

    # --- уход из правки ---

    def _close_edit(self) -> None:
        self._sig_close()
        super()._close_edit()

    def start_search(self) -> None:
        self._sig_close()
        super().start_search()

    def finalize(self) -> None:
        self._sig_close()
        super().finalize()
