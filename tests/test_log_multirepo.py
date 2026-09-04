"""log в папке над несколькими репозиториями: общая лента по времени,
фокус на одном репозитории, fetch веером и push в свой репозиторий.
"""

import os
import subprocess
import time
import unittest

from test_vcs_workspace import MultiRepoCase

import kittymock  # noqa: F401
import log as L
from kittymock import draw_text, run_threads_inline, wire


class LogMultiRepoTest(MultiRepoCase):

    def setUp(self):
        super().setUp()
        # коммиты вперемешку по времени и позже init из фикстуры
        base = int(time.time())
        self.commit(self.api, 'api one', base + 10)
        self.commit(self.web, 'web one', base + 20)
        self.commit(self.api, 'api two', base + 30)
        self.h = L.CommitLogHandler([], self.workspace())
        wire(self.h, rows=40, cols=120)
        self.h.load_state()

    def commit(self, root: str, message: str, ts: int) -> None:
        stamp = f'{ts} +0000'
        env = {**os.environ, 'GIT_AUTHOR_DATE': stamp, 'GIT_COMMITTER_DATE': stamp}
        with open(os.path.join(root, 'a.txt'), 'a') as f:
            f.write(f'{message}\n')
        subprocess.run(['git', '-C', root, 'commit', '-am', message],
                       check=True, capture_output=True, env=env)

    def subjects(self) -> list:
        return [c['subject'] for c in self.h.commits]

    # --- лента ---

    def test_commits_of_all_repos_ordered_by_time(self):
        self.assertEqual(self.subjects()[:3], ['api two', 'web one', 'api one'])

    def test_every_commit_knows_its_repo(self):
        names = {c['repo_name'] for c in self.h.commits}
        self.assertEqual(names, {'api', 'web'})

    def test_graph_is_off_across_repos(self):
        self.assertEqual(self.h.graph, [])

    def test_row_shows_repo_and_age(self):
        row = self.h._commit_row(self.h.commits[0], 100, False)
        self.assertIn('api', row)
        self.assertIn('api two', row)

    def test_columns_line_up_regardless_of_merge_or_hash_length(self):
        # значок мержа в колонке хеша сдвигал бы всю строку
        rows = [self.h._commit_row(c, 100, False) for c in self.h.commits]
        starts = {r.index(c['short']) for r, c in zip(rows, self.h.commits)}
        self.assertEqual(len(starts), 1, rows)

    def test_row_never_exceeds_its_width(self):
        for c in self.h.commits:
            for width in (40, 80, 200):
                self.assertEqual(len(self.h._commit_row(c, width, False)), width)
                self.assertEqual(len(self.h._commit_row(c, width, True)), width)

    def test_header_counts_repos(self):
        self.h.draw_screen()
        self.assertIn('2 repos', kittymock.draw_text(self.h))

    def test_graph_key_explains_itself(self):
        self.h.out = []
        self.h.on_text('g')
        self.assertIn('single repo', draw_text(self.h))

    def test_filter_matches_repo_name(self):
        # по имени репозитория остаётся вся его история
        self.h.commit_filter = 'web'
        self.h.rebuild_commits()
        self.assertEqual(self.subjects(), ['web one', 'init'])

    # --- фокус ---

    def test_focus_narrows_the_feed_and_brings_graph_back(self):
        self.h.on_text('R')
        self.assertIsNotNone(self.h._repo_menu)
        self.h.on_text('1')
        self.assertEqual(self.h.repo_focus, self.api)
        self.assertEqual(self.subjects(), ['api two', 'api one', 'init'])
        self.assertTrue(self.h.graph)

    def test_focus_keeps_unpushed_counts_of_other_repos(self):
        # в фокусе загружена история одного, но ↑N в меню нужен по всем
        self.h.unpushed = {self.api: {'a'}, self.web: {'b'}}
        self.h.set_repo_focus(self.api)
        self.assertIn(self.web, self.h.unpushed)
        web = next(r for r in self.h.ws.repos if r.root == self.web)
        self.assertIn('↑1', self.h._repo_summary(web))

    def test_filter_by_repo_name_does_not_bring_the_graph_back(self):
        # от истории остаётся произвольный кусок: лейны легли бы мимо
        self.h.commit_filter = 'web'
        self.h.rebuild_commits()
        self.assertEqual(self.h.graph, [])
        self.h.on_text('g')
        self.assertIn('single repo', draw_text(self.h))

    def test_escape_clears_focus(self):
        self.h.set_repo_focus(self.web)
        self.h.on_key(kittymock.KeyEvent('ESCAPE'))
        self.assertIsNone(self.h.repo_focus)
        self.assertFalse(self.h.confirm_active)

    # --- операции ---

    def test_open_commit_uses_its_own_repo(self):
        self.h.sel = 1                       # web one
        self.h.open_commit()
        self.assertEqual(self.h.root, self.web)
        self.assertEqual(self.h.source.sha, self.h.commits[1]['sha'])

    def test_open_commit_shows_its_files(self):
        # файлы коммита не знают про репозитории, и уровень репозиториев
        # в дереве оставил бы их без секции — то есть экран пустым
        self.h.sel = 1
        self.h.open_commit()
        self.assertEqual([it['path'] for it in self.h.items], ['a.txt'])
        self.assertTrue([r for r in self.h.rows if r['type'] == 'file'])
        self.assertIn('web one', self.h.diff_after)

    def test_open_commit_while_focused_still_shows_files(self):
        self.h.set_repo_focus(self.api)
        self.h.sel = 0
        self.h.open_commit()
        self.assertTrue([r for r in self.h.rows if r['type'] == 'file'])

    def test_back_from_a_commit_restores_the_repo_hint(self):
        # источник коммита однорепозиторный: оставшись, он выключил бы
        # R и на списке коммитов
        self.h.open_commit()
        self.h.back_to_commits()
        self.assertIn('R repo', self.h._commits_footer())
        self.assertTrue(self.h.source_ws().multi)

    def test_copied_path_from_a_commit_keeps_the_repo(self):
        copied = []
        self.h._copy_clipboard = copied.append
        self.h.sel = 1                       # web one
        self.h.open_commit()
        self.h.tsel = next(i for i, r in enumerate(self.h.rows) if r['type'] == 'file')
        self.h.copy_path()
        self.assertEqual(copied, ['@web/a.txt'])
        self.assertIn('web/a.txt', self.h._header())

    def test_find_in_a_commit_stays_in_its_repo(self):
        # ищем в снимке коммита api строку, которая есть только у web
        self.h.sel = 0                       # api two
        self.h.open_commit()
        self.h.find_query = 'web one'
        self.h.toggle_find()
        self.assertEqual(self.h.items, [])

    def test_fetch_covers_every_repo(self):
        run_threads_inline(self)
        seen = []
        L.fetch = lambda root: seen.append(root)
        self.addCleanup(setattr, L, 'fetch', _REAL_FETCH)
        self.h.do_fetch()
        self.assertEqual(sorted(seen), sorted([self.api, self.web]))
        self.assertFalse(self.h._fetching)

    def test_push_targets_the_repo_of_the_selected_commit(self):
        asked = []

        def fake_target(root):
            asked.append(root)
            return 'main', 'origin/main', 1

        L.push_target = fake_target
        self.addCleanup(setattr, L, 'push_target', _REAL_PUSH_TARGET)
        self.h.sel = 1                       # web one
        self.h.start_push()
        self.assertEqual(asked, [self.web])
        self.assertEqual(self.h.pending_push[0], self.web)
        self.assertIn('from web', self.h._pending_prompt())


_REAL_FETCH = L.fetch
_REAL_PUSH_TARGET = L.push_target


if __name__ == '__main__':
    unittest.main()
