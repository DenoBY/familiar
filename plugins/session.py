#!/usr/bin/env python3
"""
session — kitten для kitty.

Оверлей для просмотра и управления сессиями Claude Code.

Точка входа кита; логика разнесена по пакету modules.session:
util (строки, раскладка, возраст), data (чтение проектов/сессий
из ~/.claude), transcript и markdown (рендер диалога в строки
экрана), preview (состояние и логика экрана предпросмотра).

Подключение в ~/.config/kitty/kitty.conf:
    map cmd+shift+s kitten /Users/deno/Projects/kitty/plugins/session.py
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
from modules.inputline import InputLine
from modules.keylayout import chord
from modules.overlay import mark_overlay
from modules.pointer import PointerCursor
from modules.session.data import (
    STATUS_COLOR,
    STATUS_LABEL,
    append_custom_title,
    build_projects,
    load_sessions,
    running_sessions,
    scan_projects,
)
from modules.session.preview import Preview
from modules.session.util import human_age, short_path, to_latin, truncate
from modules.update import start_check, update_hint


class SessionsHandler(AtomicDraw, InputLine, DragSelect, PointerCursor, Handler):

    # full (не buttons_and_drag): нужны события движения без нажатой
    # кнопки — иначе не поймать наведение для смены формы указателя.
    mouse_tracking = MouseTracking.full

    def __init__(self, args: list[str], now: float) -> None:
        self.cli_args = args
        self.now = now
        self.result = None
        self._all_projects = []     # сырой скан (с probes всех сессий)
        self.projects = []          # отфильтрованный список для показа
        self.sessions = []
        self.project = None
        self.screen = 'projects'    # 'projects' | 'sessions' | 'preview'
        self.sel = 0
        self.offset = 0
        self.status = ''            # строка-подсказка/сообщение снизу
        self.flash = ''             # разовое сообщение поверх футера (AtomicDraw)
        self.show_all = False       # False = только cli, True = включая sdk
        self.running = {}
        self.running_ids = set()
        self.preview = Preview()
        self._worktree_cwd = None
        self.filter_query = ''

    # --- жизненный цикл ---

    def initialize(self) -> None:
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

        self.flash = update_hint() or ''
        start_check()
        self.draw_screen()

    def rebuild_projects(self) -> None:
        self.projects = build_projects(self._all_projects, self.running_ids,
                                       self.show_all)

    def finalize(self) -> None:
        self.cmd.set_cursor_visible(True)
        self.reset_pointer()

    # --- переходы между экранами ---

    def open_project(self, project: 'dict | None') -> None:
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

    def back_to_projects(self) -> None:
        self.screen = 'projects'
        self.project = None
        self.sessions = []
        self.sel = 0
        self.offset = 0
        self.filter_query = ''
        self.status = ''

    # --- геометрия ---

    def visible_rows(self) -> int:
        # на экране проектов шапки нет — только футер (1 строка),
        # на остальных: шапка + разделитель + футер (3 строки).
        reserved = 1 if self.screen == 'projects' else 3
        if self.input_mode:
            reserved += 1   # отдельная строка поля ввода над футером
        return max(1, self.screen_size.rows - reserved)

    def items(self) -> list[dict]:
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

    def current_len(self) -> int:
        return len(self.items())

    def ensure_visible(self) -> None:
        vis = self.visible_rows()
        if self.sel < self.offset:
            self.offset = self.sel
        elif self.sel >= self.offset + vis:
            self.offset = self.sel - vis + 1

    # --- отрисовка ---

    def _draw_frame(self) -> None:
        self.cmd.clear_screen()
        cols = self.screen_size.cols

        if self.screen == 'preview':
            self._draw_preview(cols)
            self.flash = ''
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

        if self.input_mode:
            self.print(styled(truncate(self._input_line(), cols), fg='cyan', bold=True))

        # footer — без финального перевода строки, иначе экран
        # прокрутится вверх на одну строку и шапка уедет за верх окна.
        self.print(styled(truncate(self._footer(), cols),
                          fg='green' if self.flash else 'gray'), end='')
        self.flash = ''

    def _draw_preview(self, cols: int) -> None:
        p = self.preview
        title = p.session['title'] if p.session else ''
        name = self.project['name'] if self.project else ''
        header = f' {name} · {title}'
        self.print(styled(truncate(header, cols), fg='green', bold=True))
        self.print(styled('─' * cols, fg='gray'))

        vis = self.visible_rows()
        p.clamp(vis)
        end = min(len(p.lines), p.offset + vis)
        cur_match = p.current_match()
        for i in range(p.offset, end):
            self.print(p.line_render(i, cols, i == cur_match))
        for _ in range(vis - (end - p.offset)):
            self.print()

        if self.input_mode:
            self.print(styled(truncate(self._input_line(), cols), fg='cyan', bold=True))

        self.print(styled(truncate(self._footer(), cols),
                          fg='green' if self.flash else 'gray'), end='')

    def _compose(self, left: str, right: str, cols: int, selected: bool) -> str:
        """При нехватке ширины усекается left, right остаётся целым."""
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

    def _project_row(self, p: dict, cols: int, selected: bool) -> str:
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

    def _session_right(self, s: dict, right_text: str) -> str:
        branch = s.get('branch')
        if branch:
            bcell = truncate('⎇ ' + branch, self._BRANCH_W).ljust(self._BRANCH_W) + ' · '
        else:
            bcell = ' ' * (self._BRANCH_W + 3)
        mcell = f'{s["msg_count"]} msg'.rjust(self._MSG_W)
        return f'{bcell}{mcell} · {right_text.rjust(self._AGE_W)} '

    def _session_row(self, s: dict, cols: int, selected: bool) -> str:
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

    def current_item(self) -> 'dict | None':
        items = self.items()
        return items[self.sel] if 0 <= self.sel < len(items) else None

    def _input_line(self) -> str:
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

    def _footer(self) -> str:
        if self.input_mode == 'filter':
            return ' Enter — keep   Esc — clear'
        if self.input_mode == 'search':
            return ' Enter — search   Esc — cancel'
        if self.input_mode == 'rename':
            return ' Enter — save   Esc — cancel'
        if self.input_mode == 'worktree':
            return ' Enter — create worktree   Esc — cancel'
        if self.flash:
            return ' ' + self.flash
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
        matches = self.preview.search_matches
        if matches:
            return (f' match {self.preview.search_idx + 1}/{len(matches)}'
                    f'   n/N — next/prev   / — search   Esc — back')
        return (' ↑↓ — scroll   g/G — top/bottom   [ ] — prompt   ⌃o — expand'
                '   ⌘c — copy   o — resume   f — fork   / — search   Esc — back')

    def toggle_show_all(self) -> None:
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

    def start_filter(self) -> None:
        if self.screen not in ('projects', 'sessions'):
            return
        self.start_input('filter', self.filter_query)

    # --- предпросмотр (делегирование в Preview) ---

    def open_preview(self, session: dict) -> None:
        self.screen = 'preview'
        self.status = ''
        self.preview.open(session, self.screen_size.cols, self.visible_rows())

    def preview_scroll(self, delta: int) -> None:
        self.preview.scroll(delta, self.visible_rows())
        self.status = ''
        self.draw_screen()

    def preview_jump(self, to_end: bool) -> None:
        self.preview.jump(to_end, self.visible_rows())
        self.status = ''
        self.draw_screen()

    def jump_prompt(self, step: int) -> None:
        self.status = self.preview.jump_prompt(step, self.visible_rows())
        self.draw_screen()

    def toggle_fold(self, idx: int) -> None:
        self.preview.toggle_fold(idx, self.screen_size.cols, self.visible_rows())
        self.draw_screen()

    def expand_all(self) -> None:
        msg = self.preview.expand_all(self.screen_size.cols, self.visible_rows())
        if msg:
            self.status = msg
        self.draw_screen()

    def copy_selection(self) -> None:
        got = self.preview.selection_text()
        if got is None:
            self.status = 'select with the mouse first'
            self.draw_screen()
            return
        text, what = got
        if text.strip():
            self.print(osc52(text), end='')
            self.status = what
        else:
            self.status = 'nothing to copy'
        self.preview.clear_selection()
        self.draw_screen()

    def search_jump(self, step: int) -> None:
        self.status = self.preview.search_jump(step, self.visible_rows())
        self.draw_screen()

    # --- resume / переименование ---

    def do_resume(self, session: 'dict | None', fork: bool = False) -> None:
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

    def do_continue(self, project: 'dict | None') -> None:
        # claude --continue — самая свежая сессия каталога, без
        # захода в список
        if not project:
            return
        self.result = {'action': 'continue', 'cwd': project.get('path')}
        self.quit_loop(0)

    def do_new(self, cwd: 'str | None') -> None:
        # claude без --resume — новая сессия в каталоге
        if not cwd:
            return
        self.result = {'action': 'new', 'cwd': cwd}
        self.quit_loop(0)

    def _screen_cwd(self) -> 'str | None':
        # каталог для нового запуска: проект под курсором либо
        # открытый проект
        if self.screen == 'projects':
            item = self.current_item()
            return item['path'] if item else None
        if self.screen == 'sessions' and self.project:
            return self.project['path']
        return None

    def start_worktree(self) -> None:
        # claude --worktree <name> создаёт изолированный worktree и
        # новую сессию; имя опционально (пусто → claude сгенерирует,
        # напр. bright-running-fox)
        cwd = self._screen_cwd()
        if not cwd:
            return
        self._worktree_cwd = cwd
        self.start_input('worktree')

    def start_rename(self) -> None:
        if self.screen != 'sessions':
            return
        s = self.current_item()
        if not s:
            return
        self.start_input('rename', s['title'] if s.get('custom') else '')

    def start_search(self) -> None:
        if self.screen != 'preview':
            return
        self.start_input('search', self.preview.search_query)

    def _input_cancelled(self, mode: str) -> None:
        if mode == 'filter':
            self.filter_query = ''   # Esc в фильтре — сбросить
            self.sel = 0
            self.offset = 0

    def commit_input(self) -> None:
        mode = self.input_mode
        buf = self.input_buffer.strip()
        self.input_mode = None
        self.input_buffer = ''
        if mode == 'filter':
            pass  # фильтр уже применён вживую, просто выходим из ввода
        elif mode == 'search':
            self.preview.run_search(buf, self.screen_size.cols,
                                    self.visible_rows())
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

    def move(self, delta: int) -> None:
        n = self.current_len()
        if n == 0:
            return
        self.sel = max(0, min(n - 1, self.sel + delta))
        self.draw_screen()

    def activate(self) -> None:
        item = self.current_item()
        if not item:
            return
        if self.screen == 'projects':
            self.open_project(item)
            self.draw_screen()
        elif self.screen == 'sessions':
            self.do_resume(item)     # Enter запускает сессию

    def preview_current(self) -> None:
        if self.screen != 'sessions':
            return
        item = self.current_item()
        if item:
            self.open_preview(item)
            self.draw_screen()

    def go_back(self) -> None:
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

    def on_key(self, key_event) -> None:
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

    def _input_live(self) -> None:
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

    def on_text(self, text: str, in_bracketed_paste: bool = False) -> None:
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
                    self.do_resume(self.preview.session)
                    return
                elif c in ('f', 'F'):
                    self.do_resume(self.preview.session, fork=True)
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

    def _preview_row_at(self, ev) -> 'int | None':
        r = ev.cell_y - 2                      # шапка + разделитель
        if not (0 <= r < self.visible_rows()):
            return None
        row = self.preview.offset + r
        return row if 0 <= row < len(self.preview.lines) else None

    def _wanted_pointer(self, ev) -> 'str | None':
        # только в просмотре сессии: рука — на сворачиваемой записи,
        # текст — на прочих строках (drag-select); в списках стрелка
        if self.screen != 'preview':
            return None
        row = self._preview_row_at(ev)
        if row is None:
            return None
        return 'pointer' if self.preview.lines[row].entry >= 0 else 'text'

    def on_mouse_event(self, ev) -> None:
        self.update_pointer(ev)
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

    def _sel_row_at(self, ev) -> 'int | None':
        return self._preview_row_at(ev)

    def _apply_char_sel(self, row: int, cs: int, ce: int) -> None:
        self.preview.char_sel = (row, cs, ce)
        self.preview.sel = None

    def _apply_line_sel(self, lo: int, hi: int, row: int) -> None:
        self.preview.sel = (lo, hi)
        self.preview.char_sel = None

    def _sel_done(self) -> None:
        self.status = 'selected — ⌘c to copy'

    def on_click(self, ev) -> None:
        """Клик по строке списка — выбрать; повторный по
        выбранной — открыть.
        В предпросмотре клик раскрывает свёрнутую запись.
        """
        if self.input_mode:
            return
        if self.screen == 'preview':
            self.preview.clear_selection()
            row = self._preview_row_at(ev)
            if row is not None and self.preview.lines[row].entry >= 0:
                self.toggle_fold(self.preview.lines[row].entry)
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

    def on_resize(self, new_size) -> None:
        if self.screen == 'preview' and self.preview.entries:
            p = self.preview
            p.clear_selection()
            p.rebuild(self.screen_size.cols)
            p.clamp(self.visible_rows())
            # видимая часть строк изменилась вместе с шириной
            p.find_matches(self.screen_size.cols)
        self.draw_screen()

    def on_interrupt(self) -> None:
        self.quit_loop(0)

    def on_eot(self) -> None:
        self.quit_loop(0)


def main(args: list[str]) -> 'dict | None':
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
    for p in procs:
        for tok in p.get('cmdline') or []:
            if os.path.basename(tok) == 'claude':
                return True
    return False


@result_handler()
def handle_result(args: list[str], result: 'dict | None',
                  target_window_id: int, boss) -> None:
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
