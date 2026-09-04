"""Go-to-definition во вьюере: символ под курсором → файл:строка,
пикер при нескольких кандидатах, стек возврата (⌃o) и read-only показ
файла, которого нет среди изменений.

Миксин к DiffTreeView. Определение ищет language server
(`modules/lsp`), поднятый на корне проекта: grep не знает ни про
наследование, ни про vendor, а сервер знает.

Резолв идёт по цепочке, каждый шаг — когда предыдущий пуст:
`textDocument/definition` по точной позиции → `workspace/symbol` по
имени → тот же идентификатор в новой версии файла. Второй шаг нужен
удалённым строкам: они принадлежат старой версии, документа с ней у
сервера нет и быть не может.
"""

import os
import time
from typing import NamedTuple

from kittens.tui.operations import styled

from ..lsp.position import (
    DECL_KINDS,
    Target,
    collapse_overloads,
    encode_character,
    location_target,
    locations,
    prefer_sources,
    rank_symbols,
    raw_index,
)
from ..lsp.rpc import RpcError
from ..lsp.session import NoServer, SessionPool
from ..text import elide_path, short_path, truncate
from .diff import DiffSource, group_key, repo_key
from .git import read_text
from .symbols import find_identifier, symbol_at, word_span


# бит Alt/Option в mouse-событии. kitty кодирует модификаторы мыши
# СВОЕЙ схемой (shift=1, alt=2, ctrl=4, super=8), а не xterm-SGR
# (где alt=8): проверено эмпирически — ⌥+click даёт mods=2. ⌘/Super
# мышью не приходит, поэтому go-to-definition — на ⌥+click.
ALT_MOD = 0b10

# первый ⌥-клик может прийти, пока сервер ещё индексирует: лучше
# подождать его, чем ответить «определения нет»
READY_WAIT = 30.0

# прогрев не по клику, а как только видно файл; задержка гасит
# прокрутку дерева стрелками — иначе сервер поднимался бы на каждый шаг
WARM_DELAY = 0.5

# gopls на большом модуле шлёт прогресс десятки раз в секунду —
# кадр на каждое событие утопил бы цикл
BADGE_INTERVAL = 0.25

# повторный старт по тёплому кэшу укладывается в доли секунды: мигать
# индикатором на такой работе незачем, он нужен для долгой
BADGE_DELAY = 1.0


class DiffRef(NamedTuple):
    """Куда ткнули: всё, что нужно для запроса, снятое в главном
    потоке (фоновому трогать состояние вьюера нельзя).

    side говорит, каким путём резолвить: 'after' — точная позиция в
    рабочем дереве; 'before' — удалённая строка, документа с ней нет;
    'symbol' — просмотр коммита, где подменять документ нельзя вовсе.
    """
    rel: str
    side: str
    line: int
    col: int           # индекс в исходной строке, в код-поинтах
    word: str
    raw: str           # сама строка без развёрнутых табов
    text: str          # содержимое файла, каким его видит пользователь


class GotoDefinitionMixin:

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # путь показанного read-only внешнего файла (None — обычный
        # дифф), стек «назад», активный пикер кандидатов
        self._external: 'str | None' = None
        self._navstack: 'list[dict]' = []
        self._cand: 'list[Target] | None' = None
        self._cand_sym = ''
        self._goto_busy = False
        # пул на репозиторий: у независимых репозиториев свои корни,
        # и один сервер индексировал бы чужой проект
        self._lsp_pools: 'dict[str, SessionPool]' = {}
        self._warm_timer = None
        self._badge_timer = None
        self._badge_at = 0.0

    # --- сессии ---

    def _lsp_pool(self) -> SessionPool:
        """Пул для показанного сейчас репозитория; поднимается лениво —
        открыли файлы в двух репозиториях, работают два, а не все.
        """
        root = self.root or os.getcwd()
        pool = self._lsp_pools.get(root)
        if pool is None:
            pool = self._lsp_pools[root] = SessionPool(root, self._lsp_progress)
        return pool

    def load_diff(self) -> None:
        super().load_diff()
        self._lsp_warm()

    def _lsp_warm(self) -> None:
        """Поднять сервер заранее: индексация большого проекта идёт
        десятки секунд, и пусть она идёт, пока читают дифф.
        """
        if not self.root:
            return
        loop = getattr(self, 'asyncio_loop', None)
        if loop is None:
            return
        if self._warm_timer is not None:
            self._warm_timer.cancel()
        self._warm_timer = loop.call_later(WARM_DELAY, self._lsp_warm_now)

    def _lsp_warm_now(self) -> None:
        self._warm_timer = None
        rel = self._external or (self.current_item() or {}).get('path')
        if not rel:
            return
        pool = self._lsp_pool()

        first_line = self.diff_after.split('\n', 1)[0]

        def work():
            try:
                pool.session_for(rel, first_line)
            except (NoServer, RpcError):
                pass       # молча: язык без сервера не повод для шума
            return None

        self.run_background(work, lambda _: None)

    def _lsp_progress(self) -> None:
        """Колбэк сессии: зовётся из её потока, поэтому в луп."""
        loop = getattr(self, 'asyncio_loop', None)
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._lsp_tick)
        except RuntimeError:
            pass           # кит уже закрыт — показывать прогресс некому

    def _lsp_tick(self) -> None:
        now = time.monotonic()
        left = BADGE_INTERVAL - (now - self._badge_at)
        if left > 0:
            # событие не выбрасываем, а откладываем: последнее из
            # пачки — как раз конец индексации, без него бейдж
            # застыл бы на «97%» до случайного перерисова
            self._badge_later(left)
            return
        self._badge_at = now
        self.draw_screen()

    def _badge_later(self, delay: float) -> None:
        if self._badge_timer is not None:
            return         # ждём уже запланированного кадра
        loop = getattr(self, 'asyncio_loop', None)
        if loop is not None:
            self._badge_timer = loop.call_later(delay, self._badge_flush)

    def _badge_flush(self) -> None:
        self._badge_timer = None
        self._badge_at = time.monotonic()
        self.draw_screen()

    def _lsp_badge(self) -> str:
        """Что показывать, пока сервер не готов: проценты, если он их
        сообщает, иначе время и рост кэша — молчание означает, что всё
        готово.

        Рисуется отдельно от футера и своим цветом: в общем сером
        хвосте подсказок индикатор терялся.
        """
        for session in [s for pool in self._lsp_pools.values() for s in pool.active()]:
            state = session.status()
            if state.state in ('ready', 'failed') or state.elapsed < BADGE_DELAY:
                continue
            lang = session.spec.lang
            if state.percent >= 0:
                return f' ⟳ {lang} {state.percent}% '
            if state.message:
                return f' ⟳ {lang} {state.message} '
            return f' ⟳ {lang} {_mmss(state.elapsed)}{_size(state.cache)} '
        return ''

    def finalize(self) -> None:
        super().finalize()
        for timer in (self._warm_timer, self._badge_timer):
            if timer is not None:
                timer.cancel()
        self._warm_timer = self._badge_timer = None
        for pool in self._lsp_pools.values():
            pool.stop_all()
        self._lsp_pools = {}

    # --- что под курсором ---

    def _doc_ref(self, di: int, col: int) -> 'DiffRef | None':
        """Ячейка диффа → позиция в документе. None — по этой ячейке
        вопроса не задать: гэп, плейсхолдер, гуттер или не слово.
        """
        if not (0 <= di < len(self.diff_plain)) or self._gap_at(di) is not None:
            return None
        gutter = self._gutter_cols()
        if col < gutter + 2:
            return None
        plain = self.diff_plain[di]
        word = symbol_at(plain, col)
        rel = self._external or (self.current_item() or {}).get('path')
        if not word or not rel:
            return None
        line = self.diff_lineno[di] if di < len(self.diff_lineno) else 0
        sign = plain[gutter:gutter + 2]
        if self.source.rev:
            # просмотр коммита: показанный текст не совпадает с диском,
            # а подсунуть его серверу значило бы испортить ему индекс.
            # Номер строки несём — по нему найдём то же место в рабочей
            # версии файла
            return DiffRef(rel, 'symbol', line, 0, word, '', self.diff_after)
        if self.view_mode != 'final' and sign.startswith('-'):
            # строка старой версии: её документа у сервера нет
            return DiffRef(rel, 'before', 0, 0, word, '', self.diff_after)
        raw = _line_at(self.diff_after, line)
        if not line or raw is None:
            return None
        return DiffRef(rel, 'after', line,
                       raw_index(raw, col - (gutter + 2)), word, raw,
                       self.diff_after)

    # --- поиск определения ---

    def goto_definition(self, ref: 'DiffRef | None') -> None:
        """Найти определение и перейти к нему.

        Запрос уходит в фоновый поток: ждать в колбэке нельзя —
        замёрзли бы кадр и ⌃c, а первый запрос ещё и ждёт индексации.
        """
        if ref is None or not self.root or self._goto_busy:
            return
        self._goto_busy = True
        self.flash = f"searching for '{ref.word}'…"
        self.draw_screen()
        pool = self._lsp_pool()
        root = self.root

        def work():
            try:
                return _resolve(pool, ref, root), ''
            except NoServer as e:
                return [], str(e)
            except RpcError as e:
                return [], f'language server: {e}'
            except Exception as e:            # noqa: BLE001
                # граница фонового потока: любая неожиданная ошибка
                # иначе унесла бы с собой и поток, и _goto_busy —
                # кит молча перестал бы отвечать на ⌥-клик вообще
                return [], f'{type(e).__name__}: {e}'

        self.run_background(work, lambda res: self._goto_done(ref.word, *res))

    def _goto_done(self, symbol: str, targets: list, err: str) -> None:
        self._goto_busy = False
        if len(targets) == 1 and self._is_here(targets[0]):
            # клик по самому объявлению: сервер отвечает этой же
            # строкой, и прыжок «в себя» только засорил бы стек ⌃o
            self.flash = f"already at the definition of '{symbol}'"
            self.draw_screen()
            return
        if not targets:
            # сбой сервера иначе выглядел бы уверенным «нет
            # определения» — молча неверный ответ
            self.flash = err or f"no definition for '{symbol}'"
            self.draw_screen()
            return
        if len(targets) == 1:
            self._navigate(targets[0])
        else:
            self._cand, self._cand_sym = targets, symbol
            self.draw_screen()

    def _is_here(self, target: Target) -> bool:
        rel = self._external or (self.current_item() or {}).get('path')
        if target.path != rel:
            return False
        shown = (self.diff_lineno[self.diff_cur]
                 if 0 <= self.diff_cur < len(self.diff_lineno) else 0)
        return target.line == shown

    def goto_from_selection(self) -> None:
        sel = self.diff_char_sel
        if not sel:
            self.flash = 'select a word (drag), then d'
            self.draw_screen()
            return
        row, cs, _ = sel
        self.goto_definition(self._doc_ref(row, cs))

    def goto_click(self, ev) -> None:
        di = self._diff_row_at(ev)
        if di is not None:
            self.goto_definition(self._doc_ref(di, self._diff_col_at(ev)))

    def goto_hover(self, di: int, col: int, mods: int) -> bool:
        """⌥ над идентификатором — строка кликабельна."""
        return bool(mods & ALT_MOD and col >= self._gutter_cols()
                    and word_span(self.diff_plain[di], col))

    # --- снимок позиции во вьюере ---

    def _capture_view(self) -> dict:
        """Куда вернуться после прыжка к определению (⌃o) или выхода
        из поиска по проекту.
        """
        return {'external': self._external, 'repo': self.view_repo,
                'tsel': self.tsel, 'diff_offset': self.diff_offset,
                'diff_cur': self.diff_cur, 'view_mode': self.view_mode,
                'hscroll': self.hscroll, 'left_offset': self.left_offset,
                'focus': self.focus, 'collapsed': set(self.collapsed)}

    def _restore_view(self, s: dict) -> None:
        """Обратная к _capture_view; дерево перестраивает сама.

        Порядок обязателен: view_mode — до load_diff (тот строит строки
        через build_diff_rows, который его читает), hscroll — после неё
        (раньше не известен hscroll_max), left_offset — последним:
        set_tsel по пути правит его под курсор.
        """
        self.collapsed = s.get('collapsed', self.collapsed)
        self.view_mode = s.get('view_mode', 'diff')
        self.rebuild_tree()
        if s.get('external'):
            self._show_file(s['external'], 0, s.get('repo'))
        else:
            self._external = None
            self.set_tsel(s.get('tsel', 0))
            self.load_diff()
        if s.get('hscroll') and self.hscroll != s['hscroll']:
            self.hscroll = min(s['hscroll'], self.hscroll_max)
            self.build_diff_rows()
        self.diff_cur = min(s.get('diff_cur', 0), max(0, len(self.diff_rows) - 1))
        self.diff_offset = min(s.get('diff_offset', 0), self.diff_offset_max())
        self.left_offset = s.get('left_offset', 0)
        self.focus = s.get('focus', 'tree')

    # --- переход ---

    def _reveal_file(self, rel: str, repo: 'str | None') -> None:
        # раскрыть свёрнутых предков, чтобы файл появился строкой дерева
        it = self._item_at(rel, repo)
        if it is None:
            return
        base = repo_key(repo) if repo else ''
        prefix = group_key(it['group'], base) if it.get('group') else base
        if prefix:
            self.collapsed.discard(prefix)
        key = prefix
        for part in rel.split('/')[:-1]:
            key = f'{key}/{part}' if key else part
            self.collapsed.discard(key)
        self.rebuild_tree()

    def _item_at(self, rel: str, repo: 'str | None') -> 'dict | None':
        """Файл в дереве: в мультирепо одноимённый есть и у соседа,
        а цель пришла от сервера, поднятого на своём репозитории.
        """
        return next((x for x in self.filtered
                     if x['path'] == rel and x.get('repo') == repo), None)

    def _tree_row_for(self, rel: str, repo: 'str | None') -> 'int | None':
        for i, r in enumerate(self.rows):
            it = self.filtered[r['idx']] if r['type'] == 'file' else None
            if it is not None and it['path'] == rel and it.get('repo') == repo:
                return i
        return None

    def _navigate(self, target: Target, repo: 'str | None' = None) -> None:
        """Открыть цель во вьюере. repo — её репозиторий; по умолчанию
        тот же, что у показанного файла (определение живёт в своём
        проекте, а Find in Files ведёт и к соседу).
        """
        self._navstack.append(self._capture_view())
        if repo is None:
            repo = self.view_repo
        row = None
        if self._item_at(target.path, repo) is not None:
            self._reveal_file(target.path, repo)
            row = self._tree_row_for(target.path, repo)
        if row is not None:
            self._external = None
            self.set_tsel(row)
            self.load_diff()
            self.focus = 'diff'
            # определение часто на неизменённой строке — в unified она
            # скрыта (свёрнута), центрироваться не на что; финальный вид
            # показывает файл целиком. nav_back вернёт прежний режим.
            if target.line and target.line not in self.diff_lineno:
                self.view_mode = 'final'
                self.build_diff_rows()
            self._center_on_line(target.line)
        else:
            self._show_file(target.path, target.line, repo)
        self.flash = f'{short_path(target.path)}:{target.line}'
        self.draw_screen()

    def _show_file(self, rel: str, line: int, repo: 'str | None' = None) -> None:
        text = self._read_target(rel, repo)
        self._external = rel
        # правая панель ушла в чужой репозиторий: редактор и language
        # server должны идти туда же, а элемента дерева тут нет
        self.view_repo = repo
        self.diff_before = self.diff_after = text
        self.diff_ext = os.path.splitext(rel)[1].lower()
        self.diff_src = DiffSource(text, text)
        self.view_mode = 'final'
        self.hscroll = 0
        self.diff_sel = self.diff_char_sel = None
        self.expanded = {}
        self.build_diff_rows()
        self.focus = 'diff'
        self._center_on_line(line)

    def _read_target(self, rel: str, repo: 'str | None' = None) -> str:
        """Содержимое файла, в который прыгнули.

        Вне репозитория (stdlib) путь абсолютный — source про такой
        файл не знает. Внутри репозитория берём версию источника, но
        при просмотре коммита её может не быть вовсе: `vendor/` и
        прочее из `.gitignore` в снимок не попадает, и вместо файла
        показалось бы «(empty file)».
        """
        if os.path.isabs(rel):
            return read_text(rel)
        text = self.source.read(rel, repo)
        root = repo or self.root
        return text or (read_text(os.path.join(root, rel)) if root else '')

    def nav_back(self) -> None:
        if not self._navstack:
            self.flash = 'nothing to go back to'
            self.draw_screen()
            return
        self._restore_view(self._navstack.pop())
        self.flash = 'back'
        self.draw_screen()

    # --- пикер кандидатов (несколько определений) ---

    def _draw_picker(self) -> None:
        cols = self.screen_size.cols
        self.cmd.clear_screen()
        self.print(styled(truncate(f" definitions of ‘{self._cand_sym}’", cols),
                          fg='green', bold=True))
        self.print(styled('─' * cols, fg='gray'))
        for i, t in enumerate(self._cand[:9]):
            mark = '▎' if t.kind in DECL_KINDS else ' '
            # путь ужимаем, чтобы строку определения не вытеснило за
            # край: у стабов и vendor он длиннее самого кода
            loc = f'{elide_path(short_path(t.path), cols // 3)}:{t.line}'
            self.print(truncate(f' {i + 1} {mark} {loc}   {t.preview}', cols))
        self.print('')
        self.print(styled(truncate(' 1-9 open · Esc cancel', cols), fg='gray'), end='')

    def _pick(self, n: int) -> None:
        targets = self._cand
        if not targets or not 0 <= n < min(9, len(targets)):
            return         # промах цифрой не стоит всего списка
        self._cand, self._cand_sym = None, ''
        self._navigate(targets[n])

    def _close_picker(self) -> None:
        self._cand, self._cand_sym = None, ''
        self.draw_screen()


# ───────────────────── резолв в фоновом потоке ─────────────────────

def _resolve(pool: SessionPool, ref: DiffRef, root: str) -> 'list[Target]':
    session = pool.session_for(ref.rel, ref.text.split('\n', 1)[0])
    session.wait_ready(READY_WAIT)
    preview = _previewer(root)
    targets = (_by_position(session, ref, root, preview)
               or _by_worktree(session, ref, root, preview))
    if targets:
        return targets
    found = rank_symbols(session.symbols(ref.word), ref.word, ref.rel, root, preview)
    return found or _by_twin(session, ref, root, preview)


def _by_position(session, ref: DiffRef, root: str, preview) -> 'list[Target]':
    """Точный вопрос: та самая позиция в рабочем дереве."""
    if ref.side != 'after' or not ref.line:
        return []
    path = os.path.join(root, ref.rel)
    session.open_doc(path, ref.text)
    character = encode_character(ref.raw, ref.col, session.encoding)
    result = session.definition(path, ref.line, character)
    return _targets(result, root, preview)


def _by_worktree(session, ref: DiffRef, root: str, preview) -> 'list[Target]':
    """Просмотр коммита: снимок на экране серверу не показать, зато
    рабочая версия файла у него уже проиндексирована — если тот же
    идентификатор есть и в ней, ответ будет точным, а не списком
    одноимённых символов со всего проекта.
    """
    if ref.side != 'symbol':
        return []
    path = os.path.join(root, ref.rel)
    text = read_text(path)
    found = _locate(text, ref) if text else None
    if found is None:
        return []
    line, col = found
    return _ask_at(session, path, text, line, col, root, preview)


def _locate(text: str, ref: DiffRef) -> 'tuple[int, int] | None':
    """Где искать символ в рабочей версии файла.

    Рабочее дерево ушло вперёд относительно коммита, поэтому строка с
    тем же номером — только первое предположение; дальше берём
    ближайшее к ней вхождение. Простое «первое вхождение в файле»
    почти всегда попадало бы в строку импорта, а не в место вызова.
    """
    lines = text.splitlines()
    hits = [i for i, line in enumerate(lines, start=1)
            if find_identifier(line, ref.word) is not None]
    if not hits:
        return None
    best = min(hits, key=lambda i: abs(i - ref.line)) if ref.line else hits[0]
    found = find_identifier(lines[best - 1], ref.word)
    return (best, found[1]) if found else None


def _by_twin(session, ref: DiffRef, root: str, preview) -> 'list[Target]':
    """Последняя попытка для удалённой строки: тот же идентификатор
    почти всегда есть и в новой версии файла — спрашиваем по нему.
    """
    if ref.side != 'before':
        return []
    found = find_identifier(ref.text, ref.word)
    if found is None:
        return []
    line, col = found
    return _ask_at(session, os.path.join(root, ref.rel), ref.text, line, col,
                   root, preview)


def _ask_at(session, path: str, text: str, line: int, col: int, root: str,
            preview) -> 'list[Target]':
    session.open_doc(path, text)
    raw = _line_at(text, line) or ''
    result = session.definition(path, line,
                                encode_character(raw, col, session.encoding))
    return _targets(result, root, preview)


def _targets(result: object, root: str, preview) -> 'list[Target]':
    found = [location_target(loc, root, preview) for loc in locations(result)]
    return prefer_sources(collapse_overloads([t for t in found if t is not None]))


def _previewer(root: str):
    """Строка файла-цели для пикера. Кэш на один запрос: кандидаты
    часто лежат в одном файле.
    """
    cache: 'dict[str, list[str]]' = {}

    def preview(rel: str, line: int) -> str:
        lines = cache.get(rel)
        if lines is None:
            path = rel if os.path.isabs(rel) else os.path.join(root, rel)
            lines = read_text(path).splitlines()
            cache[rel] = lines
        return lines[line - 1].strip() if 0 < line <= len(lines) else ''

    return preview


def _line_at(text: str, line: int) -> 'str | None':
    if line < 1:
        return None
    lines = text.splitlines()
    return lines[line - 1] if line <= len(lines) else None


def _mmss(seconds: float) -> str:
    return f'{int(seconds) // 60}:{int(seconds) % 60:02d}'


def _size(nbytes: int) -> str:
    return f' · {nbytes // (1024 * 1024)} MB' if nbytes >= 1024 * 1024 else ''
