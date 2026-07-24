# Тесты

[English](README.md) · [Русский](README.ru.md)

Тесты на `unittest` из стандартной библиотеки — внешних зависимостей нет (как и у самих китов).
Гоняются вне kitty: `kittymock.py` подменяет пакеты `kittens.*`/`kitty.*` заглушками и добавляет
`plugins/` в `sys.path`, поэтому `review`/`session`/`log` и `modules.*` импортируются напрямую.
Общий каркас кита — `modules.handler.OverlayHandler` (жизненный цикл и стек миксинов),
общий код vcs-китов — в пакете `modules.vcs`: рендер диффа/дерева (`diff`), строковые утилиты (`util`),
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
| `test_text.py` | `modules.text`: обрезка/паддинг, `short_path`, множественное число, перенос по словам и общий движок `wrap_words` |
| `test_keylayout.py` | `modules.keylayout`: ЙЦУКЕН→QWERTY, сочетания с модификаторами независимо от раскладки, ctrl-буква из C0-байта и её отключение внутри вставки |
| `test_vcs_util.py` / `test_sessions_util.py` | `compose`, `is_noise`, таблица статусов, `human_age` |
| `test_vcs_git.py` | общий git-слой `modules.vcs.git` на **настоящем временном репозитории**: запуск git, `last_error`, `has_head`, `read_text`, потоковый `git_lines` с обрывом |
| `test_review_git.py` | git-слой review на **настоящем временном репозитории**: незакоммиченные правки, untracked, rename, numstat |
| `test_review_diff.py` | ядро `modules.vcs.diff`: подсветка (`_fg_map`), word-diff, `unified_rows` (модификация, гэпы, expand, one-column, скоупы), дерево, отрисовка ячейки (`render_diff_cell`/`render_match`/`is_code_row`) |
| `test_highlight.py` | подсветка синтаксиса `modules.highlight`: vendored Pygments, цвета токенов по ролям (ключевые слова, строки, комментарии, классы), многострочные docstring, пропуск огромных файлов, `fit_fgs`, кэш цветов по сторонам диффа |
| `test_log_git.py` | git-слой log на **настоящем временном репозитории**: `load_commits` (ветка/`--all`/limit/skip, merge, refs/`parse_refs`), `commit_files` (корневой коммит через пустое дерево), `commit_contents` |
| `test_log_graph.py` | движок графа веток `modules.log.graph.build_graph`: линейная история, ветка+мерж (глифы/лейны), цвета лейнов, выравнивание ширины |
| `test_sessions_data.py` | реестр `modules.session.data`: проекты и сессии на диске, живые pid, метаданные, `append_custom_title` (на временных каталогах) |
| `test_sessions_conversation.py` | парсер `modules.session.conversation`: jsonl сессии → лента записей (реплики, вызовы инструментов, вывод, правки файлов, отклонённые вопросы) |
| `test_review_handler.py` | `ReviewHandler`: дерево, навигация, фильтр, фокус/курсор, гэпы, поиск, аннотации, `_editor_command` |
| `test_log_handler.py` | `CommitLogHandler`: список коммитов, фильтр, режим ветка/`--all`, открытие коммита, дифф, копирование, мышь |
| `test_sessions_transcript.py` | `modules.session.transcript`: метки инструментов, вывод `⎿`, diff правок, планы, сворачивание, ширина |
| `test_sessions_markdown.py` | `modules.session.markdown`: инлайн-стили, заголовки, списки, fenced-код, перенос |
| `test_sessions_handler.py` | `SessionsHandler`: проекты/сессии/предпросмотр, фильтр, переименование, resume, навигация, мышь |
| `test_review_grep.py` | `git grep`-слой Find in Files на **настоящем временном репозитории**: smart-case, regex-режим и его ошибки, untracked/ignored/бинарные файлы, потолок совпадений |
| `test_review_find.py` | режим Find in Files в review: вход/выход с восстановлением состояния, живой запрос с дебаунсом, дерево со счётчиками совпадений, навигация по совпадениям, переключение regex, read-only-ограждения, открытие в редакторе |
| `test_result_handlers.py` | `handle_result` китов — построение команды remote-control (сторона процесса kitty) |
| `test_overlay.py` | `modules.overlay.mark_overlay`: escape-последовательность OSC 1337 `SetUserVar` с именем плагина в base64 |
| `test_pointer.py` | `modules.pointer`: escape-последовательности OSC 22 — push формы указателя мыши на стек и pop обратно |
| `test_theme.py` | цветовые темы: формат `palette/*.conf` и наследование ролей, откат роли, которой нет и в дефолтной палитре, truecolor-значения Darcula против схемы JetBrains, разбор `FAMILIAR_THEME` |
| `test_update.py` | проверка обновлений: суточный интервал, кэш и его перезапись из фонового потока, согласие CLI и китов по URL тегов и разбору версии |
| `test_navdef.py` | резолвер go-to-definition `modules.vcs.navdef`: символ под курсором, паттерны объявлений по языкам, ранжирование кандидатов, предпочтение своего файла |
| `test_navdef_imports.py` | резолв импортов на **настоящем временном репозитории**: Python, JS/TS, PHP (PSR-4), Go (go.mod), относительные пути |
| `test_review_goto.py` | go-to-definition в ките: пикер кандидатов, read-only-просмотр внешнего файла, возврат по ⌃o, ошибка git вместо ложного «нет определения» |
| `test_confirm.py` | диалог подтверждения выхода: фокус кнопок, клавиши и клики, ⌃c поверх диалога |
| `test_inputline.py` | строка ввода `modules.inputline`: каретка, перенос многострочного текста, ⌃w/⌃u |
| `test_familiar_cli.py` | CLI `bin/familiar`: `--version` против тега формулы, рендер генерируемого конфига (include'ы, темы, unmap'ы), managed-блок (insert/upsert/remove), флаги выбора для `enable`, скан тем и наличие look-файла у каждой темы |

Интерактивная отрисовка в реальном kitty тестами не покрывается (её нельзя запустить вне kitty);
`styled` в моке — тождество, поэтому вывод хендлеров детерминирован и проверяется по подстрокам.
