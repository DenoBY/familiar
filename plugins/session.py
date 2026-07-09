#!/usr/bin/env python3
"""
session — kitten для kitty.

Оверлей для просмотра и управления сессиями Claude Code.

Точка входа кита; логика разнесена по пакету modules.session:
util (строки, раскладка, возраст) и data (чтение проектов/сессий
из ~/.claude).

Подключение в ~/.config/kitty/kitty.conf:
    map cmd+shift+s kitten /Users/deno/Projects/kitty/plugins/session.py

Навигация:
    ↑/↓        — перемещение по списку
    Enter      — войти в проект
    Esc        — назад / выход
    q          — выход
"""

import os
import shlex
import sys
import time

from kittens.tui.handler import Handler, result_handler
from kittens.tui.loop import Loop, MouseButton
from kittens.tui.operations import MouseTracking, styled
from kitty.key_encoding import EventType


# Пакет modules лежит рядом с этим файлом. При запуске через
# `kitten path.py` (CLI/автодополнение) kitty не добавляет его папку
# в sys.path; при штатном launch папка и так в sys.path на время
# загрузки, но __file__ там отсутствует.
if '__file__' in globals():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.clipboard import osc52
from modules.dragselect import DragSelect
from modules.draw import AtomicDraw
from modules.highlight import SEL_RANGE_BG
from modules.inputline import InputLine
from modules.keylayout import chord
from modules.overlay import mark_overlay
from modules.session.data import (
    STATUS_COLOR,
    STATUS_LABEL,
    append_custom_title,
    build_projects,
    load_conversation,
    load_sessions,
    running_sessions,
    scan_projects,
)
from modules.session.transcript import transcript_lines
from modules.session.util import human_age, pad, short_path, to_latin, truncate


class SessionsHandler(AtomicDraw, InputLine, DragSelect, Handler):

    mouse_tracking = MouseTracking.buttons_and_drag

    def __init__(self, args: list, now: float) -> None:
        self.cli_args = args
        self.now = now
        self.result = None
        self._all_projects = []     # сырой скан (с probes всех сессий)
        self.projects = []          # отфильтрованный список для показа
        self.sessions = []
        self.project = None         # выбранный проект
        self.screen = 'projects'    # 'projects' | 'sessions' | 'preview'
        self.sel = 0
        self.offset = 0
        self.status = ''            # строка-подсказка/сообщение снизу
        self.show_all = False       # False = только cli, True = включая sdk
        self.running = {}
        self.running_ids = set()
        # предпросмотр
        self.preview_entries = []
        self.preview_lines = []
        self.preview_offset = 0
        self.preview_expanded = set()   # индексы раскрытых записей диалога
        self.preview_session = None
        self.preview_sel = None         # (lo, hi) — выделение целых строк
        self.preview_char_sel = None    # (row, cs, ce) — выделение куска строки
        self._preview_cache = {}        # кэш строк transcript_lines
        self._preview_cache_width = 0       # ширина, для которой он собран
        self._preview_cache_entries = None  # и список записей (сравнение по is)
        self._worktree_cwd = None   # каталог, для которого создаём worktree
        self.filter_query = ''      # фильтр списка проектов/сессий
        self.search_query = ''
        self.search_matches = []
        self.search_idx = -1

    # --- жизненный цикл ---

    def initialize(self):
        self.cmd.set_cursor_visible(False)
        self.running = running_sessions()          # {sessionId: info}
        self.running_ids = set(self.running)
        self._all_projects = scan_projects()
        self.rebuild_projects()

        # Если вызвали из папки проекта — сразу открываем его сессии.
        # Иначе стартуем со списка проектов (режим выбора проекта).
        current = next((p for p in self.projects if p['is_current']), None)
        if current:
            self.open_project(current)

        self.draw_screen()

    def rebuild_projects(self):
        self.projects = build_projects(self._all_projects, self.running_ids,
                                       self.show_all)

    def finalize(self):
        self.cmd.set_cursor_visible(True)

    # --- переходы между экранами ---

    def open_project(self, project):
        if not project:
            return
        self.project = project
        self.sessions = load_sessions(self.project)
        # Активные сессии — те, что реально запущены
        # (есть в реестре живых).
        for s in self.sessions:
            info = self.running.get(s['id'])
            s['active'] = info is not None
            s['status'] = info.get('status') if info else None
            s['waitingFor'] = info.get('waitingFor') if info else None
            s['bg'] = bool(info) and info.get('kind') == 'bg'
        # Запущенные — наверх, дальше по свежести.
        self.sessions.sort(key=lambda s: (not s['active'], -s['mtime']))
        self.screen = 'sessions'
        self.sel = 0
        self.offset = 0
        self.filter_query = ''
        self.status = ''

    def back_to_projects(self):
        self.screen = 'projects'
        self.project = None
        self.sessions = []
        self.sel = 0
        self.offset = 0
        self.filter_query = ''
        self.status = ''

    # --- геометрия ---

    def visible_rows(self):
        # на экране проектов шапки нет — только футер (1 строка),
        # на остальных: шапка + разделитель + футер (3 строки).
        reserved = 1 if self.screen == 'projects' else 3
        if self.input_mode:
            reserved += 1   # отдельная строка поля ввода над футером
        return max(1, self.screen_size.rows - reserved)

    def items(self):
        """Видимый список текущего экрана с учётом фильтра."""
        q = self.filter_query.lower()
        if self.screen == 'projects':
            src = self.projects
            if q:
                return [p for p in src
                        if q in p['name'].lower() or q in p['path'].lower()]
            return src
        if self.screen == 'sessions':
            src = self.sessions
            if q:
                return [s for s in src if q in s['title'].lower()]
            return src
        return []

    def current_len(self):
        return len(self.items())

    def ensure_visible(self):
        vis = self.visible_rows()
        if self.sel < self.offset:
            self.offset = self.sel
        elif self.sel >= self.offset + vis:
            self.offset = self.sel - vis + 1

    # --- отрисовка ---

    def _draw_frame(self):
        self.cmd.clear_screen()
        cols = self.screen_size.cols

        if self.screen == 'preview':
            self._draw_preview(cols)
            return

        items = self.items()
        if self.sel >= len(items):
            self.sel = max(0, len(items) - 1)
        self.ensure_visible()

        # header — только на экране сессий; на проектах шапки нет
        if self.screen == 'sessions':
            n_active = sum(1 for s in self.sessions if s.get('active'))
            header = f' {short_path(self.project["path"])} · sessions ({len(items)}'
            header += f'/{len(self.sessions)})' if self.filter_query else ')'
            if n_active:
                header += f'  ·  active: {n_active}'
            self.print(styled(truncate(header, cols), fg='green', bold=True))
            self.print(styled('─' * cols, fg='gray'))

        # body
        vis = self.visible_rows()
        if not items:
            msg = '  no matches' if self.filter_query else '  empty'
            self.print(styled(msg, fg='gray'))
            for _ in range(vis - 1):
                self.print()
        else:
            end = min(len(items), self.offset + vis)
            for idx in range(self.offset, end):
                selected = (idx == self.sel)
                if self.screen == 'projects':
                    self.print(self._project_row(items[idx], cols, selected))
                else:
                    self.print(self._session_row(items[idx], cols, selected))
            for _ in range(vis - (end - self.offset)):
                self.print()

        # строка ввода (над футером) — только в режиме ввода
        if self.input_mode:
            self.print(styled(truncate(self._input_line(), cols), fg='cyan', bold=True))

        # footer — без финального перевода строки, иначе экран
        # прокрутится вверх на одну строку и шапка уедет за верх окна.
        self.print(styled(truncate(self._footer(), cols), fg='gray'), end='')

    def _draw_preview(self, cols):
        s = self.preview_session
        title = s['title'] if s else ''
        name = self.project['name'] if self.project else ''
        header = f' {name} · {title}'
        self.print(styled(truncate(header, cols), fg='green', bold=True))
        self.print(styled('─' * cols, fg='gray'))

        vis = self.visible_rows()
        total = len(self.preview_lines)
        self.preview_offset = max(0, min(self.preview_offset, max(0, total - vis)))
        end = min(total, self.preview_offset + vis)
        cur_match = -1
        if self.search_matches and 0 <= self.search_idx < len(self.search_matches):
            cur_match = self.search_matches[self.search_idx]
        for i in range(self.preview_offset, end):
            self.print(self._preview_line(self.preview_lines[i], cols,
                                          i == cur_match, i))
        for _ in range(vis - (end - self.preview_offset)):
            self.print()

        if self.input_mode:
            self.print(styled(truncate(self._input_line(), cols), fg='cyan', bold=True))

        self.print(styled(truncate(self._footer(), cols), fg='gray'), end='')

    def _selection_render(self, ln, cols, row):
        """Фон выделения поверх строки: диапазон строк целиком,
        символьное — срезом.
        """
        line = truncate(ln.text, cols)
        if self.preview_sel and self.preview_sel[0] <= row <= self.preview_sel[1]:
            return styled(pad(line, cols), bg=SEL_RANGE_BG)
        if self.preview_char_sel and self.preview_char_sel[0] == row:
            _, cs, ce = self.preview_char_sel
            cs, ce = max(0, cs), min(len(line), ce)
            if cs >= ce:
                return None
            return (styled(line[:cs], fg=ln.color)
                    + styled(line[cs:ce], bg=SEL_RANGE_BG)
                    + styled(line[ce:], fg=ln.color))
        return None

    def _preview_line(self, ln, cols, is_current, row):
        """Строка предпросмотра: выделение, подсветка совпадений
        поиска, готовый ANSI.
        """
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

    def _compose(self, left, right, cols, selected):
        """Собрать строку: left слева, right справа, ширина = cols."""
        gap = 1
        left = truncate(left, max(0, cols - len(right) - gap))
        pad = cols - len(left) - len(right)
        if pad < 1:
            pad = 1
        line = left + ' ' * pad + right
        line = line[:cols]
        if selected:
            return styled(line, reverse=True)
        return line

    def _project_row(self, p, cols, selected):
        active = p.get('active', 0)
        marker = '●' if active else '▸'
        name = p['name'] + ('  (here)' if p.get('is_current') else '')
        path = short_path(p['path'])
        if active:
            right = f'{active} active · {p["count"]} sess · {human_age(self.now - p["mtime"])} '
        else:
            right = f'{p["count"]} sess · {human_age(self.now - p["mtime"])} '

        # " ● name  path" — путь занимает остаток строки и обрезается
        # при нехватке.
        base = f' {marker} {name}  '
        avail = max(0, cols - len(right) - 1)
        path_shown = truncate(path, max(0, avail - len(base)))
        left_plain = base + path_shown

        if selected:
            return self._compose(left_plain, right, cols, True)

        marker_c = styled(marker, fg='green' if active else 'blue')
        name_c = styled(name, bold=True) if p.get('is_current') else name
        left = f' {marker_c} {name_c}  ' + styled(path_shown, fg='gray')
        visible_left = len(base) + len(path_shown)
        pad = max(1, cols - visible_left - len(right))
        return left + ' ' * pad + styled(right, fg='gray')

    # Фиксированные колонки правого блока строки сессии — чтобы
    # ветка, msg и возраст выстраивались вертикально, а имена
    # резались с ровным зазором.
    _BRANCH_W = 18
    _MSG_W = 8
    _AGE_W = 8

    def _session_right(self, s, right_text):
        branch = s.get('branch')
        if branch:
            bcell = truncate('⎇ ' + branch, self._BRANCH_W).ljust(self._BRANCH_W) + ' · '
        else:
            bcell = ' ' * (self._BRANCH_W + 3)
        mcell = f'{s["msg_count"]} msg'.rjust(self._MSG_W)
        return f'{bcell}{mcell} · {right_text.rjust(self._AGE_W)} '

    def _session_row(self, s, cols, selected):
        active = s.get('active', False)
        # ромб — фоновый агент: к нему нельзя подключиться через
        # --resume, пока процесс жив
        marker = '◆' if s.get('bg') else '●'
        if active:
            status = s.get('status')
            # только базовый статус в колонку (waitingFor опускаем —
            # иначе блок «едет»); состояние и так видно по цвету
            # точки/строки
            right_text = STATUS_LABEL.get(status, status or 'running')
            if s.get('bg'):
                right_text = 'bg ' + right_text
            color = STATUS_COLOR.get(status, 'green')
        else:
            right_text = human_age(self.now - s['mtime'])
            color = 'gray'
        right = self._session_right(s, right_text)
        left_plain = f' {marker} {s["title"]}'
        if selected:
            return self._compose(left_plain, right, cols, True)
        marker_c = styled(marker, fg=color)
        title_max = max(0, cols - len(right) - 4)
        title = truncate(s['title'], title_max)
        left = f' {marker_c} {title}'
        visible_left = 3 + len(title)
        pad = max(1, cols - visible_left - len(right))
        right_styled = styled(right, fg=color) if active else styled(right, fg='gray')
        return left + ' ' * pad + right_styled

    def current_item(self):
        items = self.items()
        return items[self.sel] if 0 <= self.sel < len(items) else None

    def _input_line(self):
        """Отдельная строка поля ввода (над футером) в режиме ввода."""
        if self.input_mode == 'filter':
            return f' search: {self.input_buffer}▏'
        if self.input_mode == 'search':
            return f' search: {self.input_buffer}▏'
        if self.input_mode == 'rename':
            return f' rename: {self.input_buffer}▏'
        if self.input_mode == 'worktree':
            return f' worktree name (empty = auto): {self.input_buffer}▏'
        return ''

    def _footer(self):
        if self.input_mode == 'filter':
            return ' Enter — keep   Esc — clear'
        if self.input_mode == 'search':
            return ' Enter — search   Esc — cancel'
        if self.input_mode == 'rename':
            return ' Enter — save   Esc — cancel'
        if self.input_mode == 'worktree':
            return ' Enter — create worktree   Esc — cancel'
        if self.status:
            return ' ' + self.status
        if self.screen == 'projects':
            a = 'a — cli only' if self.show_all else 'a — all (sdk)'
            return (f' Enter — open   n — new   w — worktree   c — continue'
                    f'   / — search   {a}   q — quit')
        if self.screen == 'sessions':
            return (' Enter — resume   n — new   w — worktree   f — fork   p — preview'
                    '   r — rename   / — search   Esc — back')
        # preview
        if self.search_matches:
            return (f' match {self.search_idx + 1}/{len(self.search_matches)}'
                    f'   n/N — next/prev   / — search   Esc — back')
        return (' ↑↓ — scroll   g/G — top/bottom   [ ] — prompt   ⌃o — expand'
                '   ⌘c — copy   o — resume   f — fork   / — search   Esc — back')

    def toggle_show_all(self):
        if self.screen != 'projects':
            return
        self.show_all = not self.show_all
        # запомним, на каком проекте стоим, чтобы вернуть курсор туда же
        cur = self.current_item()
        cur_dir = cur['dir_name'] if cur else None
        self.filter_query = ''
        self.rebuild_projects()
        self.sel = 0
        for i, p in enumerate(self.projects):
            if p['dir_name'] == cur_dir:
                self.sel = i
                break
        self.offset = 0
        self.draw_screen()

    def start_filter(self):
        if self.screen not in ('projects', 'sessions'):
            return
        self.start_input('filter', self.filter_query)

    # --- предпросмотр ---

    def open_preview(self, session):
        self.preview_session = session
        self.preview_entries = load_conversation(session['file'])
        self.preview_expanded = set()
        self.clear_selection()
        self.search_query = ''
        self.search_matches = []
        self.search_idx = -1
        self.screen = 'preview'
        self.status = ''
        self.build_preview_lines()
        # открываемся на свежих сообщениях: их и хотят увидеть,
        # а не начало диалога
        self.preview_offset = self._preview_limit()

    def build_preview_lines(self):
        width = max(10, self.screen_size.cols)
        if (width != self._preview_cache_width
                or self.preview_entries is not self._preview_cache_entries):
            self._preview_cache = {}
            self._preview_cache_width = width
            self._preview_cache_entries = self.preview_entries
        root = (self.preview_session or {}).get('cwd') or ''
        self.preview_lines = transcript_lines(self.preview_entries, width,
                                              self.preview_expanded, root,
                                              cache=self._preview_cache)

    def _preview_limit(self):
        return max(0, len(self.preview_lines) - self.visible_rows())

    def preview_scroll(self, delta):
        self.preview_offset = max(0, min(self._preview_limit(),
                                         self.preview_offset + delta))
        self.clear_selection()
        self.status = ''
        self.draw_screen()

    def preview_jump(self, to_end):
        self.preview_offset = self._preview_limit() if to_end else 0
        self.clear_selection()
        self.status = ''
        self.draw_screen()

    def jump_prompt(self, step):
        """[ / ]: к предыдущей/следующей реплике пользователя —
        оглавление диалога.
        """
        rows = [i for i, ln in enumerate(self.preview_lines) if ln.prompt]
        if step > 0:
            nxt = next((r for r in rows if r > self.preview_offset), None)
        else:
            nxt = next((r for r in reversed(rows) if r < self.preview_offset), None)
        if nxt is None:
            self.status = 'no prompts' if not rows else (
                'last prompt' if step > 0 else 'first prompt')
            self.draw_screen()
            return
        self.preview_offset = min(nxt, self._preview_limit())
        self.clear_selection()
        self.status = ''
        self.draw_screen()

    def clear_selection(self):
        self.preview_sel = None
        self.preview_char_sel = None

    def _rebuild_after_fold(self):
        self.clear_selection()
        self.build_preview_lines()
        self.preview_offset = min(self.preview_offset, self._preview_limit())
        self._find_matches()
        self.draw_screen()

    def toggle_fold(self, idx):
        """Клик по строке: раскрыть/свернуть одну запись."""
        if idx < 0:
            return
        self.preview_expanded ^= {idx}
        self._rebuild_after_fold()

    def _foldable(self):
        return {ln.entry for ln in self.preview_lines if ln.entry >= 0}

    def expand_all(self):
        """Ctrl+o / Enter: раскрыть ВЕСЬ свёрнутый вывод разом
        (как в Claude Code); когда раскрыто всё — свернуть обратно.
        """
        foldable = self._foldable()
        if not foldable:
            self.status = 'nothing to expand'
            self.draw_screen()
            return
        self.preview_expanded = foldable if foldable - self.preview_expanded else set()
        self._rebuild_after_fold()

    def copy_selection(self):
        if self.preview_char_sel is not None:
            row, cs, ce = self.preview_char_sel
            text = self.preview_lines[row].text[cs:ce].strip()
            what = f'copied "{truncate(text, 30)}"'
        elif self.preview_sel is not None:
            lo, hi = self.preview_sel
            text = '\n'.join(ln.text for ln in self.preview_lines[lo:hi + 1])
            what = f'copied {hi - lo + 1} lines'
        else:
            self.status = 'select with the mouse first'
            self.draw_screen()
            return
        if text.strip():
            self.print(osc52(text), end='')
            self.status = what
        else:
            self.status = 'nothing to copy'
        self.clear_selection()
        self.draw_screen()

    def _find_matches(self):
        # ищем в видимой (обрезанной по ширине) части строки — как
        # подсветка: совпадение за границей экрана было бы «найдено»,
        # но невидимо
        q = self.search_query.lower()
        cols = self.screen_size.cols
        self.search_matches = [i for i, ln in enumerate(self.preview_lines)
                               if q and q in truncate(ln.text, cols).lower()]
        if not self.search_matches:
            self.search_idx = -1
        elif self.search_idx >= len(self.search_matches):
            self.search_idx = 0

    def run_search(self):
        self.search_idx = -1
        self._find_matches()
        if not self.search_matches:
            return
        self.search_idx = 0
        for j, mi in enumerate(self.search_matches):
            if mi >= self.preview_offset:
                self.search_idx = j
                break
        self._scroll_to_match()

    def search_jump(self, step):
        if not self.search_matches:
            self.status = ('no matches' if self.search_query
                           else 'search first: press /')
            self.draw_screen()
            return
        self.status = ''
        self.search_idx = (self.search_idx + step) % len(self.search_matches)
        self._scroll_to_match()
        self.draw_screen()

    def _scroll_to_match(self):
        if 0 <= self.search_idx < len(self.search_matches):
            # центрируем совпадение в окне
            mi = self.search_matches[self.search_idx]
            self.preview_offset = max(0, mi - self.visible_rows() // 2)

    # --- resume / переименование ---

    def do_resume(self, session, fork=False):
        if not session:
            return
        # Claude Code откажется подключаться к живому фоновому
        # агенту («stop it there first to resume here») — не
        # запускаем заведомо падающий --resume. Форк делает новую
        # сессию из файла, ему живой процесс не мешает.
        if session.get('bg') and not fork:
            self.status = 'background agent — stop it, attach via `claude agents`, or f to fork'
            self.draw_screen()
            return
        cwd = session.get('cwd') or (self.project['path'] if self.project else None)
        self.result = {'action': 'resume', 'session_id': session['id'],
                       'cwd': cwd, 'fork': fork}
        self.quit_loop(0)

    def do_continue(self, project):
        # claude --continue — самая свежая сессия каталога, без
        # захода в список
        if not project:
            return
        self.result = {'action': 'continue', 'cwd': project.get('path')}
        self.quit_loop(0)

    def do_new(self, cwd):
        # claude без --resume — новая сессия в каталоге
        if not cwd:
            return
        self.result = {'action': 'new', 'cwd': cwd}
        self.quit_loop(0)

    def _screen_cwd(self):
        # каталог для нового запуска: проект под курсором либо
        # открытый проект
        if self.screen == 'projects':
            item = self.current_item()
            return item['path'] if item else None
        if self.screen == 'sessions' and self.project:
            return self.project['path']
        return None

    def start_worktree(self):
        # claude --worktree <name> создаёт изолированный worktree и
        # новую сессию; имя опционально (пусто → claude сгенерирует,
        # напр. bright-running-fox)
        cwd = self._screen_cwd()
        if not cwd:
            return
        self._worktree_cwd = cwd
        self.start_input('worktree')

    def start_rename(self):
        if self.screen != 'sessions':
            return
        s = self.current_item()
        if not s:
            return
        self.start_input('rename', s['title'] if s.get('custom') else '')

    def start_search(self):
        if self.screen != 'preview':
            return
        self.start_input('search', self.search_query)

    def _input_cancelled(self, mode):
        if mode == 'filter':
            self.filter_query = ''   # Esc в фильтре — сбросить
            self.sel = 0
            self.offset = 0

    def commit_input(self):
        mode = self.input_mode
        buf = self.input_buffer.strip()
        self.input_mode = None
        self.input_buffer = ''
        if mode == 'filter':
            pass  # фильтр уже применён вживую, просто выходим из ввода
        elif mode == 'search':
            self.search_query = buf
            self.run_search()
        elif mode == 'rename' and buf:
            # Пишем ту же запись, что и /rename в Claude Code — имя
            # станет единым и в плагине, и в самом Claude Code.
            s = self.current_item()
            if s and append_custom_title(s['file'], s['id'], buf):
                s['title'] = buf
                s['custom'] = True
            else:
                self.status = 'rename failed'
        elif mode == 'worktree':
            self.result = {'action': 'worktree', 'cwd': self._worktree_cwd, 'name': buf}
            self.quit_loop(0)
            return
        self.draw_screen()

    # --- навигация ---

    def move(self, delta):
        n = self.current_len()
        if n == 0:
            return
        self.sel = max(0, min(n - 1, self.sel + delta))
        self.draw_screen()

    def activate(self):
        item = self.current_item()
        if not item:
            return
        if self.screen == 'projects':
            self.open_project(item)
            self.draw_screen()
        elif self.screen == 'sessions':
            self.do_resume(item)     # Enter запускает сессию

    def preview_current(self):
        if self.screen != 'sessions':
            return
        item = self.current_item()
        if item:
            self.open_preview(item)
            self.draw_screen()

    def go_back(self):
        # активный фильтр Esc сбрасывает первым, не покидая экран
        if self.screen in ('projects', 'sessions') and self.filter_query:
            self.filter_query = ''
            self.sel = 0
            self.offset = 0
            self.draw_screen()
            return
        if self.screen == 'preview':
            self.screen = 'sessions'
            self.status = ''
            self.draw_screen()
        elif self.screen == 'sessions':
            self.back_to_projects()
            self.draw_screen()
        else:
            self.quit_loop(0)

    # --- ввод ---

    def on_key(self, key_event):
        # kitty шлёт и нажатие, и отпускание — реагируем только на
        # нажатие/повтор, иначе одно нажатие стрелки срабатывает дважды.
        if key_event.type == EventType.RELEASE:
            return
        if chord(key_event, 'ctrl', 'c'):
            self.quit_loop(0)
            return
        k = key_event.key

        if self.input_key(k):
            return

        if self.screen == 'preview' and chord(key_event, 'super', 'c'):
            self.copy_selection()
            return

        if self.screen == 'preview' and key_event.ctrl:
            # chord с буквой самого события = «зажат ровно ctrl»
            letter = to_latin((key_event.key or '').lower())
            if chord(key_event, 'ctrl', letter) and self._preview_ctrl(letter):
                return

        if self.screen == 'preview':
            if k == 'UP':
                self.preview_scroll(-1)
            elif k == 'DOWN':
                self.preview_scroll(1)
            elif k == 'PAGE_UP':
                self.preview_scroll(-self.visible_rows())
            elif k == 'PAGE_DOWN':
                self.preview_scroll(self.visible_rows())
            elif k == 'HOME':
                self.preview_jump(False)
            elif k == 'END':
                self.preview_jump(True)
            elif k == 'ENTER':
                self.expand_all()
            elif k in ('ESCAPE', 'LEFT'):
                self.go_back()
            return

        # projects / sessions
        if k == 'UP':
            self.move(-1)
        elif k == 'DOWN':
            self.move(1)
        elif k == 'PAGE_UP':
            self.move(-self.visible_rows())
        elif k == 'PAGE_DOWN':
            self.move(self.visible_rows())
        elif k == 'HOME':
            self.sel = 0
            self.draw_screen()
        elif k == 'END':
            self.sel = max(0, self.current_len() - 1)
            self.draw_screen()
        elif k == 'ENTER':
            self.activate()
        elif k in ('ESCAPE', 'LEFT'):
            self.go_back()
        elif k == 'RIGHT':
            if self.screen == 'sessions':
                self.preview_current()
            else:
                self.activate()

    def _input_live(self):
        """Живой отклик ввода: для фильтра сразу применяем к списку."""
        if self.input_mode == 'filter':
            self.filter_query = self.input_buffer
            self.sel = 0
            self.offset = 0
        self.draw_screen()

    def _preview_ctrl(self, letter: str) -> bool:
        """Ctrl-хоткеи предпросмотра — единая точка для on_key и
        on_text: на кириллице ctrl+буква приходит не событием клавиши,
        а C0-байтом (конфиг терминала мапит ctrl+<кириллица>
        в send_text).
        """
        if self.screen != 'preview':
            return False
        if letter == 'o':
            self.expand_all()
            return True
        return False

    def on_text(self, text, in_bracketed_paste=False):
        if self.input_text(text):
            return

        for ch in text:
            c = to_latin(ch)
            if self.screen == 'preview':
                if '\x01' <= ch <= '\x1a':          # C0-байт → ctrl+буква
                    self._preview_ctrl(chr(ord(ch) + 96))
                    continue
                if c in ('q', 'Q'):
                    self.quit_loop(0)
                    return
                elif c == '/':
                    self.start_search()
                elif c == 'n':
                    self.search_jump(1)
                elif c == 'N':
                    self.search_jump(-1)
                elif c == 'g':
                    self.preview_jump(False)
                elif c == 'G':
                    self.preview_jump(True)
                elif c == '[':
                    self.jump_prompt(-1)
                elif c == ']':
                    self.jump_prompt(1)
                elif c in ('o', 'O'):
                    self.do_resume(self.preview_session)
                    return
                elif c in ('f', 'F'):
                    self.do_resume(self.preview_session, fork=True)
                    return
                continue

            if c in ('q', 'Q'):
                self.quit_loop(0)
                return
            elif c == '/':
                self.start_filter()
            elif c == 'g':
                self.sel = 0
                self.draw_screen()
            elif c == 'G':
                self.sel = max(0, self.current_len() - 1)
                self.draw_screen()
            elif c in ('a', 'A') and self.screen == 'projects':
                self.toggle_show_all()
            elif c in ('c', 'C') and self.screen == 'projects':
                self.do_continue(self.current_item())
                return
            elif c in ('n', 'N') and self.screen in ('projects', 'sessions'):
                self.do_new(self._screen_cwd())
                return
            elif c in ('w', 'W') and self.screen in ('projects', 'sessions'):
                self.start_worktree()
            elif c in ('o', 'O') and self.screen == 'sessions':
                self.do_resume(self.current_item())
                return
            elif c in ('f', 'F') and self.screen == 'sessions':
                self.do_resume(self.current_item(), fork=True)
                return
            elif c in ('p', 'P') and self.screen == 'sessions':
                self.preview_current()
            elif c in ('r', 'R') and self.screen == 'sessions':
                self.start_rename()

    def _preview_row_at(self, ev):
        r = ev.cell_y - 2                      # шапка + разделитель
        if not (0 <= r < self.visible_rows()):
            return None
        row = self.preview_offset + r
        return row if 0 <= row < len(self.preview_lines) else None

    def on_mouse_event(self, ev):
        # колесо мыши: в предпросмотре — скролл текста, в списках —
        # движение по строкам
        if ev.buttons in (MouseButton.WHEEL_UP, MouseButton.WHEEL_DOWN):
            up = ev.buttons == MouseButton.WHEEL_UP
            if self.screen == 'preview':
                self.preview_scroll(-3 if up else 3)
            else:
                self.move(-1 if up else 1)
            return
        if self.screen == 'preview' and self.drag_select(ev):
            return
        super().on_mouse_event(ev)

    # --- хуки DragSelect (выделение мышью в предпросмотре) ---

    def _sel_row_at(self, ev):
        return self._preview_row_at(ev)

    def _apply_char_sel(self, row, cs, ce):
        self.preview_char_sel = (row, cs, ce)
        self.preview_sel = None

    def _apply_line_sel(self, lo, hi, row):
        self.preview_sel = (lo, hi)
        self.preview_char_sel = None

    def _sel_done(self):
        self.status = 'selected — ⌘c to copy'

    def on_click(self, ev):
        """Клик по строке списка — выбрать; повторный по
        выбранной — открыть.
        В предпросмотре клик раскрывает свёрнутую запись.
        """
        if self.input_mode:
            return
        if self.screen == 'preview':
            self.clear_selection()
            row = self._preview_row_at(ev)
            if row is not None and self.preview_lines[row].entry >= 0:
                self.toggle_fold(self.preview_lines[row].entry)
            else:
                self.draw_screen()
            return
        head = 0 if self.screen == 'projects' else 2   # на проектах шапки нет
        r = ev.cell_y - head
        if r < 0:
            return
        idx = self.offset + r
        if not (0 <= idx < len(self.items())):
            return
        if idx == self.sel:
            self.activate()          # второй клик: проект — войти, сессия — запустить
        else:
            self.sel = idx
            self.draw_screen()

    def on_resize(self, new_size):
        if self.screen == 'preview' and self.preview_entries:
            self.clear_selection()
            self.build_preview_lines()
            self.preview_offset = min(self.preview_offset, self._preview_limit())
            self._find_matches()   # видимая часть строк изменилась вместе с шириной
        self.draw_screen()

    def on_interrupt(self):
        self.quit_loop(0)

    def on_eot(self):
        self.quit_loop(0)


def main(args: list) -> 'dict | None':
    mark_overlay('session')
    now = time.time()
    loop = Loop()
    handler = SessionsHandler(args, now)
    loop.loop(handler)
    return handler.result


def _running_claude(window) -> bool:
    """True, если в окне уже идёт сессия claude — накрывать её
    оверлеем нельзя.
    """
    try:
        procs = list(window.child.foreground_processes)
        procs += list(window.child.background_processes)
    except (AttributeError, OSError):
        return False
    return any(os.path.basename(tok) == 'claude'
               for p in procs for tok in (p.get('cmdline') or []))


@result_handler()
def handle_result(args, result, target_window_id, boss):
    """Выполняется в процессе kitty (вместо UI-процесса).

    Запускает claude поверх активного окна: resume/fork по id,
    continue, new или worktree. Если в окне уже идёт сессия claude —
    открывает новую сплитом рядом, иначе оверлеем в том же окне.
    """
    if not result:
        return
    action = result.get('action')
    if action == 'resume':
        sid = result.get('session_id')
        if not sid:
            return
        claude_args = 'claude --resume ' + shlex.quote(sid)
        if result.get('fork'):
            claude_args += ' --fork-session'
    elif action == 'continue':
        claude_args = 'claude --continue'
    elif action == 'new':
        claude_args = 'claude'
    elif action == 'worktree':
        claude_args = 'claude --worktree'
        name = (result.get('name') or '').strip()
        if name:
            claude_args += ' ' + shlex.quote(name)
    else:
        return

    cwd = result.get('cwd')
    w = boss.window_id_map.get(target_window_id)
    if w is None:
        return   # исходное окно уже закрыто — не запускать относительно «какого-то» окна
    # Окно занято claude — открываем новую сессию отдельным
    # окном-сплитом рядом (--location=vsplit, тот же механизм, что
    # cmd+d в splits.conf: cmd+w закроет только этот сплит и вернёт к
    # соседней сессии, а не весь таб). Иначе оверлей лёг бы поверх
    # текущей сессии (наложение). Свободное окно — оверлеем в том же
    # окне: по выходу из claude вернётся исходный шелл.
    # Запуск через login+interactive шелл, а не `claude` напрямую:
    # PATH и переменные (~/.local/bin, где лежит claude) задаются в
    # .zshrc/.zprofile — при прямом запуске они не подхватываются,
    # claude не находит креды в Keychain и требует /login.
    shell = os.environ.get('SHELL') or '/bin/zsh'
    placement = '--location=vsplit' if _running_claude(w) else '--type=overlay'
    cmd = ['launch', placement]
    if cwd:
        cmd += ['--cwd', cwd]
    cmd += [shell, '-l', '-i', '-c', 'exec ' + claude_args]
    boss.call_remote_control(w, tuple(cmd))


if __name__ == '__main__':
    main(sys.argv)
