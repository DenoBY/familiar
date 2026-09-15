"""Правка файла прямо в final-view review: каретка, ввод, выделение,
история и автосохранение поверх обычной модели строк диффа.

Миксин к ReviewScreen; собирается только в review — править имеет
смысл лишь рабочее дерево. Режим модальный: `i` входит, Esc выходит, и
вне режима все клавиши экрана значат то же, что и раньше.

Текст живёт в TextBuffer, а рисует его штатный final_rows: после
каждой пачки ввода источник диффа патчится (DiffSource.patched) —
сравнение и лексинг всего файла на каждое нажатие стоили бы сотни
миллисекунд, — а точный пересчёт идёт в фоне на паузе.

Файл пишется при выходе из режима, при уходе на другой файл, по ⌘s и
при закрытии кита. Если на диске он успел поменяться (правит агент),
молча не перезаписываем: спрашиваем y / d.
"""

import os
import time
from typing import Callable, Literal

from kittens.tui.loop import EventType as MouseEventType
from kittens.tui.loop import MouseButton

from ..highlight import MAX_HIGHLIGHT_BYTES
from ..keylayout import chord, to_latin
from ..lsp.position import line_splice
from .buffer import TextBuffer, decode_editable
from .diff import DiffSource, code_width
from .git import last_error, read_bytes, write_text
from .goto import ALT_MOD
from .symbols import word_span
from .view import SEP, SHIFT_MOD


# Пауза после последней правки, за которой пересчитываем дифф и
# подсветку точно: пропатченные блоки и построчные цвета — только
# приближение на время набора.
EXACT_DELAY = 0.3

# Запас колонок, с которым горизонтальный скролл догоняет каретку:
# сдвиг на колонку за символ дёргал бы весь текст на каждом нажатии.
HSCROLL_MARGIN = 8

# Форма курсора терминала (DECSCUSR): в правке — мигающая черта между
# символами, как каретка в IDE; блок накрывал бы символ и путал, куда
# пойдёт ввод. 0 — вернуть форму из конфига терминала.
CARET_BEAM = '\x1b[5 q'
CARET_RESET = '\x1b[0 q'

_MOVE_KEYS = {'LEFT': 'left', 'RIGHT': 'right', 'UP': 'up', 'DOWN': 'down',
              'HOME': 'home', 'END': 'end', 'PAGE_UP': 'page_up',
              'PAGE_DOWN': 'page_down'}
_SUPER_MOVES = {'LEFT': 'home', 'RIGHT': 'end', 'UP': 'doc_start', 'DOWN': 'doc_end'}

WriteResult = Literal['saved', 'clean', 'disk', 'error']


class EditorMixin:

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.editing = False
        self.edit_buf: 'TextBuffer | None' = None
        self._edit_path = ''
        # байты файла, какими мы их прочли или записали: по ним видно,
        # что файл правил кто-то ещё
        self._edit_disk: 'bytes | None' = None
        # что нарисовать поверх строк: (строка, экранная колонка,
        # выделение в экранных колонках). Снимок, а не живой буфер:
        # кадр между правкой и пересборкой строк рисовал бы каретку
        # по устаревшим строкам
        self._caret_snap: 'tuple | None' = None
        self._sync_pending = False
        # поколение текста: фоновый пересчёт от старого текста не
        # должен подменить модель новее
        self._edit_gen = 0
        self._exact_timer = None
        # пересчёт идёт в фоне: второй параллельно только отнимал бы
        # GIL у ввода, а его результат всё равно устарел бы
        self._exact_busy = False
        # запрос после неудачной записи, и что сделать, когда на него
        # ответят: (выйти ли из режима, продолжение, показать ли файл
        # с диска перед ним)
        self._conflict: "Literal['', 'disk', 'error']" = ''
        self._conflict_err = ''
        self._conflict_next: 'tuple[bool, Callable[[], None] | None, bool]' = (False, None, True)
        # комментарии на момент последней записи: правка двигает их
        # вслед за строками, и отказ от неё должен вернуть их на место
        self._saved_annots: dict = {}
        self._paste_cr = False
        self._edit_drag = False
        self._press_to_super = False
        self._beam = False

    # --- что правим ---

    def _edit_target(self) -> 'tuple[str, str | None] | None':
        if self._external:
            return self._external, self.view_repo
        it = self.current_item()
        return (it['path'], it.get('repo')) if it else None

    def _edit_hint(self) -> str:
        if self.editing or self.find_mode or not self.source.mutable:
            return ''
        target = self._edit_target()
        if target is None or os.path.isabs(target[0]) or self.diff_src is None:
            return ''
        return ' · i edit'

    def _load_editable(self) -> 'tuple[str, bytes, str] | str':
        """(путь, байты, текст) файла под правку либо причина отказа."""
        if self.find_mode:
            return 'read-only (find in files)'
        if not self.source.mutable:
            return 'read-only'
        target = self._edit_target()
        if target is None or not self.root:
            return 'select a file to edit'
        rel = target[0]
        if os.path.isabs(rel):
            return 'read-only (outside the repository)'
        it = self.current_item()
        if not self._external and it and it.get('stat') == (None, None):
            return 'binary file — nothing to edit'
        path = os.path.join(self.root, rel)
        # write_text пошёл бы по ссылке и переписал бы чужой файл
        if os.path.islink(path):
            return 'cannot edit a symlink'
        if not os.path.exists(path):
            return 'file deleted — nothing to edit'
        if not os.path.isfile(path):
            return 'not a regular file'
        # сверх этого подсветка выключена, и построчный рендер всего
        # файла на каждое нажатие уже не успевает за вводом
        if os.path.getsize(path) > MAX_HIGHLIGHT_BYTES:
            return 'file too large to edit here — e opens your editor'
        raw = read_bytes(path)
        if raw is None:
            return f'cannot read {os.path.basename(path)}'
        text = None if b'\x00' in raw else decode_editable(raw)
        if text is None:
            return 'cannot edit: binary, non-UTF-8 or CRLF file'
        return path, raw, text

    # --- вход ---

    def enter_edit(self) -> None:
        if self.editing:
            return
        if self._load_later is not None:
            # курсор дерева ушёл на другой файл, а дифф ещё не догнал:
            # править надо то, что выбрано, а не то, что пока видно
            self.load_diff()
        res = self._load_editable()
        if isinstance(res, str):
            self.flash = res
            self.draw_screen()
            return
        path, raw, text = res
        line = self._edit_start_line()
        buf = TextBuffer(text)
        display = buf.display_text()
        reloaded = text != self.diff_after
        if reloaded and self._external:
            # у внешнего файла «до» — он же на момент открытия;
            # устаревший снимок пометил бы чужие правки как свои
            self.diff_before = text
        if self.diff_src is None or self.diff_after != display or reloaded:
            self.diff_after = display
            self.diff_src = DiffSource(self.diff_before, display)
        self.editing = True
        self.edit_buf = buf
        self._edit_path, self._edit_disk = path, raw
        self._saved_annots = dict(self.annots)
        self.view_mode = 'final'
        self.focus = 'diff'
        self.diff_sel = self.diff_char_sel = None
        self.expanded = {}
        indent = buf.lines[min(line, len(buf.lines)) - 1]
        buf.set_caret(line - 1, len(indent) - len(indent.lstrip()))
        self.build_diff_rows()
        self.flash = ('file changed on disk — reloaded, editing' if reloaded
                      else 'editing — Esc to finish')
        self._sync_edit()

    def _edit_start_line(self) -> int:
        """Строка нового файла, с которой начать: под курсором диффа,
        а из дерева — верхняя видимая.
        """
        if self.focus == 'diff' and 0 <= self.diff_cur < len(self.diff_lineno):
            line = self.diff_lineno[self.diff_cur]
        else:
            line = next((ln for ln in self.diff_lineno[self.diff_offset:] if ln > 0), 1)
        return max(1, line)

    # --- синхронизация модели строк с буфером ---

    def _edited(self) -> None:
        """Буфер изменился (текст или каретка): пересобрать строки один
        раз на пачку событий — автоповтор приходит очередью.
        """
        if self._sync_pending:
            return
        loop = getattr(self, 'asyncio_loop', None)
        if loop is None:
            self._sync_edit()
            return
        self._sync_pending = True
        loop.call_soon(self._sync_edit)

    def _sync_edit(self) -> None:
        self._sync_pending = False
        buf = self.edit_buf
        if not self.editing or buf is None or self.diff_src is None:
            return
        lo, old_n, new_n = line_splice(self.diff_src.b, buf.lines)
        changed = bool(old_n or new_n)
        if changed:
            self.diff_after = buf.display_text()
            self.diff_src = self.diff_src.patched(self.diff_after, lo, old_n, new_n)
            self._shift_annots(lo, old_n, new_n)
            self._edit_gen += 1
        hscroll = self._caret_hscroll()
        if changed or hscroll != self.hscroll:
            self.hscroll = hscroll
            self.build_diff_rows()
        self.diff_cur = buf.line
        sel = buf.selection()
        if sel is not None:
            sel = tuple((ln, buf.display_col(ln, col)) for ln, col in sel)
        self._caret_snap = (buf.line, buf.display_col(*buf.caret), sel)
        self._ensure_cursor_visible()
        if changed:
            self._arm_exact()
        self.draw_screen()

    def _caret_hscroll(self) -> int:
        buf = self.edit_buf
        dcol = buf.display_col(*buf.caret)
        codew = code_width(True, self.diff_width(), self._can_revert())
        margin = min(HSCROLL_MARGIN, codew // 4)
        hscroll = self.hscroll
        if dcol < hscroll:
            hscroll = max(0, dcol - margin)
        # последнюю ячейку длинной строки занимает «…» — каретка на ней
        # стояла бы поверх многоточия
        elif dcol > hscroll + codew - 2:
            hscroll = dcol - codew + 2 + margin
        limit = self._hscroll_limit(self.diff_width(), True, self._can_revert())
        return max(0, min(hscroll, limit))

    def _hscroll_limit(self, rw: int, final: bool, reverts: bool) -> int:
        limit = super()._hscroll_limit(rw, final, reverts)
        if not self.editing or self.diff_src is None:
            return limit
        # каретке нужно встать и за последним символом самой длинной
        # строки — ещё на колонку правее обычного предела
        codew = code_width(True, rw, reverts)
        return limit + 1 if self.diff_src.longest_b >= codew else limit

    def _arm_exact(self) -> None:
        loop = getattr(self, 'asyncio_loop', None)
        if loop is None:
            return
        if self._exact_timer is not None:
            self._exact_timer.cancel()
        self._exact_timer = loop.call_later(EXACT_DELAY, self._exact_now)

    def _exact_now(self) -> None:
        self._exact_timer = None
        if not self.editing or self._exact_busy:
            return
        gen, before, after, ext = self._edit_gen, self.diff_before, self.diff_after, self.diff_ext

        def work() -> DiffSource:
            src = DiffSource(before, after)
            src.colors(ext, new=True)
            return src

        self._exact_busy = True
        self.run_background(work, lambda src: self._exact_done(gen, src))

    def _exact_done(self, gen: int, src: DiffSource) -> None:
        self._exact_busy = False
        if not self.editing:
            return
        if gen != self._edit_gen:
            # текст ушёл вперёд, пока считали. Пауза, отпущенная на
            # время счёта, уже прошла — если набор не продолжается
            # (таймер не взведён), пересчитать сразу
            if self._exact_timer is None:
                self._exact_now()
            return
        self.diff_src = src
        self.build_diff_rows()
        self.draw_screen()

    def _shift_annots(self, lo: int, old_n: int, new_n: int) -> None:
        """Комментарии держатся за номер строки: вставка и удаление
        строк сдвигают всё, что ниже, иначе замечание переехало бы на
        чужой код.
        """
        delta = new_n - old_n
        target = self._edit_target()
        if not delta or not self.annots or target is None:
            return
        rel, repo = target[0], self.view_repo
        moved = {}
        for key, note in self.annots.items():
            r, path, line = key
            if (r, path) == (repo, rel) and line > lo + min(old_n, new_n):
                line = max(lo + new_n, line + delta) if line <= lo + old_n else line + delta
                key = (r, path, max(1, line))
            if key in moved:
                note = {**moved[key], 'text': moved[key]['text'] + '\n' + note['text']}
            moved[key] = note
        self.annots = moved

    # --- ввод ---

    def _edit_key(self, key_event) -> bool:
        if not self.editing:
            return False
        buf = self.edit_buf
        self._paste_cr = False
        if chord(key_event, 'super+shift', 'c'):
            return False            # @path#L — как вне правки
        if chord(key_event, 'super', 's'):
            self.save_edit()
            return True
        if chord(key_event, 'super', 'z'):
            if not buf.undo():
                self.flash = 'nothing to undo'
            self._edited()
            return True
        if chord(key_event, 'super+shift', 'z'):
            if not buf.redo():
                self.flash = 'nothing to redo'
            self._edited()
            return True
        if chord(key_event, 'super', 'a'):
            buf.select_all()
            self._edited()
            return True
        if chord(key_event, 'super', 'c'):
            self._copy_edit(cut=False)
            return True
        if chord(key_event, 'super', 'x'):
            self._copy_edit(cut=True)
            return True
        self._edit_special(key_event)
        # прочие клавиши глотаем: вне правки они — команды экрана, и
        # случайный Home не должен уводить курсор дерева
        return True

    def _edit_special(self, key_event) -> None:
        buf = self.edit_buf
        k = key_event.key
        shift = bool(getattr(key_event, 'shift', False))
        alt = bool(getattr(key_event, 'alt', False))
        sup = bool(getattr(key_event, 'super', False))
        if k in _SUPER_MOVES and sup:
            buf.move(_SUPER_MOVES[k], shift)
        elif k in ('LEFT', 'RIGHT') and alt:
            buf.move('word_' + k.lower(), shift)
        elif k in _MOVE_KEYS:
            buf.move(_MOVE_KEYS[k], shift, page=max(1, self.diff_visible_rows() - 1))
        elif k == 'ENTER':
            buf.newline()
        elif k == 'BACKSPACE':
            if sup and buf.selection() is None:
                buf.set_caret(buf.line, 0, extend=True)
                buf.backspace()
            elif alt:
                buf.delete_word_left()
            else:
                buf.backspace()
        elif k == 'DELETE':
            buf.delete()
        elif k == 'TAB':
            if shift:
                buf.outdent()
            else:
                buf.indent()
        elif k == 'ESCAPE':
            if buf.selection() is not None:
                buf.anchor = None
            else:
                self.leave_edit()
                return
        else:
            return
        self._edited()

    def _edit_text(self, text: str, in_bracketed_paste: bool) -> bool:
        if not self.editing:
            if (not in_bracketed_paste and not self.find_mode
                    and len(text) == 1 and to_latin(text) in ('i', 'I')):
                self.enter_edit()
                return True
            return False
        buf = self.edit_buf
        if in_bracketed_paste:
            # \r\n может разорваться между кусками вставки. \r в конце
            # куска сразу становится переводом строки — придержать его
            # до следующего нечем: конца вставки кит не видит, — а \n в
            # начале следующего куска — его хвост, не второй перевод
            if self._paste_cr and text.startswith('\n'):
                text = text[1:]
            self._paste_cr = text.endswith('\r')
            buf.insert(text)
        else:
            self._paste_cr = False
            # C0 вне вставки — это ⌃-клавиши на кириллице, а не текст
            for ch in text:
                if ch >= ' ' and ch != '\x7f':
                    buf.type_char(ch)
        self._edited()
        return True

    def revert_hunk_in_buffer(self, di: int) -> None:
        """Маркер отката в режиме правки: блок возвращается в буфер, а
        не на диск — это обычная правка, её откатывает ⌘z.
        """
        self._sync_edit()
        op = self._hunk_op_at(di)
        if op is None:
            return
        _tag, i1, i2, j1, j2 = op
        self.edit_buf.replace_lines(j1, j2, list(self.diff_src.a[i1:i2]))
        self.flash = 'hunk reverted — ⌘z to undo'
        self._edited()

    def _copy_edit(self, cut: bool) -> None:
        buf = self.edit_buf
        text = buf.selected_text()
        if text:
            if cut:
                buf.insert('')
        else:
            # без выделения — строка целиком, как в IDE
            text = buf.lines[buf.line] + '\n'
            if cut:
                buf.replace_lines(buf.line, buf.line + 1, [])
        self._copy_clipboard(text)
        n = text.count('\n')
        what = f'{n} lines' if n > 1 else ('line' if n else f'{len(text)} chars')
        self.flash = f'{"cut" if cut else "copied"} {what}'
        self._edited()

    def commit_input(self) -> None:
        mode = self.input_mode
        super().commit_input()
        if not (self.editing and mode == 'search' and self.search_matches):
            return
        # Enter в поиске ставит каретку на найденное и выделяет его —
        # дальше его и правят
        row = self.search_matches[self.search_idx]
        buf = self.edit_buf
        if row >= len(buf.lines):
            return
        col = buf.lines[row].lower().find(self.search_query.lower())
        if col >= 0:
            buf.select((row, col), (row, col + len(self.search_query)))
            self._edited()

    # --- сохранение ---

    def _write_edit(self) -> WriteResult:
        """'disk' — файл поменяли без нас, 'error' — не записался."""
        buf = self.edit_buf
        if buf is None or not buf.modified:
            return 'clean'
        disk = read_bytes(self._edit_path)
        data = buf.text().encode('utf-8')
        if disk != self._edit_disk and disk != data:
            return 'disk'
        return self._write_buffer(data)

    def _write_buffer(self, data: bytes) -> "Literal['saved', 'error']":
        if not write_text(self._edit_path, self.edit_buf.text()):
            self._conflict_err = last_error()
            return 'error'
        self._edit_disk = data
        self.edit_buf.mark_saved()
        self._saved_annots = dict(self.annots)
        return 'saved'

    def save_edit(self) -> None:
        if not self.editing:
            return
        res = self._write_edit()
        if res in ('disk', 'error'):
            self._ask(res, leave=False, then=None, reshow=True)
            return
        self.flash = ('saved' if res == 'saved' else 'no changes to save')
        self.draw_screen()

    def leave_edit(self, then: 'Callable[[], None] | None' = None, reshow: bool = True) -> bool:
        """Сохранить и выйти из режима, затем выполнить then. False —
        на диске конфликт, и решает пользователь (then ждёт ответа).

        reshow — перед then показать файл с диска и пересканировать
        дерево: сохранённый файл мог стать чистым или, наоборот, впервые
        изменённым, а then (переход, ⌃o, клик по дереву) должен застать
        экран, где дерево и панель говорят об одном. Выходу из кита это
        ни к чему.
        """
        if not self.editing:
            if then is not None:
                then()
            return True
        res = self._write_edit()
        if res in ('disk', 'error'):
            self._ask(res, leave=True, then=then, reshow=reshow)
            return False
        self._finish_leave(then, reshow)
        return True

    def _ask(self, kind: "Literal['disk', 'error']", leave: bool,
             then: 'Callable[[], None] | None', reshow: bool) -> None:
        self._conflict = kind
        self._conflict_next = (leave, then, reshow)
        self.draw_screen()

    def _finish_leave(self, then: 'Callable[[], None] | None', reshow: bool) -> None:
        target = self._edit_target()
        line, offset = self.edit_buf.line + 1, self.diff_offset
        self._close_edit()
        if reshow and target is not None:
            self._reshow(*target, line, offset)
        if then is not None:
            then()
        self.draw_screen()

    def _close_edit(self) -> None:
        self.editing = False
        self.edit_buf = None
        self._caret_snap = None
        self._conflict = ''
        self._paste_cr = False
        self._edit_drag = False
        self._edit_gen += 1
        if self._exact_timer is not None:
            self._exact_timer.cancel()
            self._exact_timer = None

    def _reshow(self, rel: str, repo: 'str | None', line: int, offset: int) -> None:
        """Показать файл с диска после правки. Дерево пересканируем:
        правка могла сделать файл чистым (он уйдёт из дерева — покажем
        его как есть) или впервые изменённым (появится — встанем на
        него).
        """
        if not self.find_mode:
            self._reload_items()
            self.rebuild_tree()
        row = None
        if self._item_at(rel, repo) is not None:
            self._reveal_file(rel, repo)
            row = self._tree_row_for(rel, repo)
        if row is not None:
            self._external = None
            self.set_tsel(row)
            self.load_diff()
        else:
            self._show_file(rel, line, repo)
        self.focus = 'diff'
        self.diff_cur = max(0, min(line - 1, len(self.diff_rows) - 1))
        self.diff_offset = min(offset, self.diff_offset_max())

    def _discard_edit(self) -> None:
        # своё не пишем, но и не теряем: текст — в буфер обмена, а
        # комментарии — туда, где стояли до несохранённой правки
        self._copy_clipboard(self.edit_buf.text())
        self.annots = self._saved_annots

    def _abandon_edit(self) -> None:
        """Файл уходит с экрана путём, где спросить уже нельзя: пишем,
        если можно, а при конфликте правка не пропадает — она в буфере
        обмена.
        """
        if self._write_edit() in ('disk', 'error'):
            self._discard_edit()
            self.flash = 'file changed on disk — your edit is in the clipboard'
        self._close_edit()

    # --- подтверждение после неудачной записи ---

    def _pending_active(self) -> bool:
        return bool(self._conflict) or super()._pending_active()

    def _pending_prompt(self) -> str:
        if not self._conflict:
            return super()._pending_prompt()
        if self._conflict == 'disk':
            head, retry = ' file changed on disk while editing', 'y overwrite'
        else:
            head, retry = f' save failed: {self._conflict_err}', 'y retry'
        return f'{head} — {retry} · d discard mine (copied) · any other key — keep editing'

    def _confirm_pending(self) -> None:
        if not self._conflict:
            super()._confirm_pending()
            return
        leave, then, reshow = self._conflict_next
        self._conflict = ''
        if self._write_buffer(self.edit_buf.text().encode('utf-8')) == 'error':
            self._ask('error', leave, then, reshow)
            return
        if leave:
            self._finish_leave(then, reshow)
        else:
            self.flash = 'saved'
            self.draw_screen()

    def _pending_text(self, ch: str) -> bool:
        if not self._conflict:
            return super()._pending_text(ch)
        if ch not in ('d', 'D'):
            return False
        _leave, then, reshow = self._conflict_next
        self._discard_edit()
        self.flash = 'discarded — your version is in the clipboard'
        self._finish_leave(then, reshow)
        return True

    def _cancel_pending(self) -> None:
        if not self._conflict:
            super()._cancel_pending()
            return
        self._conflict = ''
        self.flash = 'still editing — not saved'
        self.draw_screen()

    # --- уход с файла: сначала сохранить ---

    def _navigate(self, target, repo: 'str | None' = None) -> None:
        nav = super()._navigate
        if not self.editing:
            nav(target, repo)
            return
        here = self._edit_target()
        if here is not None and (target.path, repo or self.view_repo) == (here[0], self.view_repo):
            # определение в том же файле — просто перевести каретку
            text = self.edit_buf.lines[max(0, min(target.line, len(self.edit_buf.lines)) - 1)]
            self.edit_buf.set_caret(target.line - 1, len(text) - len(text.lstrip()))
            self.flash = f'line {target.line}'
            self._edited()
            return
        self.leave_edit(lambda: nav(target, repo))

    def nav_back(self) -> None:
        back = super().nav_back
        if self.editing:
            self.leave_edit(back)
        else:
            back()

    def toggle_find(self) -> None:
        toggle = super().toggle_find
        if self.editing:
            self.leave_edit(toggle)
        else:
            toggle()

    def _ctrl_key(self, letter: str) -> bool:
        if letter == 'c' and self.editing:
            self.leave_edit(lambda: self.quit_loop(0), reshow=False)
            return True
        return super()._ctrl_key(letter)

    def on_interrupt(self) -> None:
        if self.editing:
            self.leave_edit(lambda: self.quit_loop(0), reshow=False)
            return
        super().on_interrupt()

    def load_diff(self) -> None:
        # страховка для путей, которые в режиме правки недостижимы
        # клавишами (refresh, фильтр, фокус репозитория)
        if self.editing:
            self._abandon_edit()
        super().load_diff()

    def _show_file(self, rel: str, line: int, repo: 'str | None' = None) -> None:
        if self.editing:
            self._abandon_edit()
        super()._show_file(rel, line, repo)

    def on_resize(self, new_size) -> None:
        super().on_resize(new_size)
        if self.editing:
            self._sync_edit()      # ширина кода сменилась — каретку в кадр

    def finalize(self) -> None:
        # кит закрыли снаружи (открыли log или sessions поверх):
        # спросить уже некого — пишем, только если файл никто не трогал,
        # иначе правка уходит в буфер обмена
        if self.editing:
            self._abandon_edit()
        self._set_beam(False)
        super().finalize()

    # --- мышь ---

    def diff_scroll(self, delta: int) -> None:
        if not self.editing:
            super().diff_scroll(delta)
            return
        # колесо листает, не таская каретку за окном
        self.diff_offset = max(0, min(self.diff_offset_max(), self.diff_offset + delta))
        self.schedule_draw()

    def _on_mouse(self, ev) -> None:
        if not self.editing:
            super()._on_mouse(ev)
            return
        kind = getattr(ev, 'type', None)
        left = ev.buttons == MouseButton.LEFT
        press = kind == MouseEventType.PRESS and left
        if press and ev.cell_x < self.left_width():
            self._press_to_super = False
            self._tree_press(ev)
            return
        di = self._diff_row_at(ev)
        mods = getattr(ev, 'mods', 0)
        if press:
            # ⌥-клик (определение), маркер отката и всё мимо строк кода
            # — как вне правки; их отпускание тоже уйдёт базе
            self._press_to_super = bool(mods & ALT_MOD or di is None
                                        or self._revert_row_at(ev) is not None)
            if self._press_to_super:
                super()._on_mouse(ev)
            else:
                self._click_at(ev, di, bool(mods & SHIFT_MOD))
            return
        if kind == MouseEventType.MOVE and left:
            # протяжка с зажатой кнопкой — только выделение правки:
            # базе она начала бы выделение просмотрщика поверх каретки
            if self._edit_drag and di is not None:
                self.edit_buf.set_caret(*self._buf_pos(ev, di), extend=True)
                self._edited()
            self.update_pointer(ev)
            return
        if kind == MouseEventType.RELEASE:
            self._edit_drag = False
            if not self._press_to_super:
                self.update_pointer(ev)
                return
            self._press_to_super = False
        super()._on_mouse(ev)

    def _tree_press(self, ev) -> None:
        """Клик по дереву уводит с файла: сначала сохранить. Строку
        узнаём до записи — сохранение пересканирует дерево, и под той
        же ячейкой может оказаться сосед. При конфликте переход ждёт
        ответа.
        """
        r = ev.cell_y - 2
        li = self.left_offset + r
        rid = (self._row_id(self.rows[li])
               if 0 <= r < self.visible_rows() and li < len(self.rows) else None)
        self.leave_edit(lambda: self._open_tree_row(rid))

    def _open_tree_row(self, rid: 'tuple | None') -> None:
        li = self._row_by_id(rid) if rid is not None else None
        if li is not None:
            self._tree_row_clicked(li)

    def _click_at(self, ev, di: int, extend: bool) -> None:
        buf = self.edit_buf
        line, col = self._buf_pos(ev, di)
        now = time.monotonic()
        double = di == self._click_di and now - self._click_t < 0.4
        self._click_di, self._click_t = di, now
        span = word_span(self.diff_plain[di], self._diff_col_at(ev)) if double else None
        if span and line == di:
            off = self._gutter_cols() + 2
            a = buf.col_at_display(line, max(0, span[0] - off))
            b = buf.col_at_display(line, max(0, span[1] - off))
            buf.select((line, a), (line, b))
        else:
            buf.set_caret(line, col, extend=extend)
            self._edit_drag = True
        self._edited()

    def _buf_pos(self, ev, di: int) -> 'tuple[int, int]':
        buf = self.edit_buf
        line = max(0, min(di, len(buf.lines) - 1))
        dcol = max(0, self._diff_col_at(ev) - self._gutter_cols() - 2)
        return line, buf.col_at_display(line, dcol)

    # --- отрисовка ---

    def _diff_cell(self, di: int, rw: int, cur_rel: 'str | None', cur_match: int) -> str:
        snap = self._caret_snap if self.editing else None
        sel = snap[2] if snap else None
        if sel is None or not sel[0][0] <= di <= sel[1][0] or di >= len(self.diff_plain):
            return super()._diff_cell(di, rw, cur_rel, cur_match)
        (la, da), (lb, db) = sel
        off = self._gutter_cols() + 2
        cs = off + (da if di == la else 0)
        ce = off + db if di == lb else len(self.diff_plain[di])
        saved, self.diff_char_sel = self.diff_char_sel, (di, cs, ce)
        try:
            return super()._diff_cell(di, rw, cur_rel, cur_match)
        finally:
            self.diff_char_sel = saved

    def _set_beam(self, beam: bool) -> None:
        # только на смене: форма — состояние терминала, а не кадра
        if beam != self._beam:
            self._beam = beam
            self.print(CARET_BEAM if beam else CARET_RESET, end='')

    def _draw_pane_body(self) -> None:
        super()._draw_pane_body()
        self._set_beam(self.editing)
        snap = self._caret_snap if self.editing else None
        if snap is None or self._pending_active() or not self.rows:
            return
        row, dcol = snap[0], snap[1]
        r = row - self.diff_offset
        if not 0 <= r < self.diff_visible_rows():
            return
        code_x = self.left_width() + len(SEP) + self._gutter_cols() + 2
        x = code_x + dcol - self.hscroll
        if code_x <= x < self.screen_size.cols - 2:
            self.set_caret(2 + (1 if self.sticky_line() else 0) + r, x)

    def _review_footer(self) -> str:
        if not self.editing:
            return super()._review_footer()
        return (' [edit]  type · ⇧←→ select · ⌥←→ word · ⌘z undo · ⌘⇧z redo · ⌘s save'
                ' · ⌘c ⌘x ⌘v · ⌥click def · ⌘f find · Esc done')

    def _edit_badge(self) -> str:
        if not self.editing:
            return ''
        return '   ● modified' if self.edit_buf.modified else '   editing'
