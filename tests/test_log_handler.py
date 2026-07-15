import os
import shutil
import subprocess
import tempfile
import unittest

import kittymock  # noqa: F401
import log as L
from kittymock import MouseEvent, draw_text, wire


_ENV = {
    'GIT_AUTHOR_NAME': 'Alice', 'GIT_AUTHOR_EMAIL': 'a@e',
    'GIT_COMMITTER_NAME': 'Alice', 'GIT_COMMITTER_EMAIL': 'a@e',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


class LogHandlerTest(unittest.TestCase):
    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in _ENV}
        os.environ.update(_ENV)
        self.repo = tempfile.mkdtemp(prefix='cclog_h_')
        self._git('init', '-b', 'main')
        self._write('a.txt', 'a1\na2\na3\n')
        self._git('add', '-A')
        self._git('commit', '-m', 'first')
        self._write('a.txt', 'a1\na2 changed\na3\n')
        self._write('src/new.py', 'x = 1\n')
        self._git('add', '-A')
        self._git('commit', '-m', 'add feature')

        self.h = L.CommitLogHandler([], self.repo)
        wire(self.h, rows=30, cols=120)
        self.h.reload_commits()

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _git(self, *args):
        subprocess.run(['git', '-C', self.repo, *args], check=True,
                       capture_output=True, env=os.environ)

    def _write(self, rel, content):
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w') as f:
            f.write(content)

    # --- список коммитов ---

    def test_reload_lists_commits_newest_first(self):
        self.assertEqual([c['subject'] for c in self.h.commits],
                         ['add feature', 'first'])

    def test_git_error_shown_when_log_fails(self):
        d = tempfile.mkdtemp(prefix='cclog_notrepo_')
        try:
            self.h.root = d
            self.h.reload_commits()
            self.assertEqual(self.h.commits, [])
            self.assertIn('not a git repository', self.h.status)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_draw_commits_smoke(self):
        self.h.draw_screen()
        text = draw_text(self.h)
        self.assertIn('current branch', text)
        self.assertIn('add feature', text)

    def test_footer_offers_the_other_mode(self):
        self.assertIn('a all branches', self.h._footer())
        self.h.all_branches = True
        self.assertIn('a current branch', self.h._footer())

    def test_filter_commits(self):
        self.h.filter_query = 'feature'
        self.h.rebuild_commits()
        self.assertEqual([c['subject'] for c in self.h.commits], ['add feature'])

    def test_escape_clears_filter_then_asks_to_close(self):
        self.h.filter_query = 'feature'
        self.h.rebuild_commits()
        self.h._commits_key('ESCAPE')           # применённый фильтр сбрасывается
        self.assertEqual(self.h.filter_query, '')
        self.assertEqual(len(self.h.commits), 2)
        self.h._commits_key('ESCAPE')           # дно каскада: вопрос вместо выхода
        self.assertTrue(self.h.confirm_active)
        self.assertEqual(self.h.quits, [])

    def test_quit_confirm_no_stays_yes_quits(self):
        self.h._commits_key('ESCAPE')
        self.h.on_text('n')
        self.assertFalse(self.h.confirm_active)
        self.assertEqual(self.h.quits, [])
        self.h._commits_key('ESCAPE')
        self.h.on_text('y')
        self.assertEqual(self.h.quits, [0])

    def test_toggle_mode_reloads(self):
        self.h.toggle_mode()
        self.assertTrue(self.h.all_branches)
        self.h.toggle_mode()
        self.assertFalse(self.h.all_branches)

    def test_detail_panel_shows_commit_info(self):
        self.h.sel = 0
        text = '\n'.join(self.h._detail_lines(50))
        self.assertIn('add feature', text)                 # сообщение коммита
        self.assertIn('a@e', text)                         # email автора
        self.assertIn('branches', text)

    # --- push ---

    def _push_ready(self):
        """Репозиторий с удалёнкой и незапушенным коммитом + loop,
        исполняющий executor синхронно.
        """
        remote = tempfile.mkdtemp(prefix='cclog_pushremote_')
        self.addCleanup(shutil.rmtree, remote, True)
        subprocess.run(['git', 'init', '--bare', '-q', remote], check=True,
                       capture_output=True, env=os.environ)
        self._git('remote', 'add', 'origin', remote)

        class Fut:
            def __init__(self, v):
                self.v = v

            def cancelled(self):
                return False

            def exception(self):
                return None

            def result(self):
                return self.v

            def add_done_callback(self, cb):
                cb(self)

        class Loop(kittymock.ImmediateLoop):
            def run_in_executor(self, _pool, fn, *a):
                return Fut(fn(*a))

        self.h.asyncio_loop = Loop()
        self.h.reload_commits()
        return remote

    def test_push_asks_before_publishing_anything(self):
        remote = self._push_ready()
        self.h.on_text('p')
        self.assertIsNotNone(self.h.pending_push)
        branch, up, n = self.h.pending_push
        self.assertEqual((branch, up), ('main', None))
        self.h.out = []
        self.h.draw_screen()
        self.assertIn('push', draw_text(self.h))
        self.assertIn('y — yes', draw_text(self.h))
        heads = subprocess.run(['git', '--git-dir', remote, 'branch'],
                               capture_output=True, text=True, env=os.environ).stdout
        self.assertEqual(heads.strip(), '')      # пока ничего не улетело

    def test_any_key_but_y_cancels_push(self):
        remote = self._push_ready()
        self.h.on_text('p')
        self.h.on_text('n')
        self.assertIsNone(self.h.pending_push)
        heads = subprocess.run(['git', '--git-dir', remote, 'branch'],
                               capture_output=True, text=True, env=os.environ).stdout
        self.assertEqual(heads.strip(), '')

    def test_y_pushes_and_clears_unpushed(self):
        remote = self._push_ready()
        self.assertTrue(self.h.unpushed)
        self.h.on_text('p')
        self.h.on_text('y')
        self.assertIsNone(self.h.pending_push)
        self.assertFalse(self.h._pushing)
        heads = subprocess.run(['git', '--git-dir', remote, 'branch'],
                               capture_output=True, text=True, env=os.environ).stdout
        self.assertIn('main', heads)
        self.assertEqual(self.h.unpushed, set())

    def test_push_hint_only_while_something_is_unpushed(self):
        self.h.unpushed = {'deadbeef'}
        self.h.out = []
        self.h.draw_screen()
        self.assertIn('p push', draw_text(self.h))

        self.h.unpushed = set()
        self.h.out = []
        self.h.draw_screen()
        self.assertNotIn('p push', draw_text(self.h))

    def test_nothing_to_push_without_remote(self):
        self.h.on_text('p')
        self.assertIsNone(self.h.pending_push)   # удалёнки нет — публиковать некуда

    def test_header_marks_that_more_commits_may_follow(self):
        # счётчик — сколько загружено, а не сколько в ветке; без «+»
        # он читается как «в истории всего столько коммитов»
        self.h.exhausted = False
        self.h.out = []
        self.h.draw_screen()
        self.assertIn(f'({len(self.h.commits)}+)', draw_text(self.h))

        self.h.exhausted = True
        self.h.out = []
        self.h.draw_screen()
        self.assertIn(f'({len(self.h.commits)})', draw_text(self.h))

    def test_detail_panel_is_brief_until_loaded(self):
        self.h.sel = 0
        self.h._detail_cache.clear()
        text = '\n'.join(self.h._detail_lines(50))
        self.assertIn('add feature', text)      # то, что уже есть в списке
        self.assertIn('Alice', text)
        self.assertNotIn('a@e', text)           # за этим пришлось бы идти в git
        self.assertNotIn('branches', text)

    def test_fast_scroll_does_not_touch_git_on_every_step(self):
        scheduled = []

        class Timer:
            def __init__(self):
                self.cancelled = False

            def cancel(self):
                self.cancelled = True

        class DeferredLoop:
            def call_later(self, delay, cb, *args):
                t = Timer()
                scheduled.append((t, cb, args))
                return t

        self.h.asyncio_loop = DeferredLoop()
        self.h._detail_cache.clear()
        calls = []
        self.addCleanup(setattr, L, 'commit_detail', L.commit_detail)

        def fake_detail(root, sha):
            calls.append(sha)
            return {'body': '', 'author_email': '', 'committer': '',
                    'committer_email': '', 'branches': []}

        L.commit_detail = fake_detail

        self.h.sel = 0
        self.h.draw_screen()
        self.h.move(1)                          # быстрый скролл: два шага подряд
        self.h.move(-1)
        self.assertEqual(calls, [])             # во время прокрутки git не зовём
        self.assertTrue(scheduled[0][0].cancelled)   # прежний таймер отменён
        self.assertFalse(scheduled[-1][0].cancelled)

        timer, cb, args = scheduled[-1]
        cb(*args)                               # прокрутка утихла
        self.assertEqual(calls, [self.h.commits[self.h.sel]['sha']])

    def test_draw_commits_with_panel_smoke(self):
        wire(self.h, rows=30, cols=130)                    # широкий экран → панель видна
        self.h.draw_screen()
        self.assertIn('branches', draw_text(self.h))

    def test_copy_commit_writes_hash(self):
        self.h.sel = 0
        self.h.copy_commit()
        self.assertTrue(any('\x1b]52;c;' in str(x) for x in self.h.out))

    def test_commit_row_shows_ref(self):
        # HEAD-ветка (main) должна попасть в строку выбранного коммита
        row = self.h._commit_row(self.h.commits[0], 120, False)
        self.assertIn('main', row)

    def test_display_refs_collapses_remote(self):
        self.assertEqual(
            L.display_refs([('master', 'head'), ('origin/master', 'remote')]),
            [('origin & master', 'head')])                 # local+remote → один чип
        # ветка со слэшем + одноимённая удалёнка → не дублируется
        self.assertEqual(
            L.display_refs([('feature/x', 'branch'), ('origin/feature/x', 'remote')]),
            [('origin & feature/x', 'branch')])
        self.assertEqual(
            L.display_refs([('origin/PP-1', 'remote'), ('v1.0', 'tag')]),
            [('origin/PP-1', 'remote'), ('v1.0', 'tag')])  # без локальной — как есть

    def test_commit_row_columns_aligned(self):
        # автор/дата — фикс-колонки: у строк разной длины
        # хвост начинается одинаково
        c1 = dict(self.h.commits[0], subject='x', refs=[])
        c2 = dict(self.h.commits[0], subject='y' * 60, refs=[])
        r1 = self.h._commit_row(c1, 100, False)
        r2 = self.h._commit_row(c2, 100, False)
        self.assertEqual(r1.index('Alice'), r2.index('Alice'))

    # --- открытие коммита → экран diff ---

    def test_enter_opens_diff(self):
        self.h.sel = 0
        self.h.open_commit()
        self.assertEqual(self.h.screen, 'diff')
        self.assertEqual(self.h.commit['subject'], 'add feature')
        self.assertEqual(self.h.n_files, 2)                # a.txt + src/new.py
        self.h.draw_screen()
        self.assertIn(self.h.commit['short'], draw_text(self.h))

    def test_diff_shows_changed_lines(self):
        self.h.sel = 0
        self.h.open_commit()
        # выбрать a.txt (модифицирован) — в диффе
        # есть новая версия строки
        self.h.tsel = next(i for i, r in enumerate(self.h.rows)
                           if r['type'] == 'file' and r['name'] == 'a.txt')
        self.h.load_diff()
        self.assertTrue(any('a2 changed' in p for p in self.h.diff_plain))

    def test_escape_returns_to_commits(self):
        self.h.sel = 0
        self.h.open_commit()
        self.h._diff_key('ESCAPE')                         # tree → назад к списку
        self.assertEqual(self.h.screen, 'commits')

    # --- копирование ---

    def test_copy_path_writes_clipboard(self):
        self.h.sel = 0
        self.h.open_commit()
        self.h.copy_path()
        self.assertTrue(any('\x1b]52;c;' in str(x) for x in self.h.out))

    def test_yank_code_from_commit_version(self):
        self.h.sel = 0
        self.h.open_commit()
        self.h.tsel = next(i for i, r in enumerate(self.h.rows)
                           if r['type'] == 'file' and r['name'] == 'a.txt')
        self.h.load_diff()
        self.h.set_focus('diff')
        code, a, b = self.h._yank_code(*self.h._sel_range())
        self.assertIn(code, self.h.diff_after)             # код берётся из версии коммита

    # --- мышь ---

    def test_click_commit_twice_opens(self):
        self.h.draw_screen()
        self.h.on_click(MouseEvent(cell_x=2, cell_y=2))    # первая строка списка (head=2)
        self.assertEqual(self.h.sel, 0)
        self.h.on_click(MouseEvent(cell_x=2, cell_y=2))    # повтор — открыть
        self.assertEqual(self.h.screen, 'diff')


if __name__ == '__main__':
    unittest.main()
