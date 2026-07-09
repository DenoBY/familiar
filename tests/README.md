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
| `test_review_git.py` | review's git layer against a **real temporary repository**: working/staged/branch, untracked, rename, numstat, `detect_base` |
| `test_review_diff.py` | core of `modules.vcs.diff`: highlighting (`_fg_map`), word-diff, `unified_rows` (modification, gaps, expand, one-column, scopes), tree, cell rendering (`render_diff_cell`/`render_match`/`is_code_row`) |
| `test_log_git.py` | log's git layer against a **real temporary repository**: `load_commits` (branch/`--all`/limit/skip, merge, refs/`parse_refs`), `commit_files` (root commit via the empty tree), `commit_contents` |
| `test_log_graph.py` | the branch graph engine `modules.log.graph.build_graph`: linear history, branch+merge (glyphs/lanes), lane colors, width alignment |
| `test_sessions_data.py` | parsing of sessions/projects, registry of live pids, `append_custom_title` (on temporary directories) |
| `test_review_handler.py` | `ReviewHandler`: tree, navigation, scope, filter, focus/cursor, gaps, search, comments, `_editor_command` |
| `test_log_handler.py` | `CommitLogHandler`: commit list, filter, branch/`--all` mode, opening a commit, diff, copy, mouse |
| `test_sessions_transcript.py` | `modules.session.transcript`: tool labels, `⎿` output, edit diffs, plans, folding, widths |
| `test_sessions_markdown.py` | `modules.session.markdown`: inline styles, headings, lists, fenced code, wrapping |
| `test_sessions_handler.py` | `SessionsHandler`: projects/sessions/preview, filter, rename, resume, navigation, mouse |
| `test_result_handlers.py` | `handle_result` of both kittens — building the remote-control command (the kitty-process side) |

Interactive rendering in real kitty is not covered by tests (it cannot be run outside kitty);
`styled` in the mock is the identity function, so handler output is deterministic and is checked against substrings.
