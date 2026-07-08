"""Выбор редактора для «открыть файл на строке» из review-кита.

Чистая детекция без TUI: нужна обоим процессам — kitten (GUI-редактор запускаем
не закрывая оверлей) и kitty (терминальный редактор открывается в новом табе
через handle_result).
"""

import os
import shlex
import shutil


# GUI-редакторы открываем без окна kitty (у них своя оконная система),
# терминальные — в новом табе kitty.
_GUI_EDITORS = {'code', 'code-insiders', 'codium', 'cursor', 'windsurf', 'subl', 'zed'}

# JetBrains: shell-лаунчеры (если стоит command-line launcher) и .app в /Applications.
_JETBRAINS_CLI = ('phpstorm', 'idea', 'pycharm', 'webstorm', 'goland', 'rubymine',
                  'clion', 'rider', 'datagrip', 'idea-ce', 'pycharm-ce')
_JETBRAINS_APPS = ('PhpStorm', 'IntelliJ IDEA', 'IntelliJ IDEA CE', 'PyCharm',
                   'PyCharm CE', 'WebStorm', 'GoLand', 'RubyMine', 'CLion', 'Rider',
                   'DataGrip')


def editor_command(project: str, path: str, line: int) -> 'tuple[list[str], bool]':
    """(argv, gui) — чем открыть файл на строке. Приоритет: IDE по конфигам проекта
    (открываем со всем проектом), иначе $VISUAL/$EDITOR, иначе vim.
    """
    j = os.path.join
    # 1) JetBrains — по .idea/ (открывает файл в проекте, к которому он принадлежит)
    if os.path.isdir(j(project, '.idea')):
        for launcher in _JETBRAINS_CLI:
            if shutil.which(launcher):
                return ([launcher, '--line', str(line), path], True)
        for app in _JETBRAINS_APPS:
            ap = f'/Applications/{app}.app'
            if os.path.isdir(ap):
                return (['open', '-na', ap, '--args', '--line', str(line), path], True)
    # 2) VS Code / Cursor — по .vscode/ или .cursor/ (открываем папку проекта + файл:строка)
    if os.path.isdir(j(project, '.vscode')) or os.path.isdir(j(project, '.cursor')):
        cursor = os.path.isdir(j(project, '.cursor'))
        clis = ('cursor',) if cursor else ('code', 'codium', 'code-insiders')
        for launcher in clis:
            if shutil.which(launcher):
                return ([launcher, project, '-g', f'{path}:{line}'], True)
        app = '/Applications/Cursor.app' if cursor else '/Applications/Visual Studio Code.app'
        if os.path.isdir(app):
            return (['open', '-na', app, '--args', project, '-g', f'{path}:{line}'], True)
    # 3) Zed — по .zed/
    if os.path.isdir(j(project, '.zed')) and shutil.which('zed'):
        return (['zed', f'{path}:{line}'], True)
    # 4) $VISUAL / $EDITOR (или vim)
    parts = shlex.split(os.environ.get('VISUAL') or os.environ.get('EDITOR') or 'vim')
    prog = os.path.basename(parts[0]) if parts else 'vim'
    if prog in _GUI_EDITORS:
        target = f'{path}:{line}'
        tail = [target] if prog in ('subl', 'zed') else ['-g', target]
        return (parts + tail, True)
    return (parts + [f'+{line}', path], False)
