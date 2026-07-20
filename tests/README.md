# Tests

[English](README.md) · [Русский](README.ru.md)

Tests use `unittest` from the standard library — no external dependencies (just like the kittens themselves).
They run outside kitty: `kittymock.py` replaces the `kittens.*`/`kitty.*` packages with stubs and adds
`plugins/` to `sys.path`, so `review`/`session`/`log` and `modules.*` are imported directly.
Shared code lives in the `modules.vcs` package: diff/tree rendering (`diff`), string utilities (`util`),
git primitives (`git`), and the base two-panel TUI class `DiffTreeView` (`view`), from which
both review and log inherit — all navigation/scroll/search/copy live there.

## Running

From the repository root — the whole suite:

```sh
python3 -m unittest discover -s tests -t tests
```

A single module or a single test:

```sh
cd tests
python3 -m unittest test_review_diff
python3 -m unittest test_review_handler.ReviewHandlerTest.test_expand_gap
```

## What is covered

| File | What it checks |
|------|----------------|
| `test_review_util.py` / `test_sessions_util.py` | truncation/padding of strings, keyboard layout, `human_age`, wrapping, `is_noise`, `compose` |
| `test_review_git.py` | review's git layer against a **real temporary repository**: uncommitted changes, untracked, rename, numstat |
| `test_review_diff.py` | core of `modules.vcs.diff`: highlighting (`_fg_map`), word-diff, `unified_rows` (modification, gaps, expand, one-column, scopes), tree, cell rendering (`render_diff_cell`/`render_match`/`is_code_row`) |
| `test_highlight.py` | syntax highlighting in `modules.highlight`: the vendored Pygments, token colors by role (keywords, strings, comments, classes), multi-line docstrings, the huge-file skip, `fit_fgs`, per-side color caching for diffs |
| `test_log_git.py` | log's git layer against a **real temporary repository**: `load_commits` (branch/`--all`/limit/skip, merge, refs/`parse_refs`), `commit_files` (root commit via the empty tree), `commit_contents` |
| `test_log_graph.py` | the branch graph engine `modules.log.graph.build_graph`: linear history, branch+merge (glyphs/lanes), lane colors, width alignment |
| `test_sessions_data.py` | parsing of sessions/projects, registry of live pids, `append_custom_title` (on temporary directories) |
| `test_review_handler.py` | `ReviewHandler`: tree, navigation, filter, focus/cursor, gaps, search, comments, `_editor_command` |
| `test_log_handler.py` | `CommitLogHandler`: commit list, filter, branch/`--all` mode, opening a commit, diff, copy, mouse |
| `test_sessions_transcript.py` | `modules.session.transcript`: tool labels, `⎿` output, edit diffs, plans, folding, widths |
| `test_sessions_markdown.py` | `modules.session.markdown`: inline styles, headings, lists, fenced code, wrapping |
| `test_sessions_handler.py` | `SessionsHandler`: projects/sessions/preview, filter, rename, resume, navigation, mouse |
| `test_review_grep.py` | the Find in Files `git grep` layer against a **real temporary repository**: smart-case, regex mode and its errors, untracked/ignored/binary files, the match cap |
| `test_review_find.py` | review's Find in Files mode: enter/exit with state restore, live query with debounce, tree with match counts, match navigation, regex toggle, read-only guards, open in editor |
| `test_result_handlers.py` | `handle_result` of the kittens — building the remote-control command (the kitty-process side) |
| `test_overlay.py` | `modules.overlay.mark_overlay`: the OSC 1337 `SetUserVar` escape with the base64-encoded plugin name |
| `test_pointer.py` | `modules.pointer`: the OSC 22 escapes that push a mouse pointer shape onto the stack and pop it back |
| `test_theme.py` | color themes: the `palette/*.conf` format and role inheritance, Darcula's truecolor values against the JetBrains scheme, `FAMILIAR_THEME` parsing and the fallback to default |
| `test_familiar_cli.py` | the `bin/familiar` CLI: `--version` against the formula's tag, rendering the generated config (includes, themes, unmaps), the managed block (insert/upsert/remove), `enable` selection flags, theme discovery and every theme has a look file |

Interactive rendering in real kitty is not covered by tests (it cannot be run outside kitty);
`styled` in the mock is the identity function, so handler output is deterministic and is checked against substrings.
