"""Экран ревью: двухпанельный просмотр изменений со всем, что к нему
прилагается — комментарии к строкам, go-to-definition, Find in Files,
метки файлов, фильтр дерева и открытие в редакторе.

Собирает механику панелей (DiffTreeView) с миксинами и разбирает ввод
общий для обоих китов. Что именно ревьюим — задаёт источник
(modules.vcs.source): review отдаёт рабочее дерево, log — коммит.
Хосту остаётся своя специфика: шапка, дно каскада Esc, опасные
действия под «y» и собственные клавиши (`_host_key`/`_host_text`).
"""

import os
import subprocess
from typing import ClassVar

from kittens.tui.loop import EventType as MouseEventType
from kittens.tui.loop import Loop, MouseButton
from kittens.tui.operations import styled
from kitty.key_encoding import EventType

from ..keylayout import chord, ctrl_letter, to_latin
from ..text import short_path, truncate
from .annotate import AnnotationsMixin
from .editor import editor_command
from .find import FIND_MIN, FindInFilesMixin
from .git import last_error
from .goto import ALT_MOD, GotoDefinitionMixin
from .navdef import word_span
from .source import Source
from .view import SHIFT_MOD, DiffTreeView


class ReviewScreen(FindInFilesMixin, GotoDefinitionMixin, AnnotationsMixin,
                   DiffTreeView):

    multiline_modes: ClassVar[tuple[str, ...]] = ('comment',)

    def __init__(self, root: 'str | None', source: Source) -> None:
        self.source = source     # до super(): корень экрана живёт в источнике
        super().__init__(root)
        # что сделать после выхода (open in editor / send to claude)
        self.action: 'dict | None' = None
        self.filter_query = ''

    @property
    def root(self) -> 'str | None':
        """Корень репозитория — один на экран и его источник:
        разъехавшись, они читали бы разные репозитории.
        """
        return self.source.root

    @root.setter
    def root(self, value: 'str | None') -> None:
        self.source.root = value

    # --- хуки хоста ---

    def _header(self) -> str:
        raise NotImplementedError

    def _host_key(self, k: str) -> bool:
        """Клавиша, которую общий разбор не понял. True — обработана."""
        return False

    def _host_text(self, ch: str) -> bool:
        """Символ мимо общих команд. True — кит закрывается."""
        return False

    def _escape_bottom(self) -> None:
        """Дно каскада Esc: выход или возврат на предыдущий экран."""
        raise NotImplementedError

    def _tree_actions(self) -> str:
        """Действия хоста в футере дерева (stage/revert у review)."""
        return ''

    def _back_hint(self) -> str:
        """Куда ведёт Esc из диффа, если это не выход."""
        return ''

    # опасное действие ждёт подтверждения «y» (revert правок, push)
    def _pending_active(self) -> bool:
        return False

    def _pending_prompt(self) -> str:
        return ''

    def _confirm_pending(self) -> None:
        pass

    def _cancel_pending(self) -> None:
        pass

    # --- источник данных ---

    def set_source(self, source: Source) -> None:
        """Сменить ревьюемое (log: выбран другой коммит). Состояние
        предыдущего просмотра не наследуем — это другой набор файлов.
        """
        self.source = source
        self._external = None
        self._navstack = []
        self.collapsed = set()
        self.show_noise = False
        self.focus = 'tree'
        self._reset_search()
        self.load_source()

    def _contents(self, it: dict) -> tuple[str, str]:
        if self.find_mode:
            text = self.source.read(it['path'])
            return text, text   # результат поиска — файл как есть, без диффа
        return self.source.contents(it)

    def _reload_items(self) -> None:
        if not self.root:
            self.items = []
            self.status = 'not a git repository'
            return
        self.items = self.source.files()
        # пустой список из-за ошибки git — показать её, а не
        # «no changes»
        self.status = '' if self.items else last_error()

    def load_source(self) -> None:
        self._reload_items()
        self.filter_query = ''
        self.rebuild_tree()
        self.tsel = self._first_file()
        self.left_offset = 0
        self.load_diff()

    def load_state(self) -> None:
        self.load_source()

    def refresh(self) -> None:
        """Пересканировать изменения, сохранив фильтр, сворачивание,
        выделение и позицию скролла диффа (не прыгать на начало) —
        удобно пока агент дописывает код.
        """
        off, hs = self.diff_offset, self.hscroll
        self._reload_items()
        self.rebuild_tree()          # сохраняет выделение по ключу/idx
        self.load_diff()             # сбрасывает diff_offset/hscroll в 0
        if hs:
            self.hscroll = hs
            self.build_diff_rows()
        self.diff_offset = min(off, self.diff_offset_max())
        self.draw_screen()

    # --- хуки DiffTreeView ---

    def _tree_visible(self, it: dict) -> bool:
        q = self.filter_query.lower()
        return not q or q in os.path.basename(it['path']).lower()

    def _empty_pane_msg(self) -> str:
        if self.find_mode:
            if len(self.find_query) < FIND_MIN:
                return f'type to search ({FIND_MIN}+ characters)'
            return 'no matches'
        if self.filter_query:
            return 'no matches'
        return 'no changes' if self.source.mutable else 'no file changes'

    def _focus_landing(self, start: int) -> int:
        if self.find_mode:
            nxt = next((m for m in self.search_matches if m >= start), None)
            return nxt if nxt is not None else self._first_landable(start)
        return self._first_commentable(start)   # курсор встаёт на строку кода

    def _diff_annotated(self, di: int, cur_rel: 'str | None') -> bool:
        return False if self.find_mode else super()._diff_annotated(di, cur_rel)

    def _diff_line_clicked(self, di: int, double: bool, col: int) -> None:
        # клик по номеру строки (левее гуттера) → комментарий к ней
        if col < self._gutter_cols():
            if self._commentable(di):
                self.start_comment()
            else:
                self.draw_screen()
            return
        # двойной клик по коду → выделить слово под курсором
        if double and not self._external:
            span = word_span(self.diff_plain[di], col)
            if span:
                self.diff_char_sel = (di, *span)
                self.diff_sel = None
                self.flash = 'selected — ⌘c to copy'
        self.draw_screen()

    def _ro_block(self) -> bool:
        """Правки (комментарий/stage/revert) в read-only внешнем файле и
        в результатах поиска бессмысленны: tree-item там чужой.
        """
        if self._external or self.find_mode:
            self.flash = ('read-only (find in files)' if self.find_mode
                          else 'read-only (external file)')
            self.draw_screen()
            return True
        return False

    # --- открытие файла в редакторе ---

    def open_editor(self) -> None:
        """Открыть текущий файл на видимой сверху строке. GUI-редактор
        (IDE) запускаем тут же, не закрывая оверлей; терминальный
        ($EDITOR=vim) — выходим и открываем в табе.
        """
        it = self.current_item()
        if not it:
            return
        path = os.path.join(self.root, it['path'])
        line = 1
        # в поиске — текущее совпадение, в ревью — видимая сверху
        di = self.diff_cur if self.find_mode else self.diff_offset
        if 0 <= di < len(self.diff_lineno):
            line = max(1, self.diff_lineno[di])
        project = self.root or os.path.dirname(path)
        cmd, gui = editor_command(project, path, line)
        if gui:
            # start_new_session: жизнь редактора не должна зависеть от
            # процесса оверлея
            try:
                subprocess.Popen(cmd, cwd=project, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                self.flash = f'opened  {os.path.basename(path)}:{line}'
            except OSError as e:
                self.flash = f'editor failed: {e}'
            self.draw_screen()
            return
        # терминальный редактор — открываем в новом табе kitty
        # (через handle_result)
        self.action = {'action': 'edit', 'path': path, 'line': line, 'cwd': project}
        self.quit_loop(0)

    # --- отрисовка ---

    def _draw_frame(self) -> None:
        if self.draw_quit_confirm():
            return
        if self._cand is not None:
            self._draw_picker()
            return
        self.cmd.clear_screen()
        self._draw_review()

    def _draw_review(self) -> None:
        cols = self.screen_size.cols
        header = self.find_header(short_path(self.root or os.getcwd())) \
            if self.find_mode else self._header()
        self.print(styled(truncate(header, cols), fg='green', bold=True))
        self.print(styled('─' * cols, fg='gray'))
        self._draw_pane_body()
        self._draw_input_line()
        danger = self._pending_active()
        foot_fg = 'red' if danger else ('green' if self.flash else 'gray')
        self.print(styled(truncate(self._footer(), cols), fg=foot_fg, bold=danger),
                   end='')
        self.flash = ''

    def _footer(self) -> str:
        if self._pending_active():
            return self._pending_prompt()
        if self.input_mode == 'comment':
            return (' Enter — save   Shift+Enter — new line   ⌃w erase word'
                    '   ⌃u erase all   Esc — cancel   (empty = delete)')
        if self.input_mode == 'find':
            return ' Enter — search   ⌃w erase word   ⌃u erase all   Esc — cancel'
        if self.input_mode:
            return ' Enter — keep   ⌃w erase word   ⌃u erase all   Esc — clear'
        if self.flash:
            return ' ' + self.flash
        if self.find_mode:
            return self.find_footer() + self._footer_tail()
        return self._review_footer() + self._footer_tail()

    def _review_footer(self) -> str:
        modes = self._mode_hints()
        back = ' · ⌃o back' if self._navstack else ''
        if self._external:
            return f' [read-only]  ↑↓ scroll · [ ] hunk · h/l scroll · ⌥/d def{back} · q'
        if self.focus == 'diff':
            if self.diff_sel is not None or self.diff_char_sel is not None:
                base = ' [diff]  drag selects (line/text) · ⌘c copy · d def · Esc clear'
            else:
                act = ('Enter expand' if self._gap_at(self.diff_cur) is not None
                       else 'Enter/c comment')
                base = (f' [diff]  ↑↓ line · {act} · ⌥/d def · ⌘c copy · [ ] hunk'
                        f' · h/l scroll · {modes} · w export · ←/Tab tree'
                        f' · e edit{back}{self._back_hint()}')
        else:
            u = 'u show-ignored' if not self.show_noise else 'u hide-ignored'
            n_marked = len(self._marked_paths())
            copy = f'⌘c copy {n_marked}' if n_marked else '⌘c @path'
            base = (f' [tree]  ↑↓ file · ⇧↑↓/⇧click mark · Enter fold · →/Tab diff'
                    f' · {copy} · {modes}{self._tree_actions()} · e edit · r refresh'
                    f' · ⌘f search · ⌘⇧f find · f filter · {u} · q'
                    f'{self._back_hint()}')
        if self.annots:
            base += (f'   ·   ✎ {len(self.annots)}'
                     ' ({} nav · w copy+clear · s send · x clear)')
        return base

    # --- строка ввода: фильтр, поиск, запрос find, комментарий ---

    def start_filter(self) -> None:
        self.start_input('filter', self.filter_query)

    def start_search(self) -> None:
        if self.find_mode:
            self.start_input('find', self.find_query)
        else:
            super().start_search()

    def _apply_filter(self) -> None:
        self.filter_query = self.input_buffer
        self.tsel = 0
        self.rebuild_tree()
        self.load_diff()
        self.draw_screen()

    def _input_live(self) -> None:
        if self.input_mode == 'filter':
            self._apply_filter()
        elif self.input_mode == 'search':
            self.apply_search_input()
        elif self.input_mode == 'find':
            self.find_query = self.input_buffer
            self._schedule_find()
            self.draw_screen()
        else:
            self.draw_screen()

    def commit_input(self) -> None:
        if self.input_mode == 'comment' and self.comment_target:
            self._save_comment()
        elif (self.input_mode == 'find'
                and self._find_done != (self.find_query, self.find_regex)):
            self._run_find()
        super().commit_input()

    def _input_cancelled(self, mode: str) -> None:
        if mode == 'filter':
            self.filter_query = ''
            self.tsel = 0
            self.rebuild_tree()
            self.load_diff()
        elif mode == 'search':
            self._reset_search()
        elif mode == 'find':
            # Esc отменяет правку запроса: на экране остаётся
            # выполненный поиск, недопечатанный хвост не доискивается
            if self._find_later is not None:
                self._find_later.cancel()
                self._find_later = None
            self.find_query = self._find_done[0] if self._find_done else ''
        elif mode == 'comment':
            self.comment_target = None

    # --- клавиатура ---

    def on_key(self, key_event) -> None:
        if key_event.type == EventType.RELEASE:
            return
        if self.confirm_key(key_event):
            return
        if self._cand is not None:
            if key_event.key == 'ESCAPE':
                self._close_picker()
            return   # пока пикер открыт — глотаем прочие клавиши
        if self._pending_active():
            # печатаемое (в т.ч. сам «y») разбирает on_text; здесь
            # гасим только Enter/стрелки/Esc: необратимое не должно
            # подтверждаться ничем, кроме явного «y»
            if not getattr(key_event, 'text', ''):
                self._cancel_pending()
            return
        for letter in ('c', 'w', 'u', 'o', 'd'):
            if chord(key_event, 'ctrl', letter):
                if self._ctrl_key(letter):
                    return
                break
        # ⌘⇧f и до строки ввода: выйти из поиска можно прямо из правки
        # запроса; комментарий терять нельзя
        if chord(key_event, 'super+shift', 'f') and self.input_mode != 'comment':
            self.toggle_find()
            return
        k = key_event.key
        if self.input_key(k, shift=bool(getattr(key_event, 'shift', False))):
            return
        if chord(key_event, 'super', 'f'):
            self.start_search()
            return
        if chord(key_event, 'super+shift', 'c'):
            self.smart_copy_location()
            return
        if chord(key_event, 'super', 'c'):
            self.smart_copy()
            return
        if self.find_mode:
            self.find_key(k)
            return
        if (getattr(key_event, 'shift', False) and self.focus == 'tree'
                and k in ('UP', 'DOWN')):
            self.mark_move(-1 if k == 'UP' else 1)
            return
        if self.diff_common_key(k):
            return
        self._review_key(k)

    def _review_key(self, k: str) -> None:
        if k == 'HOME':
            self._drop_marks()
            self.set_tsel(0)
            self.load_diff()
            self.draw_screen()
        elif k == 'END':
            self._drop_marks()
            self.set_tsel(len(self.rows) - 1)
            self.load_diff()
            self.draw_screen()
        elif k == 'ENTER':
            self.start_comment()   # общий разбор оставил Enter на строке кода
        elif self._host_key(k):
            return
        elif k == 'ESCAPE':
            # каскад: метки → фильтр → дно (выход либо прошлый экран)
            if self.marked_paths:
                self.clear_marks()
            elif self.filter_query:
                self._input_cancelled('filter')
                self.draw_screen()
            else:
                self._escape_bottom()

    def _ctrl_key(self, letter: str) -> bool:
        """Ctrl-хоткеи — общая точка для on_key и on_text: на кириллице
        ctrl+буква приходит C0-байтом, а не key-событием (ctrl_letter).
        """
        if letter == 'c':
            self.quit_loop(0)
            return True
        if self.input_mode:
            # пока пишем в строку ввода, ⌃u/⌃w правят текст, а не
            # скроллят дифф: скроллить всё равно незачем
            if letter == 'w':
                self.input_kill_word()
                return True
            if letter == 'u':
                self.input_kill_all()
                return True
            return False
        if letter == 'o':
            if not self.find_mode:   # стек «назад» принадлежит вьюеру
                self.nav_back()
            return True
        if letter == 'd':
            self.diff_scroll(self.visible_rows() // 2)
            return True
        if letter == 'u':
            self.diff_scroll(-(self.visible_rows() // 2))
            return True
        return False

    def on_text(self, text: str, in_bracketed_paste: bool = False) -> None:
        if self.confirm_text(text):
            return
        if self._pending_active():
            if to_latin(text[:1]) in ('y', 'Y'):
                self._confirm_pending()
            else:
                self._cancel_pending()
            return
        if self._cand is not None:
            ch = text[:1]
            if ch.isdigit() and ch != '0':
                self._pick(int(ch) - 1)
            return
        ctrl = ctrl_letter(text, in_bracketed_paste)
        if ctrl is not None and self._ctrl_key(ctrl):
            return
        if self.input_text(text):
            return
        if self.find_mode:
            self.find_text(text)
            return
        for ch in text:
            if self._review_text(ch):
                return

    def _review_text(self, ch: str) -> bool:
        """True — дальше символы не разбираем (кит закрывается)."""
        if ch in ('{', 'Х'):    # прыжок к пред. аннотации (Shift+[; ru — Shift+х)
            self.jump_annot(-1)
            return False
        if ch in ('}', 'Ъ'):    # прыжок к след. аннотации
            self.jump_annot(1)
            return False
        c = to_latin(ch)
        if c in ('q', 'Q'):
            self.quit_loop(0)
            return True
        if self.diff_common_text(ch):
            return False
        if c in ('e', 'E'):
            self.open_editor()
            return True
        if c in ('f', 'F'):
            self.start_filter()
        elif c in ('r', 'R'):
            self.refresh()
        elif c in ('c', 'C'):
            self.start_comment()
        elif c in ('d', 'D'):
            self.goto_from_selection()
        elif c in ('w', 'W'):
            self.export_review()
        elif c in ('s', 'S'):
            self.send_review()
            return True
        elif c in ('x', 'X'):
            self.clear_annotations()
        else:
            return self._host_text(ch)
        return False

    # --- мышь ---

    def _pointer_for(self, ev) -> 'str | None':
        di = self._diff_row_at(ev)
        if di is not None and not self.find_mode:
            col = self._diff_col_at(ev)
            if self.goto_hover(di, col, getattr(ev, 'mods', 0)):
                return 'pointer'
            # над номером строки, где можно оставить комментарий
            if col < self._gutter_cols() and self._commentable(di):
                return 'pointer'
        return super()._pointer_for(ev)

    def _on_mouse(self, ev) -> None:
        press = getattr(ev, 'type', None) == MouseEventType.PRESS
        left = bool(ev.buttons & MouseButton.LEFT)
        # пикер открыт — клик выбирает кандидата (строки списка с 2-й)
        if self._cand is not None:
            if press and left:
                self._pick(ev.cell_y - 2)
            return
        # ⇧/⌥+ЛКМ по дереву — метка файла; ⌥ в диффе — go-to-definition,
        # ⇧ в диффе падает в базовый drag-select. press глотаем при
        # обработке, иначе базовый Handler синтезирует click. В поиске
        # ни меток, ни goto-definition нет
        mods = getattr(ev, 'mods', 0)
        if press and left and (mods & (SHIFT_MOD | ALT_MOD)) and not self.find_mode:
            if self._mark_click(ev):
                return
            if mods & ALT_MOD:
                self.goto_click(ev)
                return
        super()._on_mouse(ev)

    # --- жизненный цикл ---

    def on_resize(self, new_size) -> None:
        self.build_diff_rows()
        self.draw_screen()

    def on_interrupt(self) -> None:
        self.quit_loop(0)

    def on_eot(self) -> None:
        # Ctrl+D — скролл диффа на полстраницы вниз, а НЕ закрытие
        # оверлея.
        self.diff_scroll(self.visible_rows() // 2)


def run_screen(handler) -> dict:
    """Прокрутить цикл кита и вернуть результат для handle_result.

    Не None даже при выходе без действия: без результата kitty не
    вызывает handle_result — а layout вернуть надо всегда.
    """
    Loop().loop(handler)
    return handler.action or {'action': 'close'}


def apply_result(answer: 'dict | None', target_window_id: int, boss) -> None:
    """Общая часть handle_result обоих китов: вставить ревью в окно под
    оверлеем либо открыть файл в редакторе.
    """
    if not answer:
        return
    w = boss.window_id_map.get(target_window_id)
    if w is None:
        return   # исходное окно уже закрыто — не запускать «куда попало»
    if answer.get('action') == 'send':
        # paste_text уважает bracketed paste: многострочный markdown
        # ляжет в промпт claude одной вставкой, без отправки по \n
        w.paste_text(answer['text'])
        return
    if answer.get('action') != 'edit':
        return
    project, path, line = answer['cwd'], answer['path'], answer['line']
    cmd, gui = editor_command(project, path, line)
    kind = '--type=background' if gui else '--type=tab'
    boss.call_remote_control(w, ('launch', kind, '--cwd', project, *cmd))
