"""Режим Find in Files (⌘⇧F): живой поиск по проекту через git grep —
слева файлы с совпадениями, справа файл целиком.

Миксин к DiffTreeView: подменяет источник дерева на результаты поиска
и переопределяет то, что в этом режиме означает другое (совпадения
вместо ханков, файл как есть вместо диффа). Ищет там же, откуда взят
дифф: в рабочем дереве или в снимке коммита (`source.rev`).

Собирается только в стеке ReviewScreen: снимок вида для возврата берёт
у GotoDefinitionMixin, перечитывание файлов и сброс подтверждений —
у самого экрана.
"""

from ..keylayout import to_latin
from ..text import plural
from .git import last_error
from .grep import MAX_MATCHES, search_files
from .navdef import Target


# Короче — не ищем (живой запрос из одной буквы в большом репозитории
# совпадает почти с каждой строкой); пауза после последнего символа —
# тот же порядок, что у отложенной загрузки диффа при прокрутке дерева.
FIND_MIN = 2
FIND_DELAY = 0.2


class FindInFilesMixin:

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.find_mode = False
        self.find_query = ''
        self.find_regex = False
        self.find_truncated = False
        self._find_later = None
        # (query, regex) последнего выполненного поиска
        self._find_done: 'tuple[str, bool] | None' = None
        # состояние обычного вьюера на время поиска
        self._before_find: 'dict | None' = None

    # --- вход и выход ---

    def toggle_find(self) -> None:
        if not self.find_mode:
            self._enter_find()
        elif self.input_mode == 'find':
            self._exit_find()
        else:
            # найдя одно, обычно ищут следующее: ⌘⇧f над результатами
            # возвращает к запросу, а закрывает поиск уже из него
            self.start_input('find', self.find_query)

    def _enter_find(self) -> None:
        if not self.root:
            self.flash = 'not a git repository'
            self.draw_screen()
            return
        self._before_find = {**self._capture_view(),
                             'filter': self.filter_query,
                             'search_query': self.search_query,
                             'show_noise': self.show_noise}
        self.find_mode = True
        self.filter_query = ''
        self.search_query = ''
        self.search_matches = []
        self._external = None
        self._drop_marks()
        self._cancel_pending()
        self.view_mode = 'final'
        self.items = []
        self.find_truncated = False
        self._find_done = None
        self.status = ''
        self.rebuild_tree()
        self.load_diff()
        if self.find_query:
            self._run_find()   # повторный вход — прежний запрос ещё актуален
        self.start_input('find', self.find_query)

    def _exit_find(self) -> None:
        s = self._before_find or {}
        self.find_mode = False
        self._before_find = None
        self.input_mode = None
        self.input_buffer = ''
        if self._find_later is not None:
            self._find_later.cancel()
            self._find_later = None
        # обратно к живому вьюеру: файлы могли измениться, пока искали
        self._reload_items()
        self.filter_query = s.get('filter', '')
        self.show_noise = s.get('show_noise', False)
        self.search_query = s.get('search_query', '')
        self._restore_view(s)
        self.draw_screen()

    # --- запуск поиска ---

    def _schedule_find(self) -> None:
        """Живой запрос запускаем отложенно: git grep на каждый символ
        копил бы очередь событий, и ввод отставал бы от рук.
        """
        if self._find_later is not None:
            self._find_later.cancel()
        self._find_later = self.asyncio_loop.call_later(FIND_DELAY, self._find_now)

    def _find_now(self) -> None:
        self._find_later = None
        self._run_find()
        self.draw_screen()

    def _run_find(self) -> None:
        if self._find_later is not None:   # прямой запуск отменяет отложенный
            self._find_later.cancel()
            self._find_later = None
        if len(self.find_query) < FIND_MIN:
            self.items, self.find_truncated = [], False
            self.status = ''
        else:
            self.items, self.find_truncated = search_files(
                self.root, self.find_query, self.find_regex, self.source.rev)
            # пусто из-за ошибки git (кривой regex, index.lock) —
            # показать её, а не «no matches»
            self.status = '' if self.items else last_error()
        self._find_done = (self.find_query, self.find_regex)
        self.rebuild_tree()
        self.tsel = self._first_file()
        self.left_offset = 0
        self.load_diff()

    def toggle_find_regex(self) -> None:
        self.find_regex = not self.find_regex
        self.flash = f'regex {"on" if self.find_regex else "off"}'
        self._run_find()
        self.draw_screen()

    # --- чем поиск отличается от диффа ---

    def load_diff(self) -> None:
        if self.find_mode:
            self.search_query = (self.find_query
                                 if len(self.find_query) >= FIND_MIN else '')
        super().load_diff()
        if self.find_mode:
            self.search_idx = 0
            if self.search_matches:
                self.diff_cur = self.search_matches[0]
                self._scroll_to_match()

    def build_diff_rows(self) -> None:
        """В поиске прыжки [ ] ведут по совпадениям — «ханков» у файла
        нет. Подмена именно здесь: resize и h/l перестраивают модель и
        вернули бы ханки изменений.
        """
        super().build_diff_rows()
        if self.find_mode and self.search_matches:
            self.diff_hunks = list(self.search_matches)

    def _recompute_matches(self) -> None:
        """В поиске совпадения — по номерам строк из git grep, а не по
        подстроке: только так regex и smart-case на экране сходятся
        с результатом.
        """
        if not self.find_mode:
            super()._recompute_matches()
            return
        it = self.current_item()
        linenos = {ln for ln, _ in it.get('lines', ())} if it else set()
        self.search_matches = [i for i, ln in enumerate(self.diff_lineno)
                               if ln > 0 and ln in linenos]
        if self.search_idx >= len(self.search_matches):
            self.search_idx = 0

    def search_next(self, direction: int) -> None:
        """В поиске n/N ведёт и курсор: в regex-режиме вхождение внутри
        строки не подсвечивается (render_match ищет подстроку), строку
        показывает курсор.
        """
        if not self.find_mode:
            super().search_next(direction)
            return
        if not self.search_matches:
            return
        self.search_idx = (self.search_idx + direction) % len(self.search_matches)
        self.diff_cur = self.search_matches[self.search_idx]
        self._scroll_to_match()
        self.draw_screen()

    def toggle_view_mode(self) -> None:
        if self.find_mode:
            self.flash = 'find shows the file as is'
            self.draw_screen()
            return
        super().toggle_view_mode()

    def open_match_in_view(self) -> None:
        """Enter на совпадении: выйти из поиска и открыть файл штатной
        навигацией вьюера — изменённый попадёт в свой дифф
        (комментарии, stage), прочие — в тот же read-only вид, что
        go-to-definition; ⌃o возвращает.
        """
        it = self.current_item()
        if not it:
            return
        line = 0
        if 0 <= self.diff_cur < len(self.diff_lineno):
            line = self.diff_lineno[self.diff_cur]
        rel = it['path']
        self._exit_find()
        self._navigate(Target(rel, max(1, line), 'def', ''))

    # --- шапка и футер режима ---

    def find_header(self, base: str) -> str:
        header = f' {base} — find'
        if self.find_query:
            header += f' ‘{self.find_query}’'
        if self.n_files:
            total = sum(len(it.get('lines', ())) for it in self.filtered)
            header += (f' — {plural(total, "match", "matches")}'
                       f' in {plural(self.n_files, "file")}')
            if self.find_truncated:
                header += f' (first {MAX_MATCHES})'
        cur = self.current_item()
        if cur:
            header += f'   ▸ {cur["path"]}'
        return header

    def find_footer(self) -> str:
        if self.focus == 'diff':
            return (' [file]  ↑↓ line · n/N match · Enter open in review'
                    ' · ⌘c copy · ⌘⇧c @path#L · e edit · ⌘f query'
                    ' · ←/Tab files · Esc back')
        rx = 'on' if self.find_regex else 'off'
        return (f' [files]  ↑↓ file · Enter/→ open · ⌘f query · n/N match'
                f' · x regex:{rx} · ⌘c @path · e edit · r rescan'
                ' · u ignored · Esc back · q')

    # --- ввод ---

    def find_key(self, k: str) -> None:
        if k == 'ENTER' and self.current_item():
            if self.focus == 'tree':
                self.set_focus('diff')
            else:
                self.open_match_in_view()
            return
        if k == 'ESCAPE':
            # свой каскад: базовый шаг «очистить search_query» здесь не
            # годится — подсветка запроса и есть результат поиска
            if self.diff_sel is not None or self.diff_char_sel is not None:
                self.diff_sel = self.diff_char_sel = None
                self.draw_screen()
            elif self.focus == 'diff':
                self.set_focus('tree')
            else:
                self._exit_find()
            return
        self.diff_common_key(k)

    def find_text(self, text: str) -> None:
        for ch in text:
            c = to_latin(ch)
            if c in ('q', 'Q'):
                self.quit_loop(0)
                return
            if self.diff_common_text(ch):
                continue
            if c in ('e', 'E'):
                self.open_editor()
                return
            if c in ('r', 'R'):
                self._run_find()
                self.flash = 'rescanned'
                self.draw_screen()
            elif c in ('f', 'F'):
                self.start_search()
            elif c in ('x', 'X'):
                self.toggle_find_regex()
