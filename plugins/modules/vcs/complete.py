"""Автодополнение в режиме правки review: когда спрашивать language
server, как показать и отфильтровать ответ и что делает принятый
пункт — вплоть до auto-import и плейсхолдеров сниппета.

Миксин к ReviewHandler раньше EditorMixin в MRO: клавиши списка
перехватываются до того, как правка сочтёт их переводом строки или
отступом. Протокол и разбор ответа — modules/lsp, фильтр — fuzzy,
раскладка плашек — popup.

Запрос уходит в фоновый поток и отменяется новым нажатием. Ответ,
опоздавший к набору, не выбрасывается, пока текст левее слова тот
же, — иначе при быстром наборе список не появлялся бы никогда.
"""

import heapq
import re
from typing import NamedTuple

from kittens.tui.loop import EventType as MouseEventType
from kittens.tui.loop import MouseButton

from ..keylayout import chord
from ..lsp.completion import (
    WORD_KIND,
    Item,
    additional_edits,
    item_start,
    main_edit,
    parse_completion,
    word_start,
)
from ..lsp.position import encode_character
from ..lsp.rpc import RpcError
from ..lsp.session import NoServer
from .fuzzy import FUZZY, Match, buffer_words, match
from .popup import (
    MAX_ROWS,
    MenuRow,
    Pane,
    doc_content,
    doc_lines,
    menu_lines,
    menu_widths,
    place_doc,
    place_menu,
)
from .tabstops import Tabstops
from .view import SEP


# символов идентификатора перед кареткой, после которых список
# открывается сам (Helix: completion-trigger-len)
AUTO_MIN = 2
# слова файла — только с трёх: иначе в прозе список висел бы всегда
WORDS_MIN = 3
MAX_SHOWN = 200
# пауза перед resolve выбранного пункта: стрелкой по списку бегут
# быстрее, чем сервер отвечает, и спрашивать про каждый пункт незачем
RESOLVE_DELAY = 0.12
RESOLVE_TIMEOUT = 2.0

# даже если сервер объявил их триггерами — это не повод открывать
# список (blink.cmp: show_on_blocked_trigger_characters)
_BLOCKED = frozenset(' \t\n')
_WORD_CHAR = re.compile(r'[A-Za-z0-9_$]')
_IDENT_TAIL = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*$')

# команды VS Code, которые серверы вешают на пункт
TRIGGER_PARAMS = 'editor.action.triggerParameterHints'
TRIGGER_SUGGEST = 'editor.action.triggerSuggest'

INVOKED, TRIGGER_CHARACTER, INCOMPLETE = 1, 2, 3

Pos = 'tuple[int, int]'


class Ask(NamedTuple):
    """Снимок запроса: по нему поздний ответ сверяют с текстом."""
    path: str
    rel: str
    line: int
    caret: int
    line_text: str
    text: str
    kind: int
    trigger: str
    manual: bool
    epoch: int


class Popup:
    """Открытый список: пункты ответа и то, что из них видно сейчас."""

    def __init__(self, ask: Ask, items: 'list[Item]', incomplete: bool,
                 encoding: str, session: object = None) -> None:
        self.ask = ask
        self.items = items
        self.incomplete = incomplete
        self.encoding = encoding
        self.session = session
        self.starts = [item_start(it, ask.line_text, ask.caret, encoding) for it in items]
        self.min_start = min(self.starts, default=word_start(ask.line_text, ask.caret))
        self._uniform = all(s == self.min_start for s in self.starts)
        # (запрос, индексы подошедших): удлинившийся запрос сужает их,
        # а не перебирает весь ответ заново
        self._last: 'tuple[str, list[int]] | None' = None
        self._seen: 'str | None' = None
        self.shown: 'list[tuple[Item, Match, str]]' = []
        self.sel = 0
        self.top = 0
        self.above: 'bool | None' = None

    @property
    def manual(self) -> bool:
        return self.ask.manual

    @property
    def words(self) -> bool:
        return self.session is None

    def refilter(self, line: str, caret: int) -> None:
        # пересборка строк бывает и без правки (resize, клик в то же
        # место) — выбор в списке она сбрасывать не должна
        if self._seen == line[:caret]:
            return
        self._seen = line[:caret]
        query = line[self.min_start:caret]
        pool: 'list[int] | range' = range(len(self.items))
        if self._uniform and self._last is not None and query.startswith(self._last[0]):
            pool = self._last[1]
        found = []
        for i in pool:
            q = query if self._uniform else line[self.starts[i]:caret]
            m = match(q, self.items[i].filter_text)
            if m is not None:
                found.append((i, m, q))
        if self._uniform:
            self._last = (query, [i for i, _, _ in found])
        if any(q for _, _, q in found):
            ranked = heapq.nsmallest(MAX_SHOWN, found, key=self._rank)
        else:
            ranked = heapq.nsmallest(MAX_SHOWN, found, key=self._rank_empty)
        self.shown = [(self.items[i], m, q) for i, m, q in ranked]
        self.sel = self.top = 0

    def _rank(self, entry: 'tuple[int, Match, str]') -> tuple:
        # счёт грубее сортировки сервера: между почти равными решает
        # sortText — gopls и ts ранжируют пункты сами. Но префикс в
        # том же регистре важнее: на `Use` класс User — выше use_…
        i, m, query = entry
        item = self.items[i]
        cased = m.tier < FUZZY and item.filter_text.startswith(query)
        return m.tier, not cased, -(m.score // 8), item.sort_text, len(item.label), i

    def _rank_empty(self, entry: 'tuple[int, Match, str]') -> tuple:
        i = entry[0]
        item = self.items[i]
        return not item.preselect, item.sort_text, item.label, i

    def selected(self) -> 'Item | None':
        return self.shown[self.sel][0] if self.shown else None

    def move(self, step: int) -> None:
        n = len(self.shown)
        if not n:
            return
        if abs(step) == 1:
            self.sel = (self.sel + step) % n
        else:
            self.sel = max(0, min(n - 1, self.sel + step))

    def scroll_into(self, height: int) -> None:
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + height:
            self.top = self.sel - height + 1
        self.top = max(0, min(self.top, len(self.shown) - height))


class CompletionMixin:

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cmp: 'Popup | None' = None
        # растёт на каждом закрытии списка: ответ на запрос из прошлой
        # «эпохи» (до Esc) не должен открыть список снова
        self._cmp_epoch = 0
        self._cmp_busy = False
        self._cmp_next: 'tuple | None' = None
        self._cmp_call = None
        self._cmp_peek: 'tuple[str, object] | None' = None
        self._cmp_box = None
        self._cmp_rows = MAX_ROWS
        self._cmp_swallow = False
        self._words_cache: 'tuple[int, int, list[str]]' = (-1, -1, [])
        # документация выбранного пункта — только по ⌃Space, как в
        # JetBrains: рядом со списком она всё время перекрывала код
        self._doc_on = False
        self._resolve_timer = None
        self._resolve_busy = False
        self._tabs: 'Tabstops | None' = None
        self._tabs_lines: 'list[str]' = []
        self._tabs_final = False

    # --- состояние ---

    def _cmp_visible(self) -> bool:
        return (self._cmp is not None and bool(self._cmp.shown) and self.editing
                and not self.input_mode and not self._pending_active())

    def _cmp_close(self) -> None:
        self._cmp = None
        self._cmp_box = None
        self._cmp_epoch += 1
        self._cmp_next = None
        call = self._cmp_call
        if call is not None:
            call.cancel()
        if self._resolve_timer is not None:
            self._resolve_timer.cancel()
            self._resolve_timer = None

    def _cmp_session(self) -> object:
        """Живая сессия для правимого файла, без старта сервера —
        по ней решаем, триггер ли набранный символ.
        """
        cached = self._cmp_peek
        if cached is not None and cached[0] == self._edit_path and cached[1].alive():
            return cached[1]
        target = self._edit_target()
        if target is None or self.edit_buf is None:
            return None
        session = self._lsp_pool().peek(target[0], self.edit_buf.lines[0])
        self._cmp_peek = (self._edit_path, session) if session is not None else None
        return session

    def _signature_hint(self) -> None:
        """Хук SignatureMixin: пункт попросил показать сигнатуру."""

    # --- ввод ---

    def _edit_text(self, text: str, in_bracketed_paste: bool) -> bool:
        was = self.editing
        # ⌃Space на части раскладок приходит байтом NUL, а не клавишей
        if was and not in_bracketed_paste and text == '\x00':
            self._cmp_manual()
            return True
        handled = super()._edit_text(text, in_bracketed_paste)
        if was and self.editing and handled and text:
            if in_bracketed_paste:
                self._cmp_close()
            else:
                self._cmp_typed(text[-1])
        return handled

    def _edit_key(self, key_event) -> bool:
        if not self.editing:
            return super()._edit_key(key_event)
        if _is_manual(key_event):
            self._cmp_manual()
            return True
        if self._cmp_visible() and self._menu_key(key_event):
            return True
        if self._tabs is not None and self._snippet_key(key_event):
            return True
        if chord(key_event, 'super', 'z') or chord(key_event, 'super+shift', 'z'):
            self._end_snippet()
            self._cmp_close()
        return super()._edit_key(key_event)

    def _cmp_manual(self) -> None:
        if self._cmp_visible():
            self._doc_on = not self._doc_on
            self._arm_resolve()
            self.draw_screen()
            return
        self._cmp_close()
        self._cmp_request(INVOKED, manual=True)

    def _menu_key(self, key_event) -> bool:
        k = key_event.key
        if _mods(key_event):
            return False
        popup = self._cmp
        if k in ('UP', 'DOWN'):
            popup.move(-1 if k == 'UP' else 1)
        elif k in ('PAGE_UP', 'PAGE_DOWN'):
            step = max(1, self._cmp_rows - 1)
            popup.move(-step if k == 'PAGE_UP' else step)
        elif k in ('ENTER', 'TAB'):
            self._cmp_accept()
            return True
        elif k == 'ESCAPE':
            self._cmp_close()
            self.draw_screen()
            return True
        else:
            return False
        self._arm_resolve()
        self.draw_screen()
        return True

    def _cmp_typed(self, ch: str) -> None:
        """Набран символ (буфер уже изменён): открыть, переспросить или
        закрыть список.
        """
        buf = self.edit_buf
        if buf.selection() is not None:
            return
        session = self._cmp_session()
        triggers = session.completion_triggers if session is not None else ()
        popup = self._cmp
        if ch in triggers and ch not in _BLOCKED:
            self._cmp_close()
            self._cmp_request(TRIGGER_CHARACTER, ch)
            return
        if not _WORD_CHAR.match(ch):
            if popup is not None:
                self._cmp_close()
            return
        if popup is not None:
            if popup.incomplete and not popup.words:
                self._cmp_request(INCOMPLETE)
            return
        tail = _IDENT_TAIL.search(buf.lines[buf.line][:buf.col])
        if tail and len(tail.group()) >= AUTO_MIN:
            self._cmp_request(INVOKED)

    # --- запрос ---

    def _cmp_request(self, kind: int, trigger: str = '', manual: bool = False) -> None:
        buf = self.edit_buf
        target = self._edit_target()
        if buf is None or target is None or buf.selection() is not None or self.input_mode:
            return
        rel = target[0]
        line_text = buf.lines[buf.line]
        ask = Ask(self._edit_path, rel, buf.line, buf.col, line_text, buf.display_text(),
                  kind, trigger, manual, self._cmp_epoch)
        pool = self._lsp_pool()
        if pool.language(rel, buf.lines[0]) is None:
            self._cmp_words(ask)
            return
        session = pool.peek(rel, buf.lines[0])
        if session is not None and not session.has_completion:
            self._cmp_words(ask)
            return
        if session is None or not session.ready():
            # сервер стартует или индексирует: слова файла — сразу,
            # ответ сервера заменит их, когда придёт
            self._cmp_words(ask)
        if self._cmp_busy:
            self._cmp_next = (pool, ask)
            call = self._cmp_call
            if call is not None:
                call.cancel()
            return
        self._cmp_start(pool, ask)

    def _cmp_start(self, pool, ask: Ask) -> None:
        self._cmp_busy = True
        first_line = ask.text.split('\n', 1)[0]

        def work():
            try:
                session = pool.session_for(ask.rel, first_line)
                if not session.has_completion:
                    return ask, session, None, ''
                session.open_doc(ask.path, ask.text)
                character = encode_character(ask.line_text, ask.caret, session.encoding)
                context = {'triggerKind': ask.kind}
                if ask.trigger:
                    context['triggerCharacter'] = ask.trigger
                call = session.completion_call(ask.path, ask.line + 1, character, context)
                self._cmp_call = call
                try:
                    result = call.result(session.spec.timeout)
                finally:
                    self._cmp_call = None
                return ask, session, parse_completion(result), ''
            except NoServer as e:
                return ask, None, None, str(e)
            except RpcError as e:
                return ask, None, None, f'language server: {e}'
            except Exception as e:            # noqa: BLE001
                # граница фонового потока: без неё исключение унесло бы
                # и _cmp_busy — автодополнение молча выключилось бы до
                # закрытия кита
                return ask, None, None, f'{type(e).__name__}: {e}'

        self.run_background(work, self._cmp_done)

    def _cmp_done(self, res: tuple) -> None:
        self._cmp_busy = False
        ask, session, parsed, err = res
        pending, self._cmp_next = self._cmp_next, None
        if self._compatible(ask):
            if parsed is not None:
                self._cmp_install(ask, session, *parsed)
            elif pending is None:
                if ask.manual and err:
                    self.flash = err
                self._cmp_words(ask, fallback=True)
        if pending is not None and self.editing:
            self._cmp_start(*pending)

    def _compatible(self, ask: Ask) -> bool:
        buf = self.edit_buf
        return (self.editing and buf is not None and ask.epoch == self._cmp_epoch
                and ask.path == self._edit_path and buf.line == ask.line
                and buf.selection() is None)

    def _cmp_install(self, ask: Ask, session, items: 'list[Item]', incomplete: bool) -> None:
        buf = self.edit_buf
        popup = Popup(ask, items, incomplete, session.encoding, session)
        line = buf.lines[buf.line]
        if buf.col < popup.min_start or line[:popup.min_start] != ask.line_text[:popup.min_start]:
            return
        if self._cmp is not None:
            popup.above = self._cmp.above
        popup.refilter(line, buf.col)
        if not popup.shown:
            if ask.manual and not incomplete:
                self._cmp_words(ask, fallback=True)
                return
            # неполный пустой ответ — сессия жива, следующий символ
            # переспросит; полный пустой — закрыть
            if incomplete:
                self._cmp = popup
            else:
                self._cmp_close()
            self.schedule_draw()
            return
        self._cmp = popup
        self._arm_resolve()
        self.schedule_draw()

    def _cmp_words(self, ask: Ask, fallback: bool = False) -> None:
        """Список из слов файла — пока сервера нет, он молчит или
        ничего не нашёл.
        """
        if not self._compatible(ask):
            return
        current = self._cmp
        if current is not None and not current.words and current.shown:
            return
        buf = self.edit_buf
        start = word_start(ask.line_text, ask.caret)
        query = ask.line_text[start:ask.caret]
        if not ask.manual and len(query) < WORDS_MIN:
            return
        items = []
        for word in self._buffer_words(query):
            item = Item({'label': word})
            item.kind = WORD_KIND
            items.append(item)
        popup = Popup(ask, items, False, 'utf-16')
        popup.refilter(buf.lines[buf.line], buf.col)
        if not popup.shown:
            if ask.manual and fallback:
                self.flash = 'no suggestions'
                self.schedule_draw()
            return
        if current is not None:
            popup.above = current.above
        self._cmp = popup
        self.schedule_draw()

    def _buffer_words(self, exclude: str) -> 'list[str]':
        buf = self.edit_buf
        version, line, words = self._words_cache
        if version != buf.version or line != buf.line:
            words = buffer_words(buf.lines, buf.line, '')
            self._words_cache = (buf.version, buf.line, words)
        return [w for w in words if w != exclude]

    # --- слежение за кареткой ---

    def _edit_synced(self) -> None:
        super()._edit_synced()
        self._follow_snippet()
        popup = self._cmp
        if popup is None:
            return
        buf = self.edit_buf
        line = buf.lines[buf.line]
        if (buf.line != popup.ask.line or buf.selection() is not None
                or buf.col < popup.min_start
                or line[:popup.min_start] != popup.ask.line_text[:popup.min_start]):
            self._cmp_close()
            return
        popup.refilter(line, buf.col)
        if not popup.shown and not popup.incomplete and not popup.manual:
            self._cmp_close()
            return
        self._arm_resolve()

    # --- принятие пункта ---

    def _cmp_accept(self) -> None:
        popup = self._cmp
        item = popup.selected() if popup is not None else None
        buf = self.edit_buf
        if item is None or buf is None:
            return
        line = buf.line
        edit = main_edit(item, buf.lines[line], buf.col, popup.ask.line_text,
                         popup.ask.caret, popup.encoding, buf.unit)
        extra = []
        if item.additional:
            extra = additional_edits(item, buf.lines, popup.encoding, (line, edit.start, edit.end))
        base = shift_through((line, edit.start), extra)
        places = [_place(base, edit.text, s.start, s.end) for s in edit.stops]
        caret = offset_pos(base, edit.text, len(edit.text))
        select = None
        if places:
            first = places[0]
            caret = first[:2]
            if first[2] > first[1]:
                select = (first[:2], (first[0], first[2]))
        self._cmp_close()
        self._end_snippet()
        self.apply_edits([((line, edit.start), (line, edit.end), edit.text), *extra],
                         caret, select)
        if len(edit.stops) > 1 or (edit.stops and edit.stops[0].index != 0):
            self._tabs = Tabstops(places, buf.lines)
            self._tabs_lines = list(buf.lines)
            self._tabs_final = edit.stops[-1].index == 0
        session = popup.session
        if (session is not None and session.resolve_provider and not item.additional
                and not item.resolved):
            self._resolve_import(popup, item, base[0])
        self._after_accept(popup, item, edit.text, bool(edit.stops))

    def _after_accept(self, popup: Popup, item: Item, text: str, snippet: bool) -> None:
        command = (item.command or {}).get('command')
        if command == TRIGGER_PARAMS:
            self._signature_hint()
        if command == TRIGGER_SUGGEST:
            self._cmp_request(INVOKED)
            return
        # вставка кончилась триггером (`App\`, `$this->`) — следующий
        # список нужен сразу, как если бы символ набрали
        last = text[-1:]
        session = popup.session
        if (session is not None and not snippet and last
                and last in session.completion_triggers and last not in _BLOCKED):
            self._cmp_request(TRIGGER_CHARACTER, last)

    def _resolve_import(self, popup: Popup, item: Item, line: int) -> None:
        """auto-import, который сервер (ts) отдаёт только в resolve.

        Пункт уже вставлен; правки из resolve применяем, если все они
        выше его строки и те строки с тех пор не менялись — их
        координаты посчитаны по тексту на момент запроса.
        """
        session = popup.session
        buf = self.edit_buf
        above = list(buf.lines[:line])

        def work():
            try:
                return session.resolve_call(item.raw).result(RESOLVE_TIMEOUT)
            except RpcError:
                return None
            except Exception:                 # noqa: BLE001
                return None                   # граница потока, как у запроса

        def done(resolved):
            item.merge_resolved(resolved)
            buf = self.edit_buf
            if not item.additional or not self.editing or buf is None:
                return
            extra = additional_edits(item, buf.lines, popup.encoding, (line, 0, 0))
            if (buf.lines[:line] != above or len(extra) != len(item.additional)
                    or any(b > (line, 0) for _, b, _ in extra)):
                self.flash = 'import not added — the lines above changed'
                self.schedule_draw()
                return
            anchor = buf.anchor
            caret = shift_through(buf.caret, extra)
            select = (shift_through(anchor, extra), caret) if anchor is not None else None
            self.apply_edits(extra, caret, select)

        self.run_background(work, done)

    # --- сниппет ---

    def _snippet_key(self, key_event) -> bool:
        k = key_event.key
        mods = _mods(key_event)
        buf = self.edit_buf
        if k == 'TAB' and mods <= {'shift'}:
            step = -1 if mods else 1
            place = self._tabs.jump(step, buf.lines)
            if place is None:
                if step > 0:
                    self._end_snippet()
                return True
            if place[2] > place[1]:
                buf.select(place[:2], (place[0], place[2]))
            else:
                buf.set_caret(*place[:2])
            if self._tabs_final and self._tabs.index == len(self._tabs) - 1:
                self._end_snippet()
            self._edited()
            return True
        if k == 'ESCAPE' and not mods:
            self._end_snippet()
            buf.anchor = None
            self._edited()
            return True
        return False

    def _end_snippet(self) -> None:
        self._tabs = None
        self._tabs_lines = []

    def _follow_snippet(self) -> None:
        tabs = self._tabs
        buf = self.edit_buf
        if tabs is None or buf is None:
            return
        if self._tabs_lines != buf.lines:
            if not tabs.update(self._tabs_lines, buf.lines):
                self._end_snippet()
                return
            self._tabs_lines = list(buf.lines)
        rows = [p[0] for p in tabs.places(buf.lines)]
        if not min(rows) <= buf.line <= max(rows):
            self._end_snippet()

    # --- документация выбранного пункта ---

    def _arm_resolve(self) -> None:
        if self._resolve_timer is not None:
            self._resolve_timer.cancel()
            self._resolve_timer = None
        popup = self._cmp
        item = popup.selected() if popup is not None else None
        session = popup.session if popup is not None else None
        if (not self._doc_on or item is None or item.resolved or session is None
                or not session.resolve_provider):
            return
        loop = getattr(self, 'asyncio_loop', None)
        if loop is not None:
            self._resolve_timer = loop.call_later(RESOLVE_DELAY, self._resolve_now)

    def _resolve_now(self) -> None:
        self._resolve_timer = None
        popup = self._cmp
        item = popup.selected() if popup is not None else None
        if item is None or item.resolved or popup.session is None:
            return
        if self._resolve_busy:
            return              # ответ на прошлый пункт перевзведёт таймер
        self._resolve_busy = True
        session = popup.session

        def work():
            try:
                return session.resolve_call(item.raw).result(RESOLVE_TIMEOUT)
            except RpcError:
                return None
            except Exception:                 # noqa: BLE001
                return None                   # граница потока, как у запроса

        def done(resolved):
            self._resolve_busy = False
            item.merge_resolved(resolved)
            if self._cmp is popup:
                self._arm_resolve()
                self.schedule_draw()

        self.run_background(work, done)

    # --- отрисовка ---

    def _popup_pane(self) -> Pane:
        top = 2 + (1 if self.sticky_line() else 0)
        return Pane(top, 2 + self.visible_rows(), self.left_width() + len(SEP),
                    self.screen_size.cols - 2)

    def _draw_review(self) -> None:
        super()._draw_review()
        self._cmp_box = None
        if self._sync_pending or not self._cmp_visible():
            return
        popup = self._cmp
        buf = self.edit_buf
        anchor = self._caret_cell(buf.display_col(buf.line, popup.min_start))
        if anchor is None:
            return
        pane = self._popup_pane()
        widths = menu_widths([_row(item, m, q) for item, m, q in popup.shown])
        placed = place_menu(anchor[0], anchor[1], len(popup.shown), widths, pane, popup.above)
        if placed is None:
            return
        box, popup.above = placed
        self._cmp_rows = box.height
        popup.scroll_into(box.height)
        rows = [_row(item, m, q, with_positions=True)
                for item, m, q in popup.shown[popup.top:popup.top + box.height]]
        lines = menu_lines(rows, box, widths, popup.sel, popup.top, len(popup.shown))
        self._paint(box, lines)
        self._cmp_box = box
        item = popup.selected()
        if self._doc_on and item is not None:
            content = doc_content(item.detail if item.detail != item.description else '',
                                  item.documentation)
            doc = place_doc(box, pane, content)
            if doc is not None:
                self._paint(doc, doc_lines(content, doc))

    def _paint(self, box, lines: 'list[str]') -> None:
        # сброс SGR перед плашкой: футер кадра оставляет жирный
        # включённым (styled(bold=False) заканчивается на `1`), и
        # плашка унаследовала бы его
        for i, text in enumerate(lines):
            self.print(f'\x1b[{box.row + i + 1};{box.col + 1}H\x1b[m{text}', end='')

    def _review_footer(self) -> str:
        if self._cmp_visible():
            return ' [complete]  ↑↓ select · Enter/Tab insert · ⌃Space docs · Esc close'
        if self.editing and self._tabs is not None:
            return ' [snippet]  Tab next · ⇧Tab previous · Esc done'
        return super()._review_footer()

    # --- мышь ---

    def _on_mouse(self, ev) -> None:
        box = self._cmp_box
        if box is None or self._cand is not None or not self._cmp_visible():
            self._cmp_swallow = False
            super()._on_mouse(ev)
            return
        kind = getattr(ev, 'type', None)
        inside = box.contains(ev.cell_x, ev.cell_y)
        popup = self._cmp
        if ev.buttons in (MouseButton.WHEEL_UP, MouseButton.WHEEL_DOWN) and inside:
            popup.move(-1 if ev.buttons == MouseButton.WHEEL_UP else 1)
            self._arm_resolve()
            self.schedule_draw()
            return
        if kind == MouseEventType.PRESS and ev.buttons == MouseButton.LEFT:
            if inside:
                index = popup.top + ev.cell_y - box.row
                if index < len(popup.shown):
                    popup.sel = index
                    self._cmp_accept()
                # отпускание той же кнопки не должно поставить каретку
                # под плашку
                self._cmp_swallow = True
                return
            self._cmp_close()
        if kind == MouseEventType.RELEASE and self._cmp_swallow:
            self._cmp_swallow = False
            return
        super()._on_mouse(ev)

    # --- уход из правки ---

    def _close_edit(self) -> None:
        self._cmp_close()
        self._end_snippet()
        super()._close_edit()

    def start_search(self) -> None:
        self._cmp_close()
        super().start_search()

    def _ask(self, *args, **kwargs) -> None:
        self._cmp_close()
        super()._ask(*args, **kwargs)

    def finalize(self) -> None:
        self._cmp_close()
        super().finalize()


# --- чистые хелперы ---

def _is_manual(key_event) -> bool:
    """⌃Space, а на случай, если его забрала система под раскладку,
    ещё ⌥Esc — как в VS Code на macOS.
    """
    mods = _mods(key_event)
    if key_event.key in (' ', 'SPACE') and mods == {'ctrl'}:
        return True
    return key_event.key == 'ESCAPE' and mods == {'alt'}


def _mods(key_event) -> 'set[str]':
    return {m for m in ('shift', 'alt', 'ctrl', 'super', 'hyper', 'meta')
            if getattr(key_event, m, False)}


def _row(item: Item, m: Match, query: str, with_positions: bool = False) -> MenuRow:
    positions: 'tuple[int, ...]' = ()
    if with_positions:
        if item.filter_text == item.label:
            positions = m.positions
        else:
            on_label = match(query, item.label)
            positions = on_label.positions if on_label is not None else ()
    description = item.description if item.description != item.label_detail else ''
    return MenuRow(item.kind, item.label, item.label_detail, description, positions,
                   item.deprecated)


def offset_pos(base: Pos, text: str, offset: int) -> Pos:
    """Позиция символа `offset` вставленного в `base` текста."""
    lines = text.count('\n', 0, offset)
    if not lines:
        return base[0], base[1] + offset
    return base[0] + lines, offset - text.rfind('\n', 0, offset) - 1


def _place(base: Pos, text: str, start: int, end: int) -> 'tuple[int, int, int]':
    line, a = offset_pos(base, text, start)
    end_line, b = offset_pos(base, text, end)
    # плейсхолдер через перевод строки выделить на одной строке нельзя —
    # встаём в его начало
    return line, a, b if end_line == line else a


def shift_through(pos: Pos, edits: 'list[tuple[Pos, Pos, str]]') -> Pos:
    """Куда уедет позиция исходного текста после правок, лежащих до неё.

    Правки выше позиции сдвигают её на вставленные строки, правка на
    той же строке левее — ещё и по колонке. Идём от ближней к дальней:
    координаты дальних правок ближние не трогают.
    """
    line, col = pos
    for (la, ca), (lb, cb), text in sorted(edits, reverse=True):
        if (lb, cb) > (line, col):
            continue
        added = text.count('\n')
        if lb == line:
            tail = len(text) - text.rfind('\n') - 1 if added else ca + len(text)
            col = tail + (col - cb)
        line += added - (lb - la)
    return line, col
