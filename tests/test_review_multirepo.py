"""review в папке над несколькими репозиториями: единое дерево,
операции и @-ссылки, привязанные к своему репозиторию.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from test_vcs_workspace import MultiRepoCase

import kittymock  # noqa: F401
import review as R
from kittymock import KeyEvent, draw_text, wire
from modules.vcs.diff import group_key, repo_key
from modules.vcs.source import UNVERSIONED
from modules.vcs.workspace import Repo


class ReviewMultiRepoTest(MultiRepoCase):

    def setUp(self):
        super().setUp()
        # правки в обоих репозиториях, в том числе одноимённые файлы
        self.write(os.path.join(self.api, 'a.txt'), 'a1\nAPI EDIT\n')
        self.write(os.path.join(self.api, 'src/only-api.txt'), 'x\n')
        self.write(os.path.join(self.web, 'a.txt'), 'a1\nWEB EDIT\n')
        self.h = R.ReviewHandler([], self.workspace())
        wire(self.h, rows=40, cols=120)
        self.h.load_source()

    def _repo_rows(self):
        return [r for r in self.h.rows if r.get('repo_root')]

    def _row(self, pred):
        for i, r in enumerate(self.h.rows):
            if pred(r):
                return i, r
        raise AssertionError(f'row not found among {[r["name"] for r in self.h.rows]}')

    def _select(self, pred):
        i, row = self._row(pred)
        self.h.set_tsel(i)
        return row

    def _status(self, root):
        out = subprocess.run(['git', '-C', root, 'status', '--porcelain'],
                             capture_output=True, text=True, env=os.environ)
        return out.stdout

    # --- дерево ---

    def test_repos_are_top_level_nodes(self):
        self.assertEqual([r['name'] for r in self._repo_rows()], ['api', 'web'])
        self.assertEqual({r['depth'] for r in self._repo_rows()}, {0})

    def test_repo_node_shows_branch_and_totals(self):
        api, web = self._repo_rows()
        self.assertEqual((api['branch'], web['branch']), ('main', 'feat'))
        visible = [it for it in self.h.filtered if it.get('repo') == self.api]
        self.assertEqual(api['count'], len(visible))
        self.assertEqual(api['stat'][1], 1)     # одна строка заменена на APIEDIT

    def test_files_belong_to_their_repo(self):
        for row in self.h.rows:
            if row['type'] == 'file':
                self.assertIn(row['repo'], (self.api, self.web))

    def test_unversioned_group_lives_inside_repo(self):
        key = group_key(UNVERSIONED, repo_key(self.api))
        self.assertIn(key, self.h.collapsed)
        self.h.collapsed.discard(key)
        self.h.rebuild_tree()
        _, row = self._row(lambda r: r.get('group_root') and r['repo'] == self.api)
        self.assertEqual(row['key'], key)

    def test_unversioned_node_names_its_repo(self):
        # группа стоит в конце секции: её заголовок репозитория к
        # этому месту уже уехал за край экрана
        _, row = self._row(lambda r: r.get('group_root') and r['repo'] == self.api)
        self.assertIn('api', self.h._dir_cell(row, 60, '  ', False))

    def test_header_shows_path_with_repo(self):
        self._select(lambda r: r['type'] == 'file' and r['name'] == 'a.txt'
                     and r['repo'] == self.web)
        self.assertIn('▸ web/a.txt', self.h._header())

    def test_repo_without_changes_has_no_node(self):
        subprocess.run(['git', '-C', self.web, 'checkout', '--', 'a.txt'],
                       check=True, capture_output=True, env=os.environ)
        self.h.refresh()
        self.assertEqual([r['name'] for r in self._repo_rows()], ['api'])

    def test_header_counts_repos(self):
        self.h.draw_screen()
        self.assertIn('2 repos', kittymock.draw_text(self.h))

    # --- одинаковые пути в разных репозиториях ---

    def test_same_path_in_two_repos_is_two_items(self):
        paths = [(it.get('repo'), it['path']) for it in self.h.items if it['path'] == 'a.txt']
        self.assertEqual(sorted(paths), sorted([(self.api, 'a.txt'), (self.web, 'a.txt')]))

    def test_marking_one_repo_does_not_mark_the_other(self):
        i, _ = self._row(lambda r: r['type'] == 'file' and r['name'] == 'a.txt'
                         and r['repo'] == self.api)
        self.h._toggle_mark_at(i)
        self.assertEqual(self.h.marked, {(self.api, 'a.txt')})
        j, _ = self._row(lambda r: r['type'] == 'file' and r['name'] == 'a.txt'
                         and r['repo'] == self.web)
        self.assertFalse(self.h._row_highlight(j))

    def test_copied_path_is_relative_to_the_folder(self):
        copied = []
        self.h._copy_clipboard = copied.append
        self._select(lambda r: r['type'] == 'file' and r['name'] == 'a.txt'
                     and r['repo'] == self.web)
        self.h.copy_path()
        self.assertEqual(copied, ['@web/a.txt'])

    def test_copied_marks_carry_their_repo(self):
        copied = []
        self.h._copy_clipboard = copied.append
        for repo in (self.api, self.web):
            i, _ = self._row(lambda r, repo=repo: r['type'] == 'file'
                             and r['name'] == 'a.txt' and r['repo'] == repo)
            self.h._toggle_mark_at(i)
        self.h.copy_path()
        self.assertEqual(copied, ['@api/a.txt\n@web/a.txt'])

    def test_root_follows_the_cursor_before_the_diff_loads(self):
        # дифф грузится отложенно (80 мс), а редактор и language
        # server спрашивают корень сразу — по элементу под курсором
        self._select(lambda r: r['type'] == 'file' and r['name'] == 'a.txt'
                     and r['repo'] == self.api)
        self.h.load_diff()
        self._select(lambda r: r['type'] == 'file' and r['name'] == 'a.txt'
                     and r['repo'] == self.web)
        self.assertEqual(self.h.root, self.web)
        os.environ['EDITOR'] = 'vim'
        try:
            self.h.open_editor()
        finally:
            os.environ.pop('EDITOR', None)
        self.assertTrue(self.h.action['path'].startswith(self.web), self.h.action)

    def test_diff_reads_the_right_repo(self):
        self._select(lambda r: r['type'] == 'file' and r['name'] == 'a.txt'
                     and r['repo'] == self.web)
        self.h.load_diff()
        self.assertEqual(self.h.view_repo, self.web)
        self.assertIn('WEB EDIT', self.h.diff_after)

    # --- операции ---

    def test_stage_on_repo_node_touches_only_that_repo(self):
        self._select(lambda r: r.get('repo_root') and r['repo'] == self.api)
        self.h.stage_selected()
        self.assertIn('M  a.txt', self._status(self.api))
        self.assertIn(' M a.txt', self._status(self.web))    # web не тронут

    def test_revert_on_repo_node_touches_only_that_repo(self):
        self._select(lambda r: r.get('repo_root') and r['repo'] == self.web)
        self.h.start_revert()
        self.h._confirm_pending()
        self.assertEqual(self._status(self.web), '')
        self.assertIn(' M a.txt', self._status(self.api))

    def test_refresh_picks_up_changes_in_every_repo(self):
        self.write(os.path.join(self.web, 'fresh.txt'), 'new\n')
        self.h.refresh()
        self.assertIn((self.web, 'fresh.txt'),
                      [(it.get('repo'), it['path']) for it in self.h.items])

    # --- фокус на одном репозитории (R) ---

    def test_r_opens_repo_menu_and_digit_focuses(self):
        self.h.on_text('R')
        self.assertIsNotNone(self.h._repo_menu)
        self.h.on_text('2')
        self.assertEqual(self.h.repo_focus, self.web)
        self.assertIsNone(self.h._repo_menu)

    def test_focus_collapses_tree_to_one_repo(self):
        self.h.set_repo_focus(self.api)
        self.assertEqual(self._repo_rows(), [])      # уровень репозиториев не нужен
        self.assertEqual({it.get('repo') for it in self.h.filtered}, {self.api})

    def test_zero_returns_all_repos(self):
        self.h.set_repo_focus(self.api)
        self.h.on_text('R')
        self.h.on_text('0')
        self.assertIsNone(self.h.repo_focus)
        self.assertEqual([r['name'] for r in self._repo_rows()], ['api', 'web'])

    def test_escape_clears_focus_before_quitting(self):
        self.h.set_repo_focus(self.api)
        self.h.on_key(kittymock.KeyEvent('ESCAPE'))
        self.assertIsNone(self.h.repo_focus)
        self.assertFalse(self.h.confirm_active)

    def test_footer_shows_focus(self):
        self.assertIn('R repo', self.h._review_footer())
        self.h.set_repo_focus(self.web)
        self.assertIn('R web ✕', self.h._review_footer())

    def test_menu_is_ignored_with_a_single_repo(self):
        h = R.ReviewHandler([], R.open_workspace(self.api))
        wire(h, rows=20, cols=100)
        h.load_source()
        h.on_text('R')
        self.assertIsNone(h._repo_menu)          # R остаётся за refresh

    # --- вся работа ветки (b) ---

    def test_base_mode_compares_each_repo_with_its_own_base(self):
        # web ушёл в свою ветку от master, api сидит на своей базе:
        # у каждого репозитория сравнение своё
        self.git(self.web, 'branch', 'master')
        self.write(os.path.join(self.web, 'shipped.txt'), 'done\n')
        self.git(self.web, 'add', '-A')
        self.git(self.web, 'commit', '-m', 'shipped in branch')
        self.h.refresh()
        self.h.on_text('b')
        seen = {(it.get('repo'), it['path']) for it in self.h.items}
        self.assertIn((self.web, 'shipped.txt'), seen)    # закоммичено в ветке
        self.assertIn((self.api, 'a.txt'), seen)          # api — рабочие правки
        self.assertIn('vs master', self.h._header())

    def test_bases_are_probed_once_until_forgotten(self):
        # каждая проба — symbolic-ref и до шести rev-parse на
        # репозиторий, а зовут её и переключение режима, и обновление
        from modules.vcs import source as S
        calls = []
        real = S.base_ref

        def probe(root):
            calls.append(root)
            return real(root)

        S.base_ref = probe
        self.addCleanup(setattr, S, 'base_ref', real)
        src = self.h.source
        src.find_bases()
        src.find_bases()
        self.assertEqual(len(calls), len(self.h.ws.repos))
        src.forget_bases()
        src.find_bases()
        self.assertEqual(len(calls), 2 * len(self.h.ws.repos))

    def test_menu_reaches_repos_past_the_ninth(self):
        # цифр на все репозитории не хватает: дальше идут буквы
        ws = R.Workspace(self.base, [R.open_workspace(self.api).repos[0]]
                         + [_repo(self.base, f'r{i}') for i in range(1, 12)])
        h = R.ReviewHandler([], ws)
        wire(h, rows=30, cols=100)
        h.open_repo_menu()
        self.assertEqual(len(h._repo_menu), 12)
        h.repo_menu_text('c')                # 12-й пункт: 9 цифр + a, b, c
        self.assertEqual(h.repo_focus, ws.repos[11].root)
        self.assertIn('1-9 a-c focus', draw_text(h))

    # --- поиск и комментарии ---

    def test_find_searches_every_repo(self):
        self.h.find_query = 'a1'
        self.h.toggle_find()
        self.assertTrue(self.h.find_mode)
        self.assertEqual({it.get('repo') for it in self.h.items}, {self.api, self.web})
        self.assertEqual([r['name'] for r in self._repo_rows()], ['api', 'web'])

    def test_enter_on_a_match_opens_the_file_of_its_own_repo(self):
        # совпадение из соседнего репозитория: путь без его корня
        # прочитался бы из открытого сейчас — и панель осталась пустой
        self.write(os.path.join(self.web, 'only-web.txt'), 'needle here\n')
        self.git(self.web, 'add', '-A')
        self.git(self.web, 'commit', '-m', 'web only')
        self.h.refresh()
        self._select(lambda r: r['type'] == 'file' and r['name'] == 'a.txt'
                     and r['repo'] == self.api)
        self.h.load_diff()
        self.h.find_query = 'needle'
        self.h.toggle_find()
        self.h.input_key('ENTER')          # запрос введён, курсор в результатах
        self._select(lambda r: r['type'] == 'file' and r['name'] == 'only-web.txt')
        self.h.load_diff()
        self.h.set_focus('diff')
        self.h.on_key(KeyEvent(key='ENTER'))
        self.assertEqual(self.h._external, 'only-web.txt')
        self.assertIn('needle here', self.h.diff_after)
        self.assertEqual(self.h.root, self.web)

    def test_find_honours_repo_focus(self):
        self.h.set_repo_focus(self.web)
        self.h.find_query = 'a1'
        self.h.toggle_find()
        self.assertEqual({it.get('repo') for it in self.h.items}, {self.web})

    def test_comment_markdown_carries_the_repo_prefix(self):
        self._select(lambda r: r['type'] == 'file' and r['name'] == 'a.txt'
                     and r['repo'] == self.api)
        self.h.load_diff()
        self.h.set_focus('diff')
        self.h.diff_cur = self.h._first_commentable(0)
        self.h.start_comment()
        self.h.input_buffer = 'why?'
        self.h.commit_input()
        self.assertIn('## api/a.txt', self.h._review_markdown())

    def test_comments_of_same_named_files_do_not_mix(self):
        for repo in (self.api, self.web):
            self._select(lambda r, repo=repo: r['type'] == 'file'
                         and r['name'] == 'a.txt' and r['repo'] == repo)
            self.h.load_diff()
            self.h.set_focus('diff')
            self.h.diff_cur = self.h._first_commentable(0)
            self.h.start_comment()
            self.h.input_buffer = f'note for {self.h.ws.name_of(repo)}'
            self.h.commit_input()
        self.assertEqual(len(self.h.annots), 2)
        md = self.h._review_markdown()
        self.assertIn('## api/a.txt', md)
        self.assertIn('## web/a.txt', md)

    def test_folder_of_the_group_name_is_not_folded_with_the_group(self):
        # ключ группы «Unversioned Files» внутри секции репозитория
        # не должен совпасть с ключом одноимённой настоящей папки
        path = os.path.join(self.api, UNVERSIONED, 'x.txt')
        self.write(path, 'v1\n')
        self.git(self.api, 'add', '-A')
        self.git(self.api, 'commit', '-m', 'folder named like the group')
        self.write(path, 'v2\n')
        self.h.refresh()
        _, folder = self._row(lambda r: r['type'] == 'dir' and r['name'] == UNVERSIONED
                              and not r.get('group_root'))
        self.assertNotEqual(folder['key'], group_key(UNVERSIONED, repo_key(self.api)))

    def test_folder_without_repos_reports_no_repository(self):
        empty = tempfile.mkdtemp(prefix='ccrev_empty_')
        try:
            h = R.ReviewHandler([], R.open_workspace(empty))
            wire(h, rows=20, cols=80)
            h.load_source()
            self.assertEqual(h.items, [])
            self.assertIn('not a git repository', h.status)
        finally:
            shutil.rmtree(empty, ignore_errors=True)


def _repo(base: str, name: str) -> Repo:
    return Repo(os.path.join(base, name), name)


class ReviewCleanMultiRepoTest(MultiRepoCase):
    """Папка, где все репозитории чисты."""

    def test_clean_repos_report_no_changes(self):
        # пробу «а не репозиторий ли сама папка» git проваливает,
        # и её ошибка не должна дожить до пустого дерева
        self.git(self.api, 'add', '-A')    # вложенный репозиторий фикстуры
        self.git(self.api, 'commit', '-m', 'nothing left uncommitted')
        h = R.ReviewHandler([], self.workspace())
        wire(h, rows=20, cols=100)
        h.load_source()
        self.assertEqual(h.items, [])
        self.assertEqual(h.status, '')
        h.draw_screen()
        self.assertIn('no changes', draw_text(h))


if __name__ == '__main__':
    unittest.main()
