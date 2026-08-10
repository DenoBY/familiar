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

from modules.overlay import mark_overlay, restore_layout
from modules.text import plural, short_path
from modules.vcs.diff import group_key
from modules.vcs.git import git_root, last_error
from modules.vcs.screen import ReviewScreen, apply_result, run_screen
from modules.vcs.source import UNVERSIONED, WorkTreeSource
from modules.vcs.worktree import revert_paths, stage_paths


class ReviewHandler(ReviewScreen):

    QUIT_CONFIRM_MSG = 'Are you sure you want to close review?'

    def __init__(self, args: list[str], cwd: str, root: 'str | None') -> None:
        super().__init__(root, WorkTreeSource(root))
        # новые файлы обычно не ревьюят построчно — группа свёрнута
        self.collapsed.add(group_key(UNVERSIONED))
        self.cli_args = args
        self.cwd = cwd
        # (tracked, untracked), ждёт подтверждения
        self.pending_revert: 'tuple[list[str], list[str]] | None' = None

    # --- хуки экрана ревью ---

    def _header(self) -> str:
        header = f' {short_path(self.root or self.cwd)} ({self.n_files}'
        header += f'/{len(self.items)})' if self.filter_query else ')'
        cur = self.current_item()
        if self._external:
            header += f'   ▸ {self._external} (read-only)'
        elif cur:
            header += f'   ▸ {cur["path"]}'
        return header

    def _escape_bottom(self) -> None:
        # дно каскада: вместо тихого выхода — подтверждение
        self.start_quit_confirm()

    def _tree_actions(self) -> str:
        stage = ' · + stage' if self._selected_paths() else ''
        revert = ' · - revert' if any(self._revert_targets()) else ''
        return stage + revert

    def _host_text(self, ch: str) -> bool:
        if ch == '+':
            self.stage_selected()
        elif ch == '-':
            self.start_revert()
        return False

    # --- git add ---

    @staticmethod
    def _stageable(it: dict) -> bool:
        """Есть ли что добавлять в индекс: вторая буква статуса git —
        рабочее дерево. Полностью staged файл ('M ', 'A ') повторный
        git add не изменит, untracked ('??') — изменит.
        """
        xy = it['xy']
        return len(xy) > 1 and xy[1] != ' '

    def _selected_paths(self) -> list[str]:
        return [it['path'] for it in self._items_under_cursor(self._stageable)]

    def stage_selected(self) -> None:
        if self._ro_block() or not self.root:
            return
        paths = self._selected_paths()
        if not paths:
            self.flash = 'nothing to stage here'
            self.draw_screen()
            return
        if stage_paths(self.root, paths):
            self.flash = f'staged {plural(len(paths), "file")}'
        else:
            self.flash = f'git add failed: {last_error()}'
        self.refresh()

    # --- откат изменений (git restore / удаление новых файлов) ---

    def _revert_targets(self) -> tuple[list[str], list[str]]:
        items = self._items_under_cursor(lambda it: True)
        tracked = [it['path'] for it in items if not it['untracked']]
        untracked = [it['path'] for it in items if it['untracked']]
        return tracked, untracked

    def start_revert(self) -> None:
        """Спросить подтверждение: откат необратим, а new-файлы ещё и
        не восстановить из git.
        """
        if self._ro_block() or not self.root:
            return
        tracked, untracked = self._revert_targets()
        if not tracked and not untracked:
            self.flash = 'nothing to revert here'
            self.draw_screen()
            return
        self.pending_revert = (tracked, untracked)
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
        tracked, untracked = self.pending_revert
        self.pending_revert = None
        n = len(tracked) + len(untracked)
        if revert_paths(self.root, tracked, untracked):
            self.flash = f'reverted {plural(n, "file")}'
        else:
            self.flash = f'revert failed: {last_error()}'
        self.refresh()

    def _pending_prompt(self) -> str:
        tracked, untracked = self.pending_revert
        what = plural(len(tracked) + len(untracked), 'file')
        deleted = ''
        if untracked:
            deleted = f', {plural(len(untracked), "new file")} will be deleted for good'
        return f' revert {what}{deleted}?   y — yes   any other key — no'


def main(args: list[str]) -> dict:
    mark_overlay('review')
    cwd = os.getcwd()
    return run_screen(ReviewHandler(args, cwd, git_root(cwd)))


@result_handler()
def handle_result(args: list[str], answer: 'dict | None',
                  target_window_id: int, boss) -> None:
    restore_layout(boss, target_window_id)
    apply_result(answer, target_window_id, boss)


if __name__ == '__main__':
    main(sys.argv)
