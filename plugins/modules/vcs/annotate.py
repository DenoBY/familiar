"""Комментарии к строкам диффа: ввод, маркеры ● на строках, сборка
markdown и выгрузка — в буфер обмена или в окно под оверлеем.

Миксин к DiffTreeView: работает с показанным диффом, а не с git,
поэтому одинаково годится и для незакоммиченных правок, и для
изменений коммита. Заголовок выгрузки подставляет хост
(`_annot_title`) — по нему видно, что именно отревьюено.
"""

from ..text import plural


class AnnotationsMixin:

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # (репозиторий, rel, line) → {'code', 'text'}: в мультирепо
        # одноимённые файлы соседей иначе делили бы комментарии
        self.annots: 'dict[tuple[str | None, str, int], dict[str, str]]' = {}
        # (репозиторий, rel, line, code) редактируемой аннотации
        self.comment_target: 'tuple[str | None, str, int, str] | None' = None

    # --- хуки хоста ---

    def _annot_title(self) -> str:
        """Первая строка выгрузки: по ней видно, что отревьюено."""
        return '# Review comments'

    def _ro_block(self) -> bool:
        """True — правки сейчас запрещены (хост уже сказал почему)."""
        return False

    # --- маркеры на строках диффа ---

    def _diff_annotated(self, di: int, cur_rel: 'str | None') -> bool:
        line = self.diff_lineno[di] if di < len(self.diff_lineno) else 0
        return (cur_rel is not None and (self.view_repo, cur_rel, line) in self.annots
                and self._commentable(di))

    def jump_annot(self, direction: int) -> None:
        """Прыжок курсора между строками с аннотациями (●) в текущем
        файле, по кругу.
        """
        cur = self.current_item()
        if not cur or not self.annots:
            return
        key = (cur.get('repo'), cur['path'])
        marked = [di for di in range(len(self.diff_lineno))
                  if self._commentable(di)
                  and (*key, self.diff_lineno[di]) in self.annots]
        if not marked:
            return
        self.focus = 'diff'
        if direction > 0:
            nxt = next((d for d in marked if d > self.diff_cur), marked[0])
        else:
            nxt = next((d for d in reversed(marked) if d < self.diff_cur), marked[-1])
        self.diff_cur = nxt
        self.reveal_cursor()
        self.draw_screen()

    # --- ввод комментария ---

    def start_comment(self) -> None:
        if self._ro_block():
            return
        if self.focus != 'diff' or not self._commentable(self.diff_cur):
            self.flash = 'Tab → diff, hover a line, then c'
            self.draw_screen()
            return
        cur = self.current_item()
        if not cur:
            return
        line = self.diff_lineno[self.diff_cur]
        after_lines = self.diff_after.splitlines()
        code = after_lines[line - 1] if 0 < line <= len(after_lines) else ''
        self.comment_target = (cur.get('repo'), cur['path'], line, code)
        existing = self.annots.get((cur.get('repo'), cur['path'], line))
        self.start_input('comment', existing['text'] if existing else '')

    def _save_comment(self) -> None:
        repo, rel, line, code = self.comment_target
        text = self.input_buffer.strip()
        key = (repo, rel, line)
        if text:
            self.annots[key] = {'code': code, 'text': text}
        else:
            self.annots.pop(key, None)   # пустой комментарий = удалить
        self.comment_target = None

    # --- выгрузка ---

    def _review_markdown(self) -> 'str | None':
        if not self.annots:
            self.flash = 'no comments — Tab→diff, hover a line, c'
            self.draw_screen()
            return None
        by_file = {}
        for (repo, rel, line), v in self.annots.items():
            # путь от папки, из которой открыт кит: по нему Claude Code
            # и откроет файл
            by_file.setdefault(self._copy_rel(rel, repo), []).append((line, v))
        out = [self._annot_title(), '']
        for rel in sorted(by_file):
            out.append(f'## {rel}')
            for line, v in sorted(by_file[rel]):
                code = v['code'].strip()
                out.append(f'- **L{line}** `{code}`' if code else f'- **L{line}**')
                out += [f'  {ln}' if ln else '' for ln in v['text'].split('\n')]
            out.append('')
        return '\n'.join(out)

    def export_review(self) -> None:
        md = self._review_markdown()
        if md is None:
            return
        self._copy_clipboard(md)
        n = len(self.annots)
        # выгруженное ревью живёт дальше в буфере обмена; держать его
        # ещё и на строках диффа незачем — маркеры ● только мешают
        # следующему проходу
        self.annots = {}
        self.flash = f'copied {plural(n, "comment")} to clipboard — cleared'
        self.draw_screen()

    def send_review(self) -> None:
        """Выйти и вставить комментарии в окно под оверлеем — обычно
        там ждёт claude (вставку делает handle_result: Boss есть только
        в процессе kitty).
        """
        md = self._review_markdown()
        if md is None:
            return
        self.action = {'action': 'send', 'text': md}
        self.quit_loop(0)

    def clear_annotations(self) -> None:
        if not self.annots:
            self.flash = 'no comments'
        else:
            self.flash = f'cleared {plural(len(self.annots), "comment")}'
            self.annots = {}
        self.draw_screen()
