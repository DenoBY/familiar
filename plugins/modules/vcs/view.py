"""Базовый TUI-класс двухпанельного просмотра diff: дерево файлов
слева, дифф справа. Вся навигация, скролл, гэпы, hscroll,
поиск, выделение мышью и копирование в буфер — здесь, поверх
модели строк из modules.vcs.diff. Правая панель имеет два вида
(`view_mode`, клавиша v): unified-дифф и финальный файл целиком.
Источник данных (какие файлы и их before/after) задаёт подкласс
через хуки `_contents`; review показывает незакоммиченные правки,
log — изменения коммита.

Хуки для подклассов (контракты — в аннотациях методов):
- `_contents` — содержимое файла (before, after; обязателен);
- `_tree_visible` — доп. фильтр дерева (по умолчанию всё видно);
- `_focus_landing` — куда встаёт курсор при входе в дифф;
- `_diff_annotated` — маркер аннотации на строке (review);
- `_diff_line_clicked` — клик по строке диффа
  (review — двойной = коммент);
- `_empty_pane_msg` — сообщение, когда файлов нет.
"""

import os
import time
from typing import Callable

from kittens.tui.loop import MouseButton
from kittens.tui.operations import styled

from ..clipboard import osc52
from ..handler import OverlayHandler
from ..keylayout import to_latin
from ..text import pad, plural, truncate
from .diff import (
    GAP_STEP,
    MARK_FG,
    DiffModel,
    DiffSource,
    build_tree,
    change_map,
    final_rows,
    gutter_width,
    kinds_to_marks,
    line_marks,
    max_hscroll,
    render_diff_cell,
    unified_rows,
)
from .util import STATUS_STYLE, compose, is_noise


THUMB_FG = 244   # ползунки обеих панелей: заметнее серого текста, тише белого

# бит Shift в mouse-событии kitty. Shift+клик доходит до кита только с
# unmap в config/keys/mouse.conf: без него kitty съедает его под своё
# выделение даже в grabbed-режиме.
SHIFT_MOD = 0b1

# Сколько строк контекста оставляем над целью прыжка ([ ], поиск,
# аннотации): цель, прижатая к краю панели, читается хуже — не видно,
# откуда пришли.
JUMP_MARGIN = 3


def _row_counters(row: dict) -> 'list[tuple[str, dict]]':
    """Правая колонка строки файла: (текст, стиль) для +N/−N диффа либо
    для числа совпадений поиска.

    У совпадений свой счётчик, а не диффовый: зелёное «+N» читалось бы
    как добавленные строки — файл выглядел бы изменённым.
    """
    if row.get('matches'):
        return [(str(row['matches']), {'fg': 'gray'})]
    stat = row.get('stat') or (None, None)
    segs = []
    if stat[0]:
        segs.append((f'+{stat[0]}', {'fg': 'green'}))
    if stat[1]:
        segs.append((f'−{stat[1]}', {'fg': 'red'}))
    return segs


class DiffTreeView(OverlayHandler):

    def __init__(self, root: 'str | None') -> None:
        self.root = root
        self.items: list[dict] = []
        self.filtered: list[dict] = []
        self.rows: list[dict] = []
        self.n_files = 0
        self.collapsed: set[str] = set()
        # пути помеченных файлов (множественный выбор в дереве)
        # и якорь ⇧-диапазона — строка фокуса при входе в мультивыбор
        self.marked_paths: set[str] = set()
        self.mark_anchor: 'int | None' = None
        self.show_noise = False
        self.tsel = 0
        self.left_offset = 0
        self.focus = 'tree'
        self.diff_before = ''
        self.diff_after = ''
        self.diff_ext = ''
        self.diff_src: 'DiffSource | None' = None
        self.diff_rows: list[str] = []
        self.diff_plain: list[str] = []
        self.diff_vis: list[str] = []
        self.diff_fgs: 'list[list | None]' = []
        self.diff_hunks: list[int] = []
        self.diff_lineno: list[int] = []
        self.diff_scope: list[str] = []
        self.diff_gap: 'list[int | None]' = []
        self.diff_kind_bg: 'list[int | None]' = []
        # 'add'/'mod'/'del' по строкам — карта изменений справа
        self.diff_marks: 'list[str | None]' = []
        self.expanded: dict[int, int] = {}
        self.diff_offset = 0
        self.hscroll = 0
        self.hscroll_max = 0
        self.expand = False
        self.view_mode = 'diff'         # 'diff' — unified, 'final' — финальный файл
        # (lo, hi) — выделение целых строк (drag через строки)
        self.diff_sel: 'tuple[int, int] | None' = None
        # (row, cs, ce) — выделение куска в одной строке
        self.diff_char_sel: 'tuple[int, int, int] | None' = None
        self.diff_cur = 0
        self._click_di = -1
        self._click_t = 0.0
        self.search_query = ''
        self.search_matches: list[int] = []
        self.search_idx = 0
        self.status = ''
        self.flash = ''
        self._load_later = None

    # --- хуки подкласса ---

    def _contents(self, it: dict) -> tuple[str, str]:
        raise NotImplementedError

    def _tree_visible(self, it: dict) -> bool:
        return True

    def _focus_landing(self, start: int) -> int:
        return self._first_landable(start)

    def _diff_annotated(self, di: int, cur_rel: 'str | None') -> bool:
        return False

    def _diff_line_clicked(self, di: int, double: bool, col: int) -> None:
        self.draw_screen()

    def _empty_pane_msg(self) -> str:
        return 'no files'

    # --- геометрия ---

    def input_rows(self) -> int:
        """Высота области ввода. Растёт с текстом, но не съедает
        больше трети экрана — хвост длинного комментария виден,
        начало уезжает вверх.
        """
        if not self.input_mode:
            return 0
        cap = max(1, self.screen_size.rows // 3)
        return min(cap, len(self.input_lines(self.screen_size.cols)))

    def visible_rows(self) -> int:
        return max(1, self.screen_size.rows - 3 - self.input_rows())

    def sticky_line(self, offset: 'int | None' = None) -> str:
        """Липкий заголовок скоупа над строкой offset (пусто — нет).

        Он же занимает первую строку правой панели, поэтому от него
        зависит вся вертикальная геометрия диффа.
        """
        off = self.diff_offset if offset is None else offset
        return self.diff_scope[off] if 0 < off < len(self.diff_scope) else ''

    def diff_visible_rows(self, offset: 'int | None' = None) -> int:
        """Сколько строк диффа реально помещается в панель: липкий
        заголовок съедает строку, и без учёта этого нижняя строка
        считается видимой, а на экране её нет.
        """
        return max(1, self.visible_rows() - (1 if self.sticky_line(offset) else 0))

    def diff_offset_max(self) -> int:
        """Предельная прокрутка диффа: сдвиг открывает липкий
        заголовок, и до последней строки нужен ещё один шаг.
        """
        off = max(0, len(self.diff_rows) - self.visible_rows())
        return off + 1 if off and self.sticky_line(off) else off

    def left_width(self) -> int:
        return max(18, min(48, self.screen_size.cols * 2 // 5))

    def diff_width(self) -> int:
        # два последних столбца заняты: карта изменений и ползунок
        # (порознь, иначе риска сливается с ползунком). Оба
        # зарезервированы всегда, иначе появление полосы дёргало бы
        # перенос строк диффа
        return max(10, self.screen_size.cols - self.left_width() - 5)

    def left_limit(self) -> int:
        return max(0, len(self.rows) - self.visible_rows())

    def clamp_left(self) -> None:
        self.left_offset = max(0, min(self.left_offset, self.left_limit()))

    def ensure_left_visible(self) -> None:
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

    def set_tsel(self, i: int) -> None:
        self.tsel = max(0, min(i, max(0, len(self.rows) - 1)))
        self.ensure_left_visible()

    # --- дерево файлов ---

    def rebuild_tree(self) -> None:
        prev = self.rows[self.tsel] if (self.rows and 0 <= self.tsel < len(self.rows)) else None
        self.filtered = [it for it in self.items
                         if (self.show_noise or not is_noise(it['path']))
                         and self._tree_visible(it)]
        self.rows = build_tree(self.filtered, self.collapsed)
        # метки исчезнувших файлов (застейджили/откатили) не копим;
        # якорь — индекс строки, после перестройки он недействителен
        self.marked_paths &= {it['path'] for it in self.items}
        self.mark_anchor = None
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

    def _first_file(self) -> int:
        for i, r in enumerate(self.rows):
            if r['type'] == 'file':
                return i
        return 0

    def current_item(self) -> 'dict | None':
        if not self.rows or not (0 <= self.tsel < len(self.rows)):
            return None
        row = self.rows[self.tsel]
        return self.filtered[row['idx']] if row['type'] == 'file' else None

    def toggle_noise(self) -> None:
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

    def set_fold(self, collapse: bool) -> None:
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

    def toggle_fold(self) -> None:
        if self.rows and self.rows[self.tsel]['type'] == 'dir':
            self.set_fold(self.rows[self.tsel]['key'] not in self.collapsed)

    def tree_move(self, delta: int) -> None:
        if not self.rows:
            return
        # обычная навигация без Shift сбрасывает метки; диапазон
        # копит только mark_move (идёт мимо tree_move, через set_tsel)
        self._drop_marks()
        prev = self.tsel
        self.set_tsel(self.tsel + delta)
        if self.tsel != prev:
            self._schedule_load_diff()
        self.draw_screen()

    def tree_scroll(self, delta: int) -> None:
        """Двигает окно, но не выделение: колесо не должно
        перезагружать дифф на каждый щелчок.
        """
        if not self.rows:
            return
        self.left_offset = max(0, min(self.left_limit(), self.left_offset + delta))
        self.schedule_draw()

    def _schedule_load_diff(self) -> None:
        """Дифф при прокрутке дерева грузим отложенно: git show +
        разбор дороже кадра, синхронная загрузка на каждый шаг колеса
        копит очередь событий и курсор «догоняет» с лагом. Курсор
        двигается сразу, дифф — когда прокрутка утихла.
        """
        if self._load_later is not None:
            self._load_later.cancel()
        self._load_later = self.asyncio_loop.call_later(0.08, self._load_deferred)

    def _load_deferred(self) -> None:
        self._load_later = None
        self.load_diff()
        self.draw_screen()

    def _drop_marks(self) -> None:
        self.marked_paths.clear()
        self.mark_anchor = None

    def _items_under_cursor(self, keep: Callable[[dict], bool],
                            li: 'int | None' = None) -> list[dict]:
        """Элементы строки дерева (по умолчанию — под курсором): у
        файла — он сам, у папки (и у узла Unversioned Files) — все её
        файлы, прошедшие keep. Скрытые фильтром и noise-каталоги не
        попадают: трогаем ровно то, что видно.
        """
        if li is None:
            li = self.tsel
        if not self.rows or not (0 <= li < len(self.rows)):
            return []
        return self._row_items(self.rows[li], keep)

    def _row_items(self, row: dict, keep: Callable[[dict], bool]) -> list[dict]:
        if row['type'] == 'file':
            it = self.filtered[row['idx']]
            return [it] if keep(it) else []
        prefix = row['dir'] + '/' if row.get('dir') else ''
        return [it for it in self.filtered
                if it.get('group') == row.get('group') and it['path'].startswith(prefix)
                and keep(it)]

    # --- множественный выбор файлов (метки → ⌘c копирует все) ---

    def _paths_at(self, li: int) -> list[str]:
        return [it['path'] for it in self._items_under_cursor(lambda it: True, li)]

    def _range_paths(self, li: int) -> list[str]:
        """Вклад строки дерева в диапазонное выделение: файл — он сам;
        свёрнутая папка — все её файлы (видимы только этой строкой);
        развёрнутая — ничего: её файлы идут своими строками, и метки
        не должны убегать ниже курсора.
        """
        if not self.rows or not (0 <= li < len(self.rows)):
            return []
        row = self.rows[li]
        if row['type'] == 'file':
            return [self.filtered[row['idx']]['path']]
        return self._paths_at(li) if row.get('collapsed') else []

    def _dir_marked(self, row: dict) -> bool:
        """Свёрнутая папка подсвечивается, когда помечены все её
        файлы: поддерево видно только этой строкой.
        """
        if not row.get('collapsed'):
            return False
        paths = [it['path'] for it in self._row_items(row, lambda it: True)]
        return bool(paths) and all(p in self.marked_paths for p in paths)

    def _toggle_mark_at(self, li: int) -> None:
        """⌥+клик: пометить/снять файл, папку — все её файлы разом.
        Курсор, фокус и открытый дифф не трогаем — пометка не должна
        сбивать текущий файл. Последнюю метку клик не снимает:
        выделение не пустеет, снимает его навигация или Esc.
        """
        paths = self._paths_at(li)
        if not paths:
            self.flash = 'nothing to mark here'
            return
        if not self.marked_paths:
            # вход в мультивыбор: активное одиночное выделение — тоже
            # метка; ⌥+клик добавляет к нему, а не переносит выделение
            self.marked_paths.update(self._range_paths(self.tsel))
        if all(p in self.marked_paths for p in paths):
            if not self.marked_paths - set(paths):
                self.flash = 'last mark kept'
                return
            self.marked_paths.difference_update(paths)
        else:
            self.marked_paths.update(paths)
        self.flash = f'{plural(len(self.marked_paths), "file")} marked'

    def clear_marks(self) -> None:
        self._drop_marks()
        self.flash = 'marks cleared'
        self.draw_screen()

    def _paint_range(self, li: int) -> None:
        """Классика GUI-списков: ⇧ красит от якоря (фокус при входе в
        мультивыбор) до цели; повторное ⇧ перекрашивает от того же
        якоря, снимая ушедший из диапазона хвост.
        """
        if self.mark_anchor is None:
            self.mark_anchor = self.tsel
        a, prev = self.mark_anchor, self.tsel
        for i in range(min(a, prev), max(a, prev) + 1):
            self.marked_paths.difference_update(self._range_paths(i))
        self.set_tsel(li)
        for i in range(min(a, self.tsel), max(a, self.tsel) + 1):
            self.marked_paths.update(self._range_paths(i))
        if self.tsel != prev:
            self._schedule_load_diff()

    def mark_move(self, delta: int) -> None:
        """Shift+↑/↓: диапазон от якоря — шаг назад снимает строку.
        Строки без вклада в диапазон (развёрнутые папки) пропускаем:
        шаг на них был бы пустым — ни метки, ни подсветки.
        """
        if not self.rows:
            return
        li = self.tsel + delta
        while 0 <= li < len(self.rows) and not self._range_paths(li):
            li += delta
        if not 0 <= li < len(self.rows):
            return
        self._paint_range(li)
        self.draw_screen()

    def _mark_click(self, ev) -> bool:
        """⇧/⌥+клик по строке дерева: ⇧ красит диапазон от якоря до
        клика (повторный ⇧ перекрашивает от того же якоря),
        ⌥ — переключает метку файла/папки, не двигая курсор. В области
        диффа возвращает False.
        """
        if getattr(ev, 'cell_x', 0) >= self.left_width():
            return False
        r = ev.cell_y - 2
        if not (0 <= r < self.visible_rows()):
            return False
        li = self.left_offset + r
        if li >= len(self.rows):
            return False
        if getattr(ev, 'mods', 0) & SHIFT_MOD:
            self.focus = 'tree'
            self._paint_range(li)
            n = len(self.marked_paths)
            self.flash = (f'{plural(n, "file")} marked' if n
                          else 'nothing to mark here')
        else:
            self._toggle_mark_at(li)
        self.draw_screen()
        return True

    def _row_highlight(self, li: int) -> bool:
        """Выделение строки дерева: при активном мультивыборе — только
        метки, иначе — строка под курсором."""
        row = self.rows[li]
        cursor = li == self.tsel and not self.marked_paths
        if row['type'] == 'dir':
            return self._dir_marked(row) or cursor
        return self.filtered[row['idx']]['path'] in self.marked_paths or cursor

    def _left_cell(self, row: 'dict | None', width: int, li: int) -> str:
        if row is None:
            return ' ' * width
        indent = '  ' * row['depth']
        highlight = self._row_highlight(li)
        if row['type'] == 'dir':
            chev = '▾' if not row['collapsed'] else '▸'
            n = row['count']
            count = plural(n, 'file') if row.get('group_root') else str(n)
            if highlight:
                return styled(pad(f'{indent}{chev} {row["name"]}  {count}', width),
                              reverse=True)
            segs = [(f'{indent}{chev} ', {'fg': 'gray'}),
                    (row['name'], {'bold': True}),
                    (f'  {count}', {'fg': 'gray'})]
            return compose(segs, width)
        # статус (M/A/D/…) не пишем — его несёт цвет имени;
        # префикс-пробелы держат выравнивание имён под
        # колонкой шеврона папок
        color = STATUS_STYLE.get(row['kind'], 'gray')
        segs = _row_counters(row)
        stat_str = ' '.join(text for text, _ in segs)
        rlen = len(stat_str)
        prefix = f'{indent}  '
        budget = width - len(prefix) - rlen - (1 if rlen else 0)
        name = truncate(row['name'], max(1, budget))
        gap = max(1 if rlen else 0, width - len(prefix) - len(name) - rlen)
        if highlight:
            return styled(pad(prefix + name + ' ' * gap + stat_str, width), reverse=True)
        out = styled(prefix, fg=color, bold=True) + styled(name, fg=color) + ' ' * gap
        return out + ' '.join(styled(text, **style) for text, style in segs)

    # --- дифф выбранного файла ---

    def _set_diff(self, model: DiffModel, marks: 'list[str | None] | None' = None) -> None:
        """marks — метки строк для карты изменений на полосе прокрутки;
        у unified их несёт фон строки, у final приходят готовыми.
        """
        self.diff_rows, self.diff_plain, self.diff_vis = model.rows, model.plains, model.vis
        self.diff_fgs = model.fgs
        self.diff_hunks, self.diff_lineno = model.hunks, model.linenos
        self.diff_scope, self.diff_gap, self.diff_kind_bg = (
            model.scopes, model.gaps, model.kinds)
        self.diff_marks = marks if marks is not None else kinds_to_marks(model.kinds)
        # Безусловно: совпадения — производная от модели, а не от
        # непустого search_query. Подкласс вправе искать не подстрокой
        # (review в find-режиме — по номерам строк из git grep), и под
        # условием базы его совпадения оставались бы от прошлой модели.
        self._recompute_matches()

    def _set_placeholder(self, msg: str) -> None:
        # lineno 0: плейсхолдер — не строка кода, копирование/комменты
        # по нему не работают
        self.hscroll_max = 0
        self._set_diff(DiffModel([styled(msg, fg='gray')], [msg], [], [0], [''],
                                 [None], [None], [msg], [None]))

    def load_diff(self) -> None:
        if self._load_later is not None:    # прямая загрузка отменяет отложенную
            self._load_later.cancel()
            self._load_later = None
        self.diff_offset = 0
        self.hscroll = 0
        self.diff_cur = 0
        self.diff_sel = None
        self.diff_char_sel = None
        self.expanded = {}
        self.diff_src = None
        it = self.current_item()
        if not it:
            self.diff_before = self.diff_after = self.diff_ext = ''
            self._set_placeholder('  select a file to see its diff')
            return
        self.diff_ext = os.path.splitext(it['path'])[1].lower()
        # git уже сказал, что файл бинарный (numstat '-') — читать его
        # содержимое незачем: показывать всё равно нечего
        if it.get('stat') == (None, None):
            self.diff_before = self.diff_after = ''
            self._set_placeholder('  (binary file)')
            return
        self.diff_before, self.diff_after = self._contents(it)
        if '\x00' in self.diff_before or '\x00' in self.diff_after:
            self._set_placeholder('  (binary file)')
            return
        # дорогая часть модели (SequenceMatcher, word-diff) — один раз
        # на файл; hscroll/гэпы дальше перестраивают только рендер
        self.diff_src = DiffSource(self.diff_before, self.diff_after)
        self.build_diff_rows()

    def build_diff_rows(self) -> None:
        # diff_src задан и без tree-item — при show_file (внешний файл в
        # режиме final read-only); current_item() там None
        if self.diff_src is None:
            return
        rw = self.diff_width()
        if not self.diff_before and not self.diff_after:
            self._set_placeholder('  (empty file)')
            return
        final = self.view_mode == 'final'
        if final and not self.diff_after:
            self._set_placeholder('  (file deleted — no final content)')
            return
        self.hscroll_max = max_hscroll(self.diff_src, rw, final)
        self.hscroll = min(self.hscroll, self.hscroll_max)
        if final:
            model = final_rows(self.diff_src, self.diff_ext, rw, self.hscroll)
            marks = line_marks(self.diff_src)[0]
        else:
            model = unified_rows(self.diff_src, self.diff_ext, rw, 3, self.hscroll,
                                 self.expanded, self.expand)
            marks = None
        if model.rows:
            self._set_diff(model, marks)
        else:
            self._set_placeholder('  (no textual changes)')

    def _diff_cell(self, di: int, rw: int, cur_rel: 'str | None', cur_match: int) -> str:
        return render_diff_cell(
            di, rw, self.focus == 'diff', self.diff_cur, self.diff_sel,
            self._diff_annotated(di, cur_rel),
            rows=self.diff_rows, plains=self.diff_plain, linenos=self.diff_lineno,
            kind_bg=self.diff_kind_bg, gaps=self.diff_gap,
            cur_match=cur_match, query=self.search_query, char_sel=self.diff_char_sel,
            vis=self.diff_vis, hscroll=self.hscroll,
            ext=self.diff_ext, gutter_w=self._gutter_cols(), fgs=self.diff_fgs)

    # --- навигация по диффу ---

    def _commentable(self, di: int) -> bool:
        return (0 <= di < len(self.diff_lineno) and self.diff_lineno[di] > 0
                and self._gap_at(di) is None)

    def _gap_at(self, di: int) -> 'int | None':
        return self.diff_gap[di] if 0 <= di < len(self.diff_gap) else None

    def _landable(self, di: int) -> bool:
        if not (0 <= di < len(self.diff_rows)):
            return False
        if self._gap_at(di) is not None:
            return bool(self.diff_plain[di])
        return True

    def _first_landable(self, start: int) -> int:
        for i in range(max(0, start), len(self.diff_rows)):
            if self._landable(i):
                return i
        for i in range(len(self.diff_rows)):
            if self._landable(i):
                return i
        return start

    def _first_commentable(self, start: int) -> int:
        for i in range(max(0, start), len(self.diff_rows)):
            if self._commentable(i):
                return i
        for i in range(len(self.diff_rows)):
            if self._commentable(i):
                return i
        return start

    def move_cursor(self, delta: int) -> None:
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

    def nav(self, delta: int) -> None:
        if self.focus == 'diff':
            self.move_cursor(delta)
        else:
            self.tree_move(delta)

    def jump_edge(self, to_end: bool) -> None:
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
            self._drop_marks()   # прыжок g/G — тоже навигация без Shift
            self.set_tsel(len(self.rows) - 1 if to_end else 0)
            self.load_diff()
        self.draw_screen()

    def diff_scroll(self, delta: int) -> None:
        self.diff_offset = max(0, min(self.diff_offset_max(), self.diff_offset + delta))
        if self.focus == 'diff':
            lo = self.diff_offset
            hi = lo + self.diff_visible_rows() - 1
            if not (lo <= self.diff_cur <= hi):
                self.diff_cur = self._focus_landing(lo)
        self.schedule_draw()

    def _ensure_cursor_visible(self) -> None:
        # два прохода: сдвиг окна сам открывает или прячет липкий
        # заголовок, а с ним меняется и высота панели
        for _ in range(2):
            vis = self.diff_visible_rows()
            if self.diff_cur < self.diff_offset:
                self.diff_offset = self.diff_cur
            elif self.diff_cur >= self.diff_offset + vis:
                self.diff_offset = self.diff_cur - vis + 1
            else:
                return

    def reveal_cursor(self, margin: int = JUMP_MARGIN) -> None:
        """Показать курсор с контекстом: цель у самого края панели
        (или за ней) — прокрутить так, чтобы над ней осталось margin
        строк. Курсор в середине кадра окно не дёргает.
        """
        vis = self.diff_visible_rows()
        m = min(margin, max(0, (vis - 1) // 2))
        if self.diff_offset + m <= self.diff_cur <= self.diff_offset + vis - 1 - m:
            return
        self.diff_offset = max(0, min(self.diff_offset_max(), self.diff_cur - m))

    def hscroll_by(self, delta: int) -> None:
        new = min(self.hscroll_max, max(0, self.hscroll + delta))
        if new == self.hscroll:
            return
        self.hscroll = new
        self.build_diff_rows()
        self.schedule_draw()

    def jump_hunk(self, direction: int) -> None:
        """Прыжок к соседнему блоку изменений (в поиске — к соседнему
        совпадению).

        Фокус уводим в дифф даже из дерева: курсор рисуется только на
        сфокусированной панели, иначе прыжок виден как безымянный
        сдвиг текста — непонятно, куда попали.
        """
        if not self.diff_hunks:
            return
        base = self.diff_cur if self.focus == 'diff' else self.diff_offset
        if direction > 0:
            nxt = next((h for h in self.diff_hunks if h > base), None)
        else:
            nxt = next((h for h in reversed(self.diff_hunks) if h < base), None)
        if nxt is None:
            return
        self.focus = 'diff'
        self.diff_cur = nxt
        self.reveal_cursor()
        self.draw_screen()

    def expand_gap(self, di: int) -> None:
        """Открыть очередную порцию скрытых строк под курсором.

        Раскрытое встаёт НАД разделителем, поэтому сам разделитель
        уезжает вниз ровно на столько строк. Курсор и окно едут за
        ним: иначе следующее нажатие пришлось бы ловить, доскроллив
        до уехавшего разделителя.
        """
        gid = self._gap_at(di)
        if gid is None:
            return
        inner = di - self._gap_start(gid)   # позиция внутри блока разделителя
        self.expanded[gid] = self.expanded.get(gid, 0) + GAP_STEP
        self.build_diff_rows()
        start = self._gap_start(gid)
        if start is None:
            self.diff_cur = di              # гэп исчерпан: на его месте уже код
        else:
            self.diff_offset += start + inner - di
            self.diff_cur = start + inner
        self.diff_offset = max(0, min(self.diff_offset, self.diff_offset_max()))
        self.diff_cur = max(0, min(self.diff_cur, len(self.diff_rows) - 1))
        self.draw_screen()

    def _gap_start(self, gid: int) -> 'int | None':
        return next((i for i, g in enumerate(self.diff_gap) if g == gid), None)

    def toggle_expand(self) -> None:
        if self.view_mode == 'final':
            self.flash = 'final view always shows the whole file'
            self.draw_screen()
            return
        self.expand = not self.expand
        self.diff_offset = 0
        self.build_diff_rows()
        self.draw_screen()

    def toggle_view_mode(self) -> None:
        """unified-дифф ↔ финальный файл. Курсор остаётся на той же
        строке кода: номер строки нового файла общий для обоих видов.
        """
        if self.diff_src is None:
            return
        line = (self.diff_lineno[self.diff_cur]
                if 0 <= self.diff_cur < len(self.diff_lineno) else 0)
        self.view_mode = 'final' if self.view_mode == 'diff' else 'diff'
        self.hscroll = 0
        self.diff_sel = self.diff_char_sel = None
        self.build_diff_rows()
        self._center_on_line(line)
        self.flash = ('final code — ▎ changed, ▔ deleted here'
                      if self.view_mode == 'final' else 'unified diff')
        self.draw_screen()

    def _mode_hints(self) -> str:
        """Подсказка футера про вид панели: `a` осмыслен только в
        unified — final и так показывает файл целиком.
        """
        if self.view_mode == 'final':
            return 'v diff-view'
        exp = 'a full-file' if not self.expand else 'a hunks'
        return f'{exp} · v final-view'

    def _footer_tail(self) -> str:
        """Хвост футера, общий обоим vcs-китам: сдвиг по горизонтали
        и счётчик совпадений — состояние базы, не подкласса.
        """
        tail = ''
        if self.hscroll:
            tail += f'   ·   ↔ {self.hscroll}'
        if self.search_matches:
            tail += f'   ·   n/N {self.search_idx + 1}/{len(self.search_matches)}'
        return tail

    def _center_on_line(self, line: int) -> None:
        """Курсор на строку нового файла с номером line (нет такой —
        на первое изменение), окно — вокруг курсора.
        """
        di = (next((i for i, ln in enumerate(self.diff_lineno) if ln == line), None)
              if line else None)
        if di is None:
            di = self.diff_hunks[0] if self.diff_hunks else self._first_landable(0)
        self.diff_cur = min(di, max(0, len(self.diff_rows) - 1))
        self.diff_offset = max(0, min(self.diff_offset_max(),
                                      self.diff_cur - self.visible_rows() // 2))

    def set_focus(self, target: str) -> None:
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

    def toggle_focus(self) -> None:
        self.set_focus('tree' if self.focus == 'diff' else 'diff')

    # --- копирование в буфер ---

    def _copy_clipboard(self, text: str) -> None:
        self.print(osc52(text), end='')

    def _yank_code(self, lo: int, hi: int) -> 'tuple[str, int, int] | None':
        if self.current_item() is None:
            return None
        nums = [self.diff_lineno[d] for d in range(lo, hi + 1)
                if 0 <= d < len(self.diff_lineno) and self.diff_lineno[d] > 0]
        if not nums:
            return None
        a, b = min(nums), max(nums)
        code = '\n'.join(self.diff_after.splitlines()[a - 1:b])
        return code, a, b

    def _sel_range(self) -> tuple[int, int]:
        return self.diff_sel if self.diff_sel is not None else (self.diff_cur, self.diff_cur)

    def copy_selection(self) -> None:
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

    def _current_rel(self) -> 'str | None':
        """Путь под курсором от корня репозитория; у папки — со
        слэшем на конце (Claude Code так отличает её от файла).
        """
        it = self.current_item()
        if it:
            return it['path']
        if not self.rows or not (0 <= self.tsel < len(self.rows)):
            return None
        row = self.rows[self.tsel]
        if row['type'] != 'dir' or not row.get('dir'):
            return None
        return row['dir'] + '/'

    def copy_location(self) -> None:
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

    def _marked_paths(self) -> list[str]:
        """Помеченные файлы в порядке дерева; отфильтрованные скрытием
        сюда не попадают — копируем ровно то, что видно."""
        return [it['path'] for it in self.filtered if it['path'] in self.marked_paths]

    def copy_path(self) -> None:
        marked = self._marked_paths()
        if marked:
            self._copy_clipboard('\n'.join(f'@{r}' for r in marked))
            self.flash = f'copied {plural(len(marked), "path")}'
            self.draw_screen()
            return
        rel = self._current_rel()
        if rel is None:
            self.flash = 'select a file or dir'
        else:
            self._copy_clipboard(f'@{rel}')
            self.flash = f'copied @{rel}'
        self.draw_screen()

    def smart_copy(self) -> None:
        if self.focus == 'diff':
            self.copy_selection()
        else:
            self.copy_path()

    def smart_copy_location(self) -> None:
        if self.focus == 'diff':
            self.copy_location()
        else:
            self.copy_path()

    # --- поиск по диффу ---

    def _recompute_matches(self) -> None:
        q = self.search_query.lower()
        self.search_matches = [i for i, p in enumerate(self.diff_plain)
                               if q and q in p.lower()]
        if self.search_idx >= len(self.search_matches):
            self.search_idx = 0

    def _scroll_to_match(self) -> None:
        if not self.search_matches:
            return
        row = self.search_matches[self.search_idx]
        self.diff_offset = max(0, min(self.diff_offset_max(),
                                      row - self.visible_rows() // 2))

    def search_next(self, direction: int) -> None:
        if not self.search_matches:
            return
        self.search_idx = (self.search_idx + direction) % len(self.search_matches)
        self._scroll_to_match()
        self.draw_screen()

    def clear_search(self) -> None:
        self._reset_search()
        self.draw_screen()

    def _reset_search(self) -> None:
        self.search_query = ''
        self.search_matches = []
        self.search_idx = 0

    def start_search(self) -> None:
        self.start_input('search', self.search_query)

    def _input_live(self) -> None:
        # Режим 'search' заводит сама база (start_search), поэтому и
        # применяет его сама: иначе унаследованный ⌘f внешне работал бы,
        # а вживую не искал, пока подкласс не продублирует эту ветку.
        if self.input_mode == 'search':
            self.apply_search_input()
            return
        super()._input_live()

    def _input_cancelled(self, mode: str) -> None:
        # без draw_screen: его сделает cancel_input, который нас позвал
        if mode == 'search':
            self._reset_search()
            return
        super()._input_cancelled(mode)

    def apply_search_input(self) -> None:
        """Живое применение строки поиска: пересчитать совпадения и
        встать на ближайшее к текущей позиции скролла.
        """
        self.search_query = self.input_buffer
        self._recompute_matches()
        if self.search_matches:
            self.search_idx = next((n for n, r in enumerate(self.search_matches)
                                    if r >= self.diff_offset), 0)
            self._scroll_to_match()
        self.draw_screen()

    # --- общий разбор ввода diff-панели (специфика — в подклассах) ---

    def diff_common_key(self, k: str) -> bool:
        """Общие клавиши двухпанельного экрана. True — обработано;
        False — подкласс разбирает свою специфику (комментарии у
        review, возврат к списку коммитов у log).
        """
        if k == 'TAB':
            self.toggle_focus()
        elif k == 'UP':
            self.nav(-1)
        elif k == 'DOWN':
            self.nav(1)
        elif k == 'PAGE_UP':
            self.diff_scroll(-self.visible_rows())
        elif k == 'PAGE_DOWN':
            self.diff_scroll(self.visible_rows())
        elif k == 'ENTER':
            if self.focus == 'diff' and self._gap_at(self.diff_cur) is not None:
                self.expand_gap(self.diff_cur)
            elif self.focus == 'tree':
                self.toggle_fold()
            else:
                return False
        elif k == 'RIGHT':
            self.set_focus('diff')
        elif k == 'LEFT':
            if self.focus != 'diff':
                return False
            self.set_focus('tree')
        elif k == 'ESCAPE':
            # каскад: выделение → поиск → фокус; последний шаг
            # (выход/назад) — за подклассом
            if self.diff_sel is not None or self.diff_char_sel is not None:
                self.diff_sel = self.diff_char_sel = None
                self.draw_screen()
            elif self.search_query:
                self.clear_search()
            elif self.focus == 'diff':
                self.set_focus('tree')
            else:
                return False
        else:
            return False
        return True

    def diff_common_text(self, ch: str) -> bool:
        """Общие печатаемые команды двухпанельного экрана (один
        символ). True — обработано.
        """
        c = to_latin(ch)
        if ch == '\t':
            self.toggle_focus()
        elif c == 'n':
            self.search_next(1)
        elif c == 'N':
            self.search_next(-1)
        elif c == '[':
            self.jump_hunk(-1)
        elif c == ']':
            self.jump_hunk(1)
        elif c in ('h', 'H'):
            self.hscroll_by(-8)
        elif c in ('l', 'L'):
            self.hscroll_by(8)
        elif c == 'g':
            self.jump_edge(False)
        elif c == 'G':
            self.jump_edge(True)
        elif c in ('a', 'A'):
            self.toggle_expand()
        elif c in ('v', 'V'):
            self.toggle_view_mode()
        elif c in ('u', 'U'):
            self.toggle_noise()
        elif ch == ' ':
            self.toggle_fold()
        else:
            return False
        return True

    # --- отрисовка панели (заголовок и футер печатает подкласс) ---

    @staticmethod
    def _thumb(offset: int, total: int, vis: int) -> 'tuple[int, int] | None':
        """(начало, длина) ползунка в строках окна, либо None, когда
        содержимое помещается целиком.
        """
        if total <= vis:
            return None
        size = max(1, vis * vis // total)
        span = vis - size
        pos = round(offset * span / (total - vis)) if span else 0
        return pos, size

    def _scrollbar(self) -> 'tuple[int, int] | None':
        return self._thumb(self.left_offset, len(self.rows), self.visible_rows())

    @staticmethod
    def _thumb_cell(bar: 'tuple[int, int] | None', r: int) -> str:
        # трека нет: рядом уже линия-разделитель панелей и рамка
        # окна, лишняя вертикаль во всю высоту только рябит
        return styled('┃', fg=THUMB_FG) if bar and bar[0] <= r < bar[0] + bar[1] else ' '

    @staticmethod
    def _change_cell(cmap: 'list[str | None]', r: int) -> str:
        """Ячейка карты изменений — своя колонка слева от ползунка,
        иначе риска и ползунок сливаются в одну вертикаль. Риска
        тонкая (│) против жирного ползунка (┃): толщина сама говорит,
        что есть что.
        """
        mark = cmap[r] if r < len(cmap) else None
        return styled('│', fg=MARK_FG[mark]) if mark is not None else ' '

    def _draw_pane_body(self) -> None:
        lw = self.left_width()
        self.clamp_left()
        vis = self.visible_rows()
        sep = styled(' │ ', fg='gray')
        rw = self.diff_width()
        cur = self.current_item()
        cur_rel = cur['path'] if cur else None
        cur_match = self.search_matches[self.search_idx] if self.search_matches else -1
        sticky = self.sticky_line()
        if not self.rows:
            self.print(styled('  ' + (self.status or self._empty_pane_msg()), fg='gray'))
            for _ in range(vis - 1):
                self.print()
            return
        tree_bar = self._scrollbar()
        diff_bar = self._thumb(self.diff_offset, len(self.diff_rows), vis)
        cmap = change_map(self.diff_marks, vis)
        # строки диффа сами до правого края не достают (фон рисуется
        # только под кодом), поэтому карту и ползунок ставим по
        # абсолютным колонкам, а не пробелами
        cols = self.screen_size.cols
        map_col, thumb_col = f'\x1b[{cols - 1}G', f'\x1b[{cols}G'
        for r in range(vis):
            li = self.left_offset + r
            left_row = self.rows[li] if li < len(self.rows) else None
            if sticky and r == 0:
                right = styled(truncate('▸ ' + sticky, rw), fg='cyan', bold=True)
            else:
                di = self.diff_offset + (r - 1 if sticky else r)
                right = self._diff_cell(di, rw, cur_rel, cur_match)
            left = self._left_cell(left_row, lw - 1, li)
            left += self._thumb_cell(tree_bar, r)
            tail = ''
            mark_cell = self._change_cell(cmap, r)
            if mark_cell != ' ':
                tail += map_col + mark_cell
            thumb = self._thumb_cell(diff_bar, r)
            if thumb != ' ':
                tail += thumb_col + thumb
            self.print(left + sep + right + tail)

    def _draw_input_line(self) -> None:
        if not self.input_mode:
            return
        cols = self.screen_size.cols
        lines, row, col = self.input_layout(cols)
        shown = lines[-self.input_rows():]
        for line in shown:
            self.print(styled(truncate(line, cols), fg='cyan', bold=True))
        # каретка — в экранных координатах области ввода (длинный
        # комментарий обрезан сверху, строки каретки это касается тоже)
        r = min(max(row - (len(lines) - len(shown)), 0), len(shown) - 1)
        self.set_caret(2 + self.visible_rows() + r, min(col, cols - 1))

    # --- мышь ---

    def _diff_row_at(self, ev) -> 'int | None':
        r = ev.cell_y - 2
        if not (0 <= r < self.visible_rows()):
            return None
        if ev.cell_x < self.left_width():
            return None
        sticky = self.sticky_line()
        if sticky and r == 0:
            return None
        di = self.diff_offset + (r - 1 if sticky else r)
        if not (0 <= di < len(self.diff_rows)):
            return None
        return di

    def _diff_col_at(self, ev) -> int:
        """Позиция символа под курсором в diff_plain строки. hscroll
        сдвигает только код: гуттер и знак прибиты к левому краю.
        """
        x = ev.cell_x - (self.left_width() + 3)
        fixed = self._gutter_cols() + 2
        return max(0, x if x < fixed else x + self.hscroll)

    def _gutter_cols(self) -> int:
        """Ширина гуттера с номерами строк в diff_plain (0 — нет диффа).
        Клик левее — по номеру строки, правее — по коду. В final-виде
        колонка одна (как в final_rows), в unified — как у diff_src.
        """
        if self.diff_src is None:
            return 0
        one_col = self.view_mode == 'final' or self.diff_src.one_col
        return gutter_width(one_col, self.diff_width())

    def _dir_row_at(self, ev) -> bool:
        r = ev.cell_y - 2
        if not (0 <= r < self.visible_rows()) or ev.cell_x >= self.left_width():
            return False
        li = self.left_offset + r
        return li < len(self.rows) and self.rows[li]['type'] == 'dir'

    def _pointer_for(self, ev) -> 'str | None':
        # рука — на кликабельном «раскрытии» (папка дерева, gap
        # диффа), текст — на строке кода (drag-select), иначе стрелка
        di = self._diff_row_at(ev)
        if di is not None:
            return 'pointer' if self._gap_at(di) is not None else 'text'
        if self._dir_row_at(ev):
            return 'pointer'
        return None

    def _on_mouse(self, ev) -> None:
        self.update_pointer(ev)
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
        super()._on_mouse(ev)

    # --- хуки DragSelect (выделение мышью в диффе) ---

    def _sel_row_at(self, ev) -> 'int | None':
        return self._diff_row_at(ev)

    def _sel_col_at(self, ev) -> int:
        return self._diff_col_at(ev)

    def _apply_char_sel(self, row: int, cs: int, ce: int) -> None:
        self.diff_char_sel = (row, cs, ce)
        self.diff_sel = None
        self.focus = 'diff'
        self.diff_cur = row

    def _apply_line_sel(self, lo: int, hi: int, row: int) -> None:
        self.diff_sel = (lo, hi)
        self.diff_char_sel = None
        self.focus = 'diff'
        self.diff_cur = row

    def _sel_done(self) -> None:
        self.flash = 'selected — ⌘c to copy'

    def on_click(self, ev) -> None:
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
            # промах по соседней строке (и возврат в дерево из диффа)
            # не должен перестраивать дерево под курсором
            already_selected = self.focus == 'tree' and self.tsel == li
            self._drop_marks()   # клик — тоже навигация без Shift
            self.focus = 'tree'
            self.tsel = li
            if self.rows[li]['type'] == 'dir':
                if already_selected:
                    self.set_fold(self.rows[li]['key'] not in self.collapsed)
                else:
                    self.draw_screen()
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
        self._diff_line_clicked(di, double, self._diff_col_at(ev))
