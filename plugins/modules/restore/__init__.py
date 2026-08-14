"""restore — снимок состояния kitty и восстановление окон.

Пакет живёт в процессе kitty: его дёргает watcher
(plugins/watchers/restore.py) на таймере, при закрытии окна и при
выходе. Разбиение: store (каталог снимков), scrollback (подготовка
текста экрана), command (что запускать в окне), sessionfile (правка
session-файла), snapshot (сборка снимка из состояния kitty).
"""
