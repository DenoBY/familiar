# Тесты

[English](README.md) · [Русский](README.ru.md)

Тесты на `unittest` из стандартной библиотеки — внешних зависимостей нет (как и у самих китов).
Гоняются вне kitty: `kittymock.py` подменяет пакеты `kittens.*`/`kitty.*` заглушками и добавляет
`plugins/` в `sys.path`, поэтому `review`/`session`/`log` и `modules.*` импортируются напрямую.
Общий код в пакете `modules.vcs`: рендер диффа/дерева (`diff`), строковые утилиты (`util`),
git-примитивы (`git`) и базовый двухпанельный TUI-класс `DiffTreeView` (`view`), от которого
наследуются и review, и log — вся навигация/скролл/поиск/копирование там.

## Запуск

Из корня репозитория — весь набор:

```sh
python3 -m unittest discover -s tests -t tests
```

Один модуль или один тест:

```sh
cd tests
python3 -m unittest test_review_diff
python3 -m unittest test_review_handler.ReviewHandlerTest.test_expand_gap
```

## Что покрыто

| Файл | Что проверяет |
|------|----------------|
| `test_review_util.py` / `test_sessions_util.py` | обрезка/паддинг строк, раскладка, `human_age`, перенос, `is_noise`, `compose` |
| `test_review_git.py` | git-слой review на **настоящем временном репозитории**: незакоммиченные правки, untracked, rename, numstat |
| `test_review_diff.py` | ядро `modules.vcs.diff`: подсветка (`_fg_map`), word-diff, `unified_rows` (модификация, гэпы, expand, one-column, скоупы), дерево, отрисовка ячейки (`render_diff_cell`/`render_match`/`is_code_row`) |
| `test_log_git.py` | git-слой log на **настоящем временном репозитории**: `load_commits` (ветка/`--all`/limit/skip, merge, refs/`parse_refs`), `commit_files` (корневой коммит через пустое дерево), `commit_contents` |
| `test_log_graph.py` | движок графа веток `modules.log.graph.build_graph`: линейная история, ветка+мерж (глифы/лейны), цвета лейнов, выравнивание ширины |
| `test_sessions_data.py` | парсинг сессий/проектов, реестр живых pid, `append_custom_title` (на временных каталогах) |
| `test_review_handler.py` | `ReviewHandler`: дерево, навигация, фильтр, фокус/курсор, гэпы, поиск, аннотации, `_editor_command` |
| `test_log_handler.py` | `CommitLogHandler`: список коммитов, фильтр, режим ветка/`--all`, открытие коммита, дифф, копирование, мышь |
| `test_sessions_transcript.py` | `modules.session.transcript`: метки инструментов, вывод `⎿`, diff правок, планы, сворачивание, ширина |
| `test_sessions_markdown.py` | `modules.session.markdown`: инлайн-стили, заголовки, списки, fenced-код, перенос |
| `test_sessions_handler.py` | `SessionsHandler`: проекты/сессии/предпросмотр, фильтр, переименование, resume, навигация, мышь |
| `test_result_handlers.py` | `handle_result` обоих китов — построение команды remote-control (сторона процесса kitty) |

Интерактивная отрисовка в реальном kitty тестами не покрывается (её нельзя запустить вне kitty);
`styled` в моке — тождество, поэтому вывод хендлеров детерминирован и проверяется по подстрокам.
