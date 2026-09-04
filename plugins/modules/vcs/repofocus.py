"""Фокус на одном репозитории (клавиша R) — общее для review и log.

В папке над несколькими репозиториями иногда нужен ровно один из них:
review схлопывается до его дерева, log — до его коммитов (и там
возвращается граф веток, у которого поверх репозиториев смысла нет).

Меню выбора рисуется поверх экрана, как пикер определений: пока оно
открыто, прочие клавиши глотаются.
"""

import string

from kittens.tui.operations import styled

from ..keylayout import to_latin
from ..text import plural, truncate
from .workspace import MAX_REPOS


# Метки пунктов меню: цифр на все репозитории не хватает (их бывает до
# MAX_REPOS), дальше идут буквы.
_LABELS = ('123456789' + string.ascii_lowercase)[:MAX_REPOS]


class RepoFocusMixin:

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.repo_focus: 'str | None' = None
        self._repo_menu: 'list | None' = None

    # --- хуки хоста ---

    def _repo_focus_changed(self) -> None:
        """Пересобрать то, что показывает хост (дерево или ленту)."""
        raise NotImplementedError

    def _repo_summary(self, repo) -> str:
        """Правая часть строки меню: чем этот репозиторий занят."""
        return ''

    # --- состояние ---

    def active_repos(self) -> list:
        """Репозитории, которые сейчас в игре: все или один в фокусе."""
        if not self.repo_focus:
            return self.ws.repos
        return [r for r in self.ws.repos if r.root == self.repo_focus]

    def shown_repos(self) -> list:
        """Репозитории того, что показано: ревью коммита открыто над
        одним репозиторием, даже когда сам кит запущен над папкой, и
        искать по соседям в нём нечего.
        """
        ws = self.source_ws()
        return self.active_repos() if ws is self.ws else ws.repos

    def tree_repos(self) -> 'list | None':
        """В фокусе уровень репозиториев лишний: вид схлопывается до
        одного репозитория, как будто он единственный.
        """
        return None if self.repo_focus else super().tree_repos()

    def focus_name(self) -> str:
        return self.ws.name_of(self.repo_focus) if self.repo_focus else ''

    def set_repo_focus(self, root: 'str | None') -> None:
        self._repo_menu = None
        if root == self.repo_focus:
            self.draw_screen()
            return
        self.repo_focus = root
        self.flash = f'{self.ws.name_of(root)} only' if root else 'all repositories'
        self._repo_focus_changed()

    def clear_repo_focus(self) -> bool:
        """Ступень каскада Esc: снять фокус, если он есть."""
        if not self.repo_focus:
            return False
        self.set_repo_focus(None)
        return True

    def note_truncation(self) -> None:
        if self.ws.truncated:
            self.flash = f'showing first {len(self.ws.repos)} repositories'

    # --- меню ---

    def open_repo_menu(self) -> None:
        if not self.ws.multi:
            return
        self._repo_menu = self.ws.repos[:len(_LABELS)]
        self.draw_screen()

    def close_repo_menu(self) -> None:
        self._repo_menu = None
        self.draw_screen()

    def repo_menu_text(self, ch: str) -> bool:
        """True — символ съеден меню."""
        if self._repo_menu is None:
            return False
        if ch == '0':
            self.set_repo_focus(None)
            return True
        i = _LABELS.find(to_latin(ch).lower()) if ch else -1
        if 0 <= i < len(self._repo_menu):
            self.set_repo_focus(self._repo_menu[i].root)
        return True

    def draw_repo_menu(self) -> None:
        cols = self.screen_size.cols
        self.cmd.clear_screen()
        self.print(styled(truncate(f' {plural(len(self.ws.repos), "repository", "repositories")}',
                                   cols), fg='green', bold=True))
        self.print(styled('─' * cols, fg='gray'))
        width = max((len(r.name) for r in self._repo_menu), default=0)
        for i, repo in enumerate(self._repo_menu):
            mark = '▎' if repo.root == self.repo_focus else ' '
            row = f' {_LABELS[i]} {mark} {repo.name:<{width}}   {self._repo_summary(repo)}'
            self.print(truncate(row.rstrip(), cols))
        mark = '▎' if not self.repo_focus else ' '
        self.print(f' 0 {mark} all')
        self.print('')
        self.print(styled(truncate(f' {self._menu_keys()} focus · 0 all · Esc cancel',
                                   cols), fg='gray'), end='')

    def _menu_keys(self) -> str:
        n = len(self._repo_menu)
        if n <= 1:
            return _LABELS[0]
        if n <= 9:
            return f'{_LABELS[0]}-{_LABELS[n - 1]}'
        return f'1-9 {_LABELS[9]}-{_LABELS[n - 1]}'
