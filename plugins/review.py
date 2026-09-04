#!/usr/bin/env python3
"""
review — kitten для kitty.

Двухпанельный оверлей для ревью незакоммиченных правок git:
слева дерево изменённых файлов (в стиле IDE, со сворачиванием
папок), справа — unified diff выделенного файла с подсветкой
синтаксиса, word-diff, поиском и прыжками по изменениям,
вживую.

Сам экран ревью — общий класс modules.vcs.screen.ReviewScreen
(комментарии к строкам, go-to-definition, Find in Files, метки,
редактор); тем же экраном log показывает изменения коммита. Здесь
только специфика рабочего дерева: живой refresh, git add и откат
правок.

Подключение в ~/.config/kitty/kitty.conf:
    map cmd+shift+r kitten /path/to/familiar/plugins/review.py
"""

import os
import sys

from kittens.tui.handler import result_handler


# Пакет modules лежит рядом с этим файлом. При запуске через
# `kitten path.py` (CLI/автодополнение) kitty не добавляет его
# папку в sys.path; при штатном launch папка и так в sys.path
# на время загрузки, но __file__ там отсутствует.
if '__file__' in globals():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.keylayout import to_latin
from modules.overlay import mark_overlay, restore_layout
from modules.text import plural, short_path
from modules.vcs.diff import group_key, repo_key
from modules.vcs.git import last_error
from modules.vcs.screen import ReviewScreen, apply_result, run_screen
from modules.vcs.source import UNVERSIONED, WorkTreeSource
from modules.vcs.workspace import Workspace, by_repo, open_workspace
from modules.vcs.worktree import revert_paths, stage_paths


class ReviewHandler(ReviewScreen):

    QUIT_CONFIRM_MSG = 'Are you sure you want to close review?'

    def __init__(self, args: list[str], ws: Workspace) -> None:
        super().__init__(ws, WorkTreeSource(ws))
        # новые файлы обычно не ревьюят построчно — группа свёрнута
        # (в мультирепо она своя у каждого репозитория)
        self.collapsed.add(group_key(UNVERSIONED))
        self.collapsed.update(group_key(UNVERSIONED, repo_key(r.root)) for r in ws.repos)
        self.cli_args = args
        # {корень: (tracked, untracked)}, ждёт подтверждения
        self.pending_revert: 'dict[str, tuple[list[str], list[str]]] | None' = None

    # --- хуки экрана ревью ---

    def _header(self) -> str:
        header = f' {short_path(self.ws.base)}'
        if self.ws.multi:
            header += f' · {plural(len(self.ws.repos), "repo")}'
        if self.source.vs_base:
            header += f' · vs {self.source.base_name()}'
        header += f' ({self.n_files}'
        header += f'/{len(self.items)})' if self.filter_query else ')'
        cur = self.current_item()
        if self._external:
            header += f'   ▸ {self._external} (read-only)'
        elif cur:
            header += f'   ▸ {self._copy_rel(cur["path"], cur.get("repo"))}'
        return header

    def _escape_bottom(self) -> None:
        # дно каскада: вместо тихого выхода — подтверждение
        self.start_quit_confirm()

    def _tree_actions(self) -> str:
        if self.source.vs_base:
            return f' · b working tree ({self.source.base_name()} now)'
        stage = ' · + stage' if self._selected_items() else ''
        revert = ' · - revert' if self._revert_targets() else ''
        return stage + revert + ' · b vs branch'

    def _repo_summary(self, repo) -> str:
        n = sum(1 for it in self.items if it.get('repo') == repo.root)
        branch = self.source.branches.get(repo.root, '')
        return f'{branch}   {plural(n, "file") if n else "clean"}'

    def _host_text(self, ch: str) -> bool:
        if ch == '+':
            self.stage_selected()
        elif ch == '-':
            self.start_revert()
        elif to_latin(ch) in ('b', 'B') and not self._ro_block():
            # как stage и revert: под read-only видом (внешний файл,
            # результаты поиска) пересобирать дерево нельзя
            self.toggle_base()
        return False

    def refresh(self) -> None:
        # точка расхождения с базой уезжает от коммита в неё и от
        # fetch — на обновлении экрана перепросим
        self.source.forget_bases()
        super().refresh()

    # --- сравнение с базовой веткой ---

    def toggle_base(self) -> None:
        """Показать всю работу ветки (и закоммиченную тоже) вместо
        одних незакоммиченных правок.
        """
        if not self.source.vs_base and not self.source.find_bases():
            # на самой базовой ветке сравнивать не с чем, и молчаливый
            # пустой экран хуже, чем прямой ответ
            self.flash = 'no base branch to compare with'
            self.draw_screen()
            return
        self.source.vs_base = not self.source.vs_base
        self.flash = (f'vs {self.source.base_name()}' if self.source.vs_base
                      else 'working tree')
        self.load_source()
        self.draw_screen()

    def _blocked_by_base(self) -> bool:
        if not self.source.vs_base:
            return False
        self.flash = 'press b for the working tree to stage or revert'
        self.draw_screen()
        return True

    # --- git add ---

    @staticmethod
    def _stageable(it: dict) -> bool:
        """Есть ли что добавлять в индекс: вторая буква статуса git —
        рабочее дерево. Полностью staged файл ('M ', 'A ') повторный
        git add не изменит, untracked ('??') — изменит.
        """
        xy = it.get('xy', '')
        return len(xy) > 1 and xy[1] != ' '

    def _selected_items(self) -> list[dict]:
        return self._items_under_cursor(self._stageable)

    def stage_selected(self) -> None:
        if self._ro_block() or self._blocked_by_base() or not self.ws.repos:
            return
        items = self._selected_items()
        if not items:
            self.flash = 'nothing to stage here'
            self.draw_screen()
            return
        # git add — по одному вызову на репозиторий: узел дерева в
        # мультирепо собирает файлы сразу из нескольких
        ok = True
        for root, group in by_repo(items).items():
            ok = stage_paths(root or self.root, [it['path'] for it in group]) and ok
        if ok:
            self.flash = f'staged {plural(len(items), "file")}'
        else:
            self.flash = f'git add failed: {last_error()}'
        self.refresh()

    # --- откат изменений (git restore / удаление новых файлов) ---

    def _revert_targets(self) -> 'dict[str, tuple[list[str], list[str]]]':
        """Что откатывать, по репозиториям:
        {корень: (tracked, untracked)}.
        """
        out = {}
        for root, group in by_repo(self._items_under_cursor(lambda it: True)).items():
            out[root or self.root] = ([it['path'] for it in group if not it['untracked']],
                                      [it['path'] for it in group if it['untracked']])
        return out

    def start_revert(self) -> None:
        """Спросить подтверждение: откат необратим, а new-файлы ещё и
        не восстановить из git.
        """
        if self._ro_block() or self._blocked_by_base() or not self.ws.repos:
            return
        targets = self._revert_targets()
        if not targets:
            self.flash = 'nothing to revert here'
            self.draw_screen()
            return
        self.pending_revert = targets
        self.draw_screen()

    def _pending_active(self) -> bool:
        return self.pending_revert is not None

    def _cancel_pending(self) -> None:
        if self.pending_revert is None:
            return
        self.pending_revert = None
        self.flash = 'revert cancelled'
        self.draw_screen()

    def _confirm_pending(self) -> None:
        targets, self.pending_revert = self.pending_revert, None
        n, ok = 0, True
        for root, (tracked, untracked) in targets.items():
            n += len(tracked) + len(untracked)
            ok = revert_paths(root, tracked, untracked) and ok
        if ok:
            self.flash = f'reverted {plural(n, "file")}'
        else:
            self.flash = f'revert failed: {last_error()}'
        self.refresh()

    def _pending_prompt(self) -> str:
        total = sum(len(t) + len(u) for t, u in self.pending_revert.values())
        new = sum(len(u) for _, u in self.pending_revert.values())
        deleted = ''
        if new:
            deleted = f', {plural(new, "new file")} will be deleted for good'
        return f' revert {plural(total, "file")}{deleted}?   y — yes   any other key — no'


def main(args: list[str]) -> dict:
    mark_overlay('review')
    return run_screen(ReviewHandler(args, open_workspace(os.getcwd())))


@result_handler()
def handle_result(args: list[str], answer: 'dict | None',
                  target_window_id: int, boss) -> None:
    restore_layout(boss, target_window_id)
    apply_result(answer, target_window_id, boss)


if __name__ == '__main__':
    main(sys.argv)
