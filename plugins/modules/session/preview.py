"""Состояние и логика экрана предпросмотра диалога session-кита.

Владеет строками транскрипта, скроллом, фолдами, поиском и
выделением; готовит ANSI-строки для отрисовки. Не знает про
Handler: геометрию (ширину экрана и высоту видимой области)
передаёт вызывающий, печать и статусная строка — в хендлере.
"""

from kittens.tui.operations import styled

from ..highlight import SEL_RANGE_BG
from .data import Entry, load_conversation
from .transcript import Line, transcript_lines
from .util import pad, truncate


class Preview:

    def __init__(self) -> None:
        self.session: 'dict | None' = None
        self.entries: list[Entry] = []
        self.lines: list[Line] = []
        self.offset = 0
        self.expanded: set[int] = set()     # раскрытые записи и id групп (group_id)
        self.sel: 'tuple | None' = None         # (lo, hi) — выделение целых строк
        self.char_sel: 'tuple | None' = None    # (row, cs, ce) — кусок строки
        self.search_query = ''
        self.search_matches: list[int] = []
        self.search_idx = -1
        self._cache: dict = {}              # кэш строк transcript_lines
        self._cache_width = 0               # ширина, для которой он собран
        self._cache_entries: 'list[Entry] | None' = None    # …и записи (сравнение по is)

    def open(self, session: dict, width: int, vis: int) -> None:
        self.session = session
        self.entries = load_conversation(session['file'])
        self.expanded = set()
        self.clear_selection()
        self.search_query = ''
        self.search_matches = []
        self.search_idx = -1
        self.rebuild(width)
        # открываемся на свежих сообщениях: их и хотят увидеть,
        # а не начало диалога
        self.offset = self.limit(vis)

    def _render_lines(self, width: int, expanded: 'set | frozenset') -> list[Line]:
        width = max(10, width)
        if width != self._cache_width or self.entries is not self._cache_entries:
            self._cache = {}
            self._cache_width = width
            self._cache_entries = self.entries
        root = (self.session or {}).get('cwd') or ''
        return transcript_lines(self.entries, width, expanded, root,
                                cache=self._cache)

    def rebuild(self, width: int) -> None:
        self.lines = self._render_lines(width, self.expanded)

    def limit(self, vis: int) -> int:
        return max(0, len(self.lines) - vis)

    def clamp(self, vis: int) -> None:
        self.offset = max(0, min(self.offset, self.limit(vis)))

    # --- скролл и прыжки ---

    def scroll(self, delta: int, vis: int) -> None:
        self.offset = max(0, min(self.limit(vis), self.offset + delta))
        self.clear_selection()

    def jump(self, to_end: bool, vis: int) -> None:
        self.offset = self.limit(vis) if to_end else 0
        self.clear_selection()

    def jump_prompt(self, step: int, vis: int) -> str:
        """[ / ]: к предыдущей/следующей реплике пользователя —
        оглавление диалога. Возвращает сообщение для статусной
        строки; '' — прыжок удался.
        """
        rows = [i for i, ln in enumerate(self.lines) if ln.prompt]
        if step > 0:
            nxt = next((r for r in rows if r > self.offset), None)
        else:
            nxt = next((r for r in reversed(rows) if r < self.offset), None)
        if nxt is None:
            if not rows:
                return 'no prompts'
            return 'last prompt' if step > 0 else 'first prompt'
        self.offset = min(nxt, self.limit(vis))
        self.clear_selection()
        return ''

    # --- фолды ---

    def _rebuild_after_fold(self, width: int, vis: int) -> None:
        self.clear_selection()
        self.rebuild(width)
        self.clamp(vis)
        self.find_matches(width)

    def toggle_fold(self, idx: int, width: int, vis: int) -> None:
        """Клик по строке: раскрыть/свернуть одну запись."""
        if idx < 0:
            return
        self.expanded ^= {idx}
        self._rebuild_after_fold(width, vis)

    def _all_foldable(self, width: int) -> set[int]:
        """Свёрнутая группа прячет свои строки, поэтому одного прохода
        мало: раскрытая группа обнажает вложенный свёрнутый вывод.
        Идём до неподвижной точки.
        """
        ids: set[int] = set()
        while True:
            new = {ln.entry for ln in self._render_lines(width, ids)
                   if ln.entry >= 0} - ids
            if not new:
                return ids
            ids |= new

    def expand_all(self, width: int, vis: int) -> str:
        """Раскрыть ВЕСЬ свёрнутый вывод разом (как в Claude Code);
        когда раскрыто всё — свернуть обратно. Возвращает сообщение
        для статусной строки; '' — успех.
        """
        foldable = self._all_foldable(width)
        if not foldable:
            return 'nothing to expand'
        self.expanded = foldable if foldable - self.expanded else set()
        self._rebuild_after_fold(width, vis)
        return ''

    # --- выделение ---

    def clear_selection(self) -> None:
        self.sel = None
        self.char_sel = None

    def selection_text(self) -> 'tuple[str, str] | None':
        """(текст выделения, подпись для статуса); None — нет
        выделения.
        """
        if self.char_sel is not None:
            row, cs, ce = self.char_sel
            text = self.lines[row].text[cs:ce].strip()
            return text, f'copied "{truncate(text, 30)}"'
        if self.sel is not None:
            lo, hi = self.sel
            text = '\n'.join(ln.text for ln in self.lines[lo:hi + 1])
            return text, f'copied {hi - lo + 1} lines'
        return None

    # --- поиск ---

    def find_matches(self, cols: int) -> None:
        # ищем в видимой (обрезанной по ширине) части строки — как
        # подсветка: совпадение за границей экрана было бы «найдено»,
        # но невидимо
        q = self.search_query.lower()
        self.search_matches = [i for i, ln in enumerate(self.lines)
                               if q and q in truncate(ln.text, cols).lower()]
        if not self.search_matches:
            self.search_idx = -1
        elif self.search_idx >= len(self.search_matches):
            self.search_idx = 0

    def run_search(self, query: str, cols: int, vis: int) -> None:
        self.search_query = query
        self.search_idx = -1
        self.find_matches(cols)
        if not self.search_matches:
            return
        self.search_idx = 0
        for j, mi in enumerate(self.search_matches):
            if mi >= self.offset:
                self.search_idx = j
                break
        self._scroll_to_match(vis)

    def search_jump(self, step: int, vis: int) -> str:
        """Возвращает сообщение для статусной строки; '' — успех."""
        if not self.search_matches:
            if self.search_query:
                return 'no matches'
            return 'search first: press /'
        self.search_idx = (self.search_idx + step) % len(self.search_matches)
        self._scroll_to_match(vis)
        return ''

    def _scroll_to_match(self, vis: int) -> None:
        if 0 <= self.search_idx < len(self.search_matches):
            # центрируем совпадение в окне
            mi = self.search_matches[self.search_idx]
            self.offset = max(0, mi - vis // 2)

    def current_match(self) -> int:
        """Индекс строки текущего совпадения поиска; -1 — нет."""
        if self.search_matches and 0 <= self.search_idx < len(self.search_matches):
            return self.search_matches[self.search_idx]
        return -1

    # --- подготовка строк рендера ---

    def _selection_render(self, ln: Line, cols: int, row: int) -> 'str | None':
        """Фон выделения поверх строки: диапазон строк целиком,
        символьное — срезом.
        """
        line = truncate(ln.text, cols)
        if self.sel and self.sel[0] <= row <= self.sel[1]:
            return styled(pad(line, cols), bg=SEL_RANGE_BG)
        if self.char_sel and self.char_sel[0] == row:
            _, cs, ce = self.char_sel
            cs, ce = max(0, cs), min(len(line), ce)
            if cs >= ce:
                return None
            return (styled(line[:cs], fg=ln.color)
                    + styled(line[cs:ce], bg=SEL_RANGE_BG)
                    + styled(line[ce:], fg=ln.color))
        return None

    def line_render(self, row: int, cols: int, is_current: bool) -> str:
        """Строка предпросмотра: выделение, подсветка совпадений
        поиска, готовый ANSI.
        """
        ln = self.lines[row]
        sel = self._selection_render(ln, cols, row)
        if sel is not None:
            return sel
        line = truncate(ln.text, cols)
        color = ln.color
        q = self.search_query.lower()
        if not q or q not in line.lower():
            if ln.render is not None:
                return ln.render
            return styled(line, fg=color) if line else ''
        low = line.lower()
        out = ''
        i = 0
        while True:
            j = low.find(q, i)
            if j < 0:
                out += styled(line[i:], fg=color)
                break
            out += styled(line[i:j], fg=color)
            seg = line[j:j + len(q)]
            if is_current:
                out += styled(seg, fg='black', bg='green', bold=True)
            else:
                out += styled(seg, fg='black', bg='yellow')
            i = j + len(q)
        return out
