"""Базовый TUI-класс двухпанельного просмотра diff: дерево файлов
слева, unified-дифф справа. Вся навигация, скролл, гэпы, hscroll,
поиск, выделение мышью и копирование в буфер — здесь, поверх
модели строк из modules.vcs.diff. Источник данных (какие файлы
и их before/after) задаёт подкласс через хуки `_contents`;
review показывает незакоммиченные правки, log — изменения
коммита.

Хуки для подклассов:
- `_contents(it) -> (before, after)` — содержимое файла (обязателен);
- `_tree_visible(it) -> bool` — доп. фильтр дерева (по
  умолчанию всё видно);
- `_focus_landing(start) -> int` — куда встаёт курсор при входе в дифф;
- `_diff_annotated(di, cur_rel) -> bool` — маркер аннотации
  на строке (review);
- `_diff_line_clicked(di, double)` — клик по строке диффа
  (review — двойной = коммент);
- `_empty_pane_msg() -> str` — сообщение, когда файлов нет.
"""

import os
import time

from kittens.tui.handler import Handler
from kittens.tui.loop import MouseButton
from kittens.tui.operations import MouseTracking, styled

from modules.clipboard import osc52
from modules.dragselect import DragSelect
from modules.draw import AtomicDraw
from modules.inputline import InputLine
from modules.text import plural

from .diff import (
    DiffModel,
    DiffSource,
    build_tree,
    is_code_row,
    max_hscroll,
    render_diff_cell,
    unified_rows,
)
from .util import STATUS_STYLE, compose, is_noise, pad, truncate


THUMB_FG = 244   # ползунок скролла дерева: заметнее серого текста, тише белого


class DiffTreeView(AtomicDraw, InputLine, DragSelect, Handler):

    mouse_tracking = MouseTracking.buttons_and_drag

    def __init__(self, root):
        self.root = root
        self.items = []
        self.filtered = []
        self.rows = []
        self.n_files = 0
        self.collapsed = set()
        self.show_noise = False
        self.tsel = 0
        self.left_offset = 0
        self.focus = 'tree'
        self.diff_before = ''
        self.diff_after = ''
        self.diff_ext = ''
        self.diff_src = None
        self.diff_rows = []
        self.diff_plain = []
        self.diff_vis = []
        self.diff_hunks = []
        self.diff_lineno = []
        self.diff_scope = []
        self.diff_gap = []
        self.diff_kind_bg = []
        self.expanded = set()
        self.diff_offset = 0
        self.hscroll = 0
        self.hscroll_max = 0
        self.expand = False
        self.diff_cur = 0
        self.diff_sel = None            # (lo, hi) — выделение целых строк (drag через строки)
        self.diff_char_sel = None       # (row, cs, ce) — выделение куска в одной строке
        self._click_di = -1
        self._click_t = 0.0
        self.search_query = ''
        self.search_matches = []
        self.search_idx = 0
        self.status = ''
        self.flash = ''
        self._load_later = None

    # --- хуки подкласса ---

    def _contents(self, it):
        raise NotImplementedError

    def _tree_visible(self, it):
        return True

    def _focus_landing(self, start):
        return self._first_landable(start)

    def _diff_annotated(self, di, cur_rel):
        return False

    def _diff_line_clicked(self, di, double):
        self.draw_screen()

    def _empty_pane_msg(self):
        return 'no files'

    # --- геометрия ---

    def input_rows(self):
        """Высота области ввода. Растёт с текстом, но не съедает
        больше трети экрана — хвост длинного комментария виден,
        начало уезжает вверх.
        """
        if not self.input_mode:
            return 0
        cap = max(1, self.screen_size.rows // 3)
        return min(cap, len(self.input_lines(self.screen_size.cols)))

    def visible_rows(self):
        return max(1, self.screen_size.rows - 3 - self.input_rows())

    def left_width(self):
        return max(18, min(48, self.screen_size.cols * 2 // 5))

    def diff_width(self):
        # последний столбец каждой панели занят ползунком; он
        # зарезервирован всегда, иначе появление полосы дёргало бы
        # перенос строк диффа
        return max(10, self.screen_size.cols - self.left_width() - 4)

    def left_limit(self):
        return max(0, len(self.rows) - self.visible_rows())

    def clamp_left(self):
        self.left_offset = max(0, min(self.left_offset, self.left_limit()))

    def ensure_left_visible(self):
        """Подтянуть скролл дерева к выделенной строке. Зовётся при
        смене выделения, но НЕ при отрисовке: колесо скроллит
        дерево независимо от курсора.
        """
        if not self.rows:
            return
        vis = self.visible_rows()
        if self.tsel < self.left_offset:
            self.left_offset = self.tsel
        elif self.tsel >= self.left_offset + vis:
            self.left_offset = self.tsel - vis + 1
        self.clamp_left()

    def set_tsel(self, i):
        self.tsel = max(0, min(i, max(0, len(self.rows) - 1)))
        self.ensure_left_visible()

    # --- дерево файлов ---

    def rebuild_tree(self):
        prev = self.rows[self.tsel] if (self.rows and 0 <= self.tsel < len(self.rows)) else None
        self.filtered = [it for it in self.items
                         if (self.show_noise or not is_noise(it['rel']))
                         and self._tree_visible(it)]
        self.rows = build_tree(self.filtered, self.collapsed)
        # файлы, а не строки: свёрнутая папка не занижает счётчик
        self.n_files = len(self.filtered)
        self.tsel = min(self.tsel, max(0, len(self.rows) - 1))
        if prev:
            for i, r in enumerate(self.rows):
                if (prev['type'] == 'dir' and r['type'] == 'dir'
                        and r.get('key') == prev.get('key')):
                    self.tsel = i
                    break
                if (prev['type'] == 'file' and r['type'] == 'file'
                        and r.get('idx') == prev.get('idx')):
                    self.tsel = i
                    break
        self.ensure_left_visible()

    def _first_file(self):
        for i, r in enumerate(self.rows):
            if r['type'] == 'file':
                return i
        return 0

    def current_item(self):
        if not self.rows or not (0 <= self.tsel < len(self.rows)):
            return None
        row = self.rows[self.tsel]
        return self.filtered[row['idx']] if row['type'] == 'file' else None

    def toggle_noise(self):
        self.show_noise = not self.show_noise
        self.tsel = 0
        was = self.n_files
        self.rebuild_tree()
        # прибавка может целиком уехать в свёрнутую группу — тогда
        # без счётчика не видно, что u вообще сработал
        delta = abs(self.n_files - was)
        self.flash = (f'showing {plural(delta, "ignored file")}' if self.show_noise
                      else f'hiding {plural(delta, "ignored file")}')
        self.set_tsel(self._first_file())
        self.load_diff()
        self.draw_screen()

    def set_fold(self, collapse):
        if not self.rows or self.rows[self.tsel]['type'] != 'dir':
            return
        key = self.rows[self.tsel]['key']
        if collapse:
            self.collapsed.add(key)
        else:
            self.collapsed.discard(key)
        self.rebuild_tree()
        self.load_diff()
        self.draw_screen()

    def toggle_fold(self):
        if self.rows and self.rows[self.tsel]['type'] == 'dir':
            self.set_fold(self.rows[self.tsel]['key'] not in self.collapsed)

    def tree_move(self, delta):
        if not self.rows:
            return
        prev = self.tsel
        self.set_tsel(self.tsel + delta)
        if self.tsel != prev:
            self._schedule_load_diff()
        self.draw_screen()

    def tree_scroll(self, delta):
        """Двигает окно, но не выделение: колесо не должно
        перезагружать дифф на каждый щелчок.
        """
        if not self.rows:
            return
        self.left_offset = max(0, min(self.left_limit(), self.left_offset + delta))
        self.draw_screen()

    def _schedule_load_diff(self):
        """Дифф при прокрутке дерева грузим отложенно: git show +
        разбор дороже кадра, синхронная загрузка на каждый шаг колеса
        копит очередь событий и курсор «догоняет» с лагом. Курсор
        двигается сразу, дифф — когда прокрутка утихла.
        """
        if self._load_later is not None:
            self._load_later.cancel()
        self._load_later = self.asyncio_loop.call_later(0.08, self._load_deferred)

    def _load_deferred(self):
        self._load_later = None
        self.load_diff()
        self.draw_screen()

    def _left_cell(self, row, width, selected):
        if row is None:
            return ' ' * width
        indent = '  ' * row['depth']
        if row['type'] == 'dir':
            chev = '▾' if not row['collapsed'] else '▸'
            n = row['count']
            count = f'{n} file{"s" if n != 1 else ""}' if row.get('group_root') else str(n)
            if selected:
                return styled(pad(f'{indent}{chev} {row["name"]}  {count}', width),
                              reverse=True)
            segs = [(f'{indent}{chev} ', {'fg': 'gray'}),
                    (row['name'], {'bold': True}),
                    (f'  {count}', {'fg': 'gray'})]
            return compose(segs, width)
        # статус (M/A/D/…) не пишем — его несёт цвет имени;
        # префикс-пробелы держат выравнивание имён под
        # колонкой шеврона папок
        color = STATUS_STYLE.get(row['kind'], ('?', 'gray'))[1]
        stat = row.get('stat')
        radd = f'+{stat[0]}' if stat and stat[0] else ''
        rdel = f'−{stat[1]}' if stat and stat[1] else ''
        stat_str = ' '.join(x for x in (radd, rdel) if x)
        rlen = len(stat_str)
        prefix = f'{indent}  '
        budget = width - len(prefix) - rlen - (1 if rlen else 0)
        name = truncate(row['name'], max(1, budget))
        gap = max(1 if rlen else 0, width - len(prefix) - len(name) - rlen)
        if selected:
            return styled(pad(prefix + name + ' ' * gap + stat_str, width), reverse=True)
        out = styled(prefix, fg=color, bold=True) + styled(name, fg=color) + ' ' * gap
        parts = []
        if radd:
            parts.append(styled(radd, fg='green'))
        if rdel:
            parts.append(styled(rdel, fg='red'))
        return out + ' '.join(parts)

    # --- дифф выбранного файла ---

    def _set_diff(self, model):
        self.diff_rows, self.diff_plain, self.diff_vis = model.rows, model.plains, model.vis
        self.diff_hunks, self.diff_lineno = model.hunks, model.linenos
        self.diff_scope, self.diff_gap, self.diff_kind_bg = (
            model.scopes, model.gaps, model.kinds)
        if self.search_query:
            self._recompute_matches()

    def _set_placeholder(self, msg):
        # lineno 0: плейсхолдер — не строка кода, копирование/комменты
        # по нему не работают
        self.hscroll_max = 0
        self._set_diff(DiffModel([styled(msg, fg='gray')], [msg], [], [0], [''],
                                 [None], [None], [msg]))

    @staticmethod
    def _is_binary(it, before, after):
        return it.get('stat') == (None, None) or '\x00' in before or '\x00' in after

    def load_diff(self):
        if self._load_later is not None:    # прямая загрузка отменяет отложенную
            self._load_later.cancel()
            self._load_later = None
        self.diff_offset = 0
        self.hscroll = 0
        self.diff_cur = 0
        self.diff_sel = None
        self.diff_char_sel = None
        self.expanded = set()
        self.diff_src = None
        it = self.current_item()
        if not it:
            self.diff_before = self.diff_after = self.diff_ext = ''
            self._set_placeholder('  select a file to see its diff')
            return
        self.diff_before, self.diff_after = self._contents(it)
        self.diff_ext = os.path.splitext(it['rel'])[1].lower()
        if self._is_binary(it, self.diff_before, self.diff_after):
            self._set_placeholder('  (binary file)')
            return
        # дорогая часть модели (SequenceMatcher, word-diff) — один раз
        # на файл; hscroll/гэпы дальше перестраивают только рендер
        self.diff_src = DiffSource(self.diff_before, self.diff_after)
        self.build_diff_rows()

    def build_diff_rows(self):
        if self.current_item() is None or self.diff_src is None:
            return
        rw = self.diff_width()
        if not self.diff_before and not self.diff_after:
            self._set_placeholder('  (empty file)')
            return
        self.hscroll_max = max_hscroll(self.diff_src, rw)
        self.hscroll = min(self.hscroll, self.hscroll_max)
        model = unified_rows(self.diff_src, self.diff_ext, rw, 3, self.hscroll,
                             self.expanded, self.expand)
        if model.rows:
            self._set_diff(model)
        else:
            self._set_placeholder('  (no textual changes)')

    def _is_code_row(self, di):
        return is_code_row(di, self.diff_lineno, self.diff_gap)

    def _diff_cell(self, di, rw, cur_rel, cur_match):
        return render_diff_cell(
            di, rw, self.focus == 'diff', self.diff_cur, self.diff_sel,
            self._diff_annotated(di, cur_rel),
            rows=self.diff_rows, plains=self.diff_plain, linenos=self.diff_lineno,
            kind_bg=self.diff_kind_bg, gaps=self.diff_gap,
            cur_match=cur_match, query=self.search_query, char_sel=self.diff_char_sel,
            vis=self.diff_vis, hscroll=self.hscroll)

    # --- навигация по диффу ---

    def _commentable(self, di):
        return (0 <= di < len(self.diff_lineno) and self.diff_lineno[di] > 0
                and self._gap_at(di) is None)

    def _gap_at(self, di):
        return self.diff_gap[di] if 0 <= di < len(self.diff_gap) else None

    def _landable(self, di):
        if not (0 <= di < len(self.diff_rows)):
            return False
        if self._gap_at(di) is not None:
            return bool(self.diff_plain[di])
        return True

    def _first_landable(self, start):
        for i in range(max(0, start), len(self.diff_rows)):
            if self._landable(i):
                return i
        for i in range(len(self.diff_rows)):
            if self._landable(i):
                return i
        return start

    def _first_commentable(self, start):
        for i in range(max(0, start), len(self.diff_rows)):
            if self._commentable(i):
                return i
        for i in range(len(self.diff_rows)):
            if self._commentable(i):
                return i
        return start

    def move_cursor(self, delta):
        if not self.diff_rows:
            return
        n = len(self.diff_rows)
        step = 1 if delta >= 0 else -1
        i = self.diff_cur + delta
        while 0 <= i < n and not self._landable(i):
            i += step
        if not (0 <= i < n):
            return
        self.diff_cur = i
        self._ensure_cursor_visible()
        self.draw_screen()

    def nav(self, delta):
        if self.focus == 'diff':
            self.move_cursor(delta)
        else:
            self.tree_move(delta)

    def jump_edge(self, to_end):
        """g/G: в фокусе диффа — курсор к началу/концу диффа;
        в дереве — первый/последний файл (поведение зависит от
        текущего фокуса).
        """
        if self.focus == 'diff':
            if not self.diff_rows:
                return
            if to_end:
                n = len(self.diff_rows)
                self.diff_cur = next((j for j in range(n - 1, -1, -1)
                                      if self._landable(j)), self.diff_cur)
            else:
                self.diff_cur = self._first_landable(0)
            self._ensure_cursor_visible()
        else:
            if not self.rows:
                return
            self.set_tsel(len(self.rows) - 1 if to_end else 0)
            self.load_diff()
        self.draw_screen()

    def diff_scroll(self, delta):
        limit = max(0, len(self.diff_rows) - self.visible_rows())
        self.diff_offset = max(0, min(limit, self.diff_offset + delta))
        if self.focus == 'diff':
            vis = self.visible_rows()
            lo, hi = self.diff_offset, self.diff_offset + vis - 1
            if not (lo <= self.diff_cur <= hi):
                self.diff_cur = self._focus_landing(lo)
        self.draw_screen()

    def _ensure_cursor_visible(self):
        vis = self.visible_rows()
        if self.diff_cur < self.diff_offset:
            self.diff_offset = self.diff_cur
        elif self.diff_cur >= self.diff_offset + vis:
            self.diff_offset = self.diff_cur - vis + 1

    def hscroll_by(self, delta):
        new = min(self.hscroll_max, max(0, self.hscroll + delta))
        if new == self.hscroll:
            return
        self.hscroll = new
        self.build_diff_rows()
        self.draw_screen()

    def jump_hunk(self, direction):
        if not self.diff_hunks:
            return
        base = self.diff_cur if self.focus == 'diff' else self.diff_offset
        if direction > 0:
            nxt = next((h for h in self.diff_hunks if h > base), None)
        else:
            nxt = next((h for h in reversed(self.diff_hunks) if h < base), None)
        if nxt is None:
            return
        if self.focus == 'diff':
            self.diff_cur = nxt
            self._ensure_cursor_visible()
        else:
            limit = max(0, len(self.diff_rows) - self.visible_rows())
            self.diff_offset = min(nxt, limit)
        self.draw_screen()

    def expand_gap(self, di):
        gid = self._gap_at(di)
        if gid is None:
            return
        self.expanded.add(gid)
        off = self.diff_offset
        self.build_diff_rows()
        self.diff_offset = min(off, max(0, len(self.diff_rows) - self.visible_rows()))
        self.diff_cur = min(self.diff_cur, len(self.diff_rows) - 1)
        self.draw_screen()

    def toggle_expand(self):
        self.expand = not self.expand
        self.diff_offset = 0
        self.build_diff_rows()
        self.draw_screen()

    def set_focus(self, target):
        if target == self.focus:
            return
        if target == 'diff':
            if not self.diff_rows:
                return
            self.focus = 'diff'
            self.diff_cur = self._focus_landing(self.diff_offset)
        else:
            self.focus = 'tree'
        self.draw_screen()

    def toggle_focus(self):
        self.set_focus('tree' if self.focus == 'diff' else 'diff')

    # --- копирование в буфер ---

    def _copy_clipboard(self, text):
        self.print(osc52(text), end='')

    def _yank_code(self, lo, hi):
        if self.current_item() is None:
            return None
        nums = [self.diff_lineno[d] for d in range(lo, hi + 1)
                if 0 <= d < len(self.diff_lineno) and self.diff_lineno[d] > 0]
        if not nums:
            return None
        a, b = min(nums), max(nums)
        code = '\n'.join(self.diff_after.splitlines()[a - 1:b])
        return code, a, b

    def _sel_range(self):
        return self.diff_sel if self.diff_sel is not None else (self.diff_cur, self.diff_cur)

    def copy_selection(self):
        if self.diff_char_sel is not None:          # выделен кусок внутри строки
            row, cs, ce = self.diff_char_sel
            text = self.diff_plain[row][cs:ce].strip() if 0 <= row < len(self.diff_plain) else ''
            if text:
                self._copy_clipboard(text)
                self.flash = f'copied "{truncate(text, 30)}"'
            else:
                self.flash = 'nothing to copy'
            self.diff_char_sel = None
            self.focus = 'tree'
            self.draw_screen()
            return
        had_sel = self.diff_sel is not None
        res = self._yank_code(*self._sel_range())
        if res is None:
            self.flash = 'nothing to copy'
        else:
            code, a, b = res
            self._copy_clipboard(code)
            self.flash = f'copied L{a}-{b}' if b > a else f'copied L{a}'
        self.diff_sel = None
        if had_sel:
            self.focus = 'tree'
        self.draw_screen()

    def _current_rel(self):
        """Путь под курсором от корня репозитория; у папки — со
        слэшем на конце (Claude Code так отличает её от файла).
        """
        it = self.current_item()
        if it:
            return it['rel']
        if not self.rows or not (0 <= self.tsel < len(self.rows)):
            return None
        row = self.rows[self.tsel]
        if row['type'] != 'dir' or not row.get('path'):
            return None
        return row['path'] + '/'

    def copy_location(self):
        res = self._yank_code(*self._sel_range())
        rel = self._current_rel()
        if res is None or rel is None:
            self.flash = 'hover a diff line'
        else:
            _, a, b = res
            ref = f'@{rel}#L{a}' + (f'-{b}' if b > a else '')
            self._copy_clipboard(ref)
            self.flash = f'copied {ref}'
        self.draw_screen()

    def copy_path(self):
        rel = self._current_rel()
        if rel is None:
            self.flash = 'select a file or dir'
        else:
            self._copy_clipboard(f'@{rel}')
            self.flash = f'copied @{rel}'
        self.draw_screen()

    def smart_copy(self):
        if self.focus == 'diff':
            self.copy_selection()
        else:
            self.copy_path()

    def smart_copy_location(self):
        if self.focus == 'diff':
            self.copy_location()
        else:
            self.copy_path()

    # --- поиск по диффу ---

    def _recompute_matches(self):
        q = self.search_query.lower()
        self.search_matches = [i for i, p in enumerate(self.diff_plain)
                               if q and q in p.lower()]
        if self.search_idx >= len(self.search_matches):
            self.search_idx = 0

    def _scroll_to_match(self):
        if not self.search_matches:
            return
        row = self.search_matches[self.search_idx]
        limit = max(0, len(self.diff_rows) - self.visible_rows())
        self.diff_offset = max(0, min(limit, row - self.visible_rows() // 2))

    def search_next(self, direction):
        if not self.search_matches:
            return
        self.search_idx = (self.search_idx + direction) % len(self.search_matches)
        self._scroll_to_match()
        self.draw_screen()

    def clear_search(self):
        self.search_query = ''
        self.search_matches = []
        self.search_idx = 0
        self.draw_screen()

    # --- отрисовка панели (заголовок и футер печатает подкласс) ---

    @staticmethod
    def _thumb(offset, total, vis):
        """(начало, длина) ползунка в строках окна, либо None, когда
        содержимое помещается целиком.
        """
        if total <= vis:
            return None
        size = max(1, vis * vis // total)
        span = vis - size
        pos = round(offset * span / (total - vis)) if span else 0
        return pos, size

    def _scrollbar(self):
        return self._thumb(self.left_offset, len(self.rows), self.visible_rows())

    @staticmethod
    def _thumb_cell(bar, r):
        # трека нет: рядом уже линия-разделитель панелей и рамка
        # окна, лишняя вертикаль во всю высоту только рябит
        return styled('┃', fg=THUMB_FG) if bar and bar[0] <= r < bar[0] + bar[1] else ' '

    def _draw_pane_body(self):
        lw = self.left_width()
        self.clamp_left()
        vis = self.visible_rows()
        sep = styled(' │ ', fg='gray')
        rw = self.diff_width()
        cur = self.current_item()
        cur_rel = cur['rel'] if cur else None
        cur_match = self.search_matches[self.search_idx] if self.search_matches else -1
        sticky = ''
        if 0 < self.diff_offset < len(self.diff_scope):
            sticky = self.diff_scope[self.diff_offset]
        if not self.rows:
            self.print(styled('  ' + (self.status or self._empty_pane_msg()), fg='gray'))
            for _ in range(vis - 1):
                self.print()
            return
        tree_bar = self._scrollbar()
        diff_bar = self._thumb(self.diff_offset, len(self.diff_rows), vis)
        # строки диффа сами до правого края не достают (фон рисуется
        # только под кодом), поэтому ползунок ставим по абсолютной
        # колонке, а не пробелами
        col = f'\x1b[{self.screen_size.cols}G'
        for r in range(vis):
            li = self.left_offset + r
            left_row = self.rows[li] if li < len(self.rows) else None
            if sticky and r == 0:
                right = styled(truncate('▸ ' + sticky, rw), fg='cyan', bold=True)
            else:
                di = self.diff_offset + (r - 1 if sticky else r)
                right = self._diff_cell(di, rw, cur_rel, cur_match)
            left = self._left_cell(left_row, lw - 1, li == self.tsel)
            left += self._thumb_cell(tree_bar, r)
            tail = col + self._thumb_cell(diff_bar, r) if diff_bar else ''
            self.print(left + sep + right + tail)

    def _draw_input_line(self):
        if not self.input_mode:
            return
        cols = self.screen_size.cols
        for line in self.input_lines(cols)[-self.input_rows():]:
            self.print(styled(truncate(line, cols), fg='cyan', bold=True))

    # --- мышь ---

    def _diff_row_at(self, ev):
        r = ev.cell_y - 2
        if not (0 <= r < self.visible_rows()):
            return None
        if ev.cell_x < self.left_width():
            return None
        sticky = (0 < self.diff_offset < len(self.diff_scope)
                  and self.diff_scope[self.diff_offset])
        if sticky and r == 0:
            return None
        di = self.diff_offset + (r - 1 if sticky else r)
        if not (0 <= di < len(self.diff_rows)):
            return None
        return di

    def _diff_col_at(self, ev):
        """Позиция символа под курсором в diff_plain строки
        (с учётом hscroll).
        """
        return max(0, ev.cell_x - (self.left_width() + 3) + self.hscroll)

    def on_mouse_event(self, ev):
        if ev.buttons in (MouseButton.WHEEL_UP, MouseButton.WHEEL_DOWN):
            up = ev.buttons == MouseButton.WHEEL_UP
            if ev.cell_x < self.left_width():
                self.tree_scroll(-3 if up else 3)
            else:
                self.diff_scroll(-3 if up else 3)
            return
        if ev.buttons in (MouseButton.WHEEL_LEFT, MouseButton.WHEEL_RIGHT):
            if ev.cell_x >= self.left_width():
                self.hscroll_by(-3 if ev.buttons == MouseButton.WHEEL_LEFT else 3)
            return
        if self.drag_select(ev):
            return
        super().on_mouse_event(ev)

    # --- хуки DragSelect (выделение мышью в диффе) ---

    def _sel_row_at(self, ev):
        return self._diff_row_at(ev)

    def _sel_col_at(self, ev):
        return self._diff_col_at(ev)

    def _apply_char_sel(self, row, cs, ce):
        self.diff_char_sel = (row, cs, ce)
        self.diff_sel = None
        self.focus = 'diff'
        self.diff_cur = row

    def _apply_line_sel(self, lo, hi, row):
        self.diff_sel = (lo, hi)
        self.diff_char_sel = None
        self.focus = 'diff'
        self.diff_cur = row

    def _sel_done(self):
        self.flash = 'selected — ⌘c to copy'

    def on_click(self, ev):
        if self.input_mode:
            return
        self.diff_sel = None
        self.diff_char_sel = None
        r = ev.cell_y - 2
        if not (0 <= r < self.visible_rows()):
            return
        lw = self.left_width()
        if ev.cell_x < lw:
            li = self.left_offset + r
            if li >= len(self.rows):
                return
            self.focus = 'tree'
            self.tsel = li
            if self.rows[li]['type'] == 'dir':
                self.set_fold(self.rows[li]['key'] not in self.collapsed)
            else:
                self.load_diff()
                self.draw_screen()
            return
        di = self._diff_row_at(ev)
        if di is None:
            return
        if self._gap_at(di) is not None:
            self.expand_gap(di)
            return
        now = time.monotonic()
        double = (di == self._click_di and now - self._click_t < 0.4)
        self._click_di, self._click_t = di, now
        self.focus = 'diff'
        self.diff_cur = di
        self._diff_line_clicked(di, double)
