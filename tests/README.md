# Tests

[English](README.md) · [Русский](README.ru.md)

Tests use `unittest` from the standard library — no external dependencies (just like the kittens themselves).
They run outside kitty: `kittymock.py` replaces the `kittens.*`/`kitty.*` packages with stubs and adds
`plugins/` to `sys.path`, so `review`/`session`/`log` and `modules.*` are imported directly.
The shared kitten skeleton is `modules.handler.OverlayHandler` (lifecycle and mixin stack);
shared vcs-kitten code lives in the `modules.vcs` package: diff/tree rendering (`diff`), string utilities (`util`),
git primitives (`git`), the sources of changes (`worktree`, `commit`, `source`), the base two-panel TUI class
`DiffTreeView` (`view`) with all navigation/scroll/search/copy, and the full review screen `ReviewScreen`
(`screen` plus `annotate`, `goto`, `find`) — review and log differ only in the source they feed it.

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
| `test_text.py` | `modules.text`: truncation/padding, `short_path`, pluralization, word wrapping and the shared `wrap_words` engine |
| `test_keylayout.py` | `modules.keylayout`: ЙЦУКЕН→QWERTY, modifier chords independent of layout, ctrl-letter from a C0 byte and its opt-out inside a paste |
| `test_vcs_util.py` / `test_sessions_util.py` | `compose`, `is_noise`, the status table, `human_age` |
| `test_vcs_git.py` | the shared git layer `modules.vcs.git` against a **real temporary repository**: running git, `last_error`, `has_head`, `read_text`, streaming `git_lines` with early stop |
| `test_vcs_worktree.py` | the working-tree source against a **real temporary repository**: uncommitted changes, untracked, rename, numstat |
| `test_review_diff.py` | core of `modules.vcs.diff`: highlighting (`_fg_map`), word-diff, `unified_rows` (modification, gaps, expand, one-column, scopes), tree, cell rendering (`render_diff_cell`/`render_match`/`is_code_row`) |
| `test_highlight.py` | syntax highlighting in `modules.highlight`: the vendored Pygments, token colors by role (keywords, strings, comments, classes), multi-line docstrings, the huge-file skip, `fit_fgs`, per-side color caching for diffs |
| `test_log_git.py` | log's git layer against a **real temporary repository**: `load_commits` (branch/`--all`/limit/skip, merge, refs/`parse_refs`), `commit_files` (root commit via the empty tree), `commit_contents` |
| `test_log_graph.py` | the branch graph engine `modules.log.graph.build_graph`: linear history, branch+merge (glyphs/lanes), lane colors, width alignment |
| `test_sessions_data.py` | the `modules.session.data` registry: projects and sessions on disk, live pids, metadata, `append_custom_title` (on temporary directories) |
| `test_sessions_conversation.py` | the `modules.session.conversation` parser: a session jsonl into a feed of entries (messages, tool calls, output, file edits, rejected questions) |
| `test_review_handler.py` | `ReviewHandler`: tree, navigation, filter, focus/cursor, gaps, search, comments, `_editor_command` |
| `test_log_handler.py` | `CommitLogHandler`: commit list, filter, branch/`--all` mode, opening a commit, diff, copy, mouse |
| `test_log_review.py` | reviewing a commit in log: the commit snapshot as the source, line comments naming the commit, go-to-definition and Find in Files against that snapshot, review footer, no stage/revert |
| `test_vcs_view.py` | vertical geometry of the diff pane: the sticky scope header shortens the pane, so the cursor stays on screen after `[`/`]`, arrows and scrolling |
| `test_sessions_transcript.py` | `modules.session.transcript`: tool labels, `⎿` output, edit diffs, plans, folding, widths |
| `test_sessions_markdown.py` | `modules.session.markdown`: inline styles, headings, lists, fenced code, wrapping |
| `test_sessions_handler.py` | `SessionsHandler`: projects/sessions/preview, filter, rename, resume, navigation, mouse |
| `test_vcs_grep.py` | the Find in Files `git grep` layer against a **real temporary repository**: smart-case, regex mode and its errors, untracked/ignored/binary files, the match cap |
| `test_review_find.py` | review's Find in Files mode: enter/exit with state restore, live query with debounce, tree with match counts, match navigation, regex toggle, read-only guards, open in editor |
| `test_result_handlers.py` | `handle_result` of the kittens — building the remote-control command (the kitty-process side) |
| `test_overlay.py` | `modules.overlay.mark_overlay`: the OSC 1337 `SetUserVar` escape with the base64-encoded plugin name |
| `test_pointer.py` | `modules.pointer`: the OSC 22 escapes that push a mouse pointer shape onto the stack and pop it back |
| `test_theme.py` | color themes: the `palette/*.conf` format and role inheritance, the fallback for a role missing even from the default palette, Darcula's truecolor values against the JetBrains scheme, `FAMILIAR_THEME` parsing |
| `test_update.py` | update checks: the daily interval, the cache and its rewrite from the background thread, CLI and kittens agreeing on the tags URL and version parsing |
| `test_navdef.py` | the go-to-definition resolver `modules.vcs.navdef`: symbol under the cursor, per-language declaration patterns, candidate ranking, preferring the current file |
| `test_navdef_imports.py` | import resolution against a **real temporary repository**: Python, JS/TS, PHP (PSR-4), Go (go.mod), relative paths |
| `test_review_goto.py` | go-to-definition in the kitten: the candidate picker, read-only view of an external file, going back with ⌃o, a git failure instead of a false "no definition" |
| `test_confirm.py` | the quit confirmation dialog: button focus, keys and clicks, ⌃c over the dialog |
| `test_close_target.py` / `test_close_screen.py` / `test_close_handler.py` / `test_close_pane.py` | the `Cmd+W` question: naming the session or the program, the screen and its buttons, deciding whether to ask, and what closes after `Yes` — a session takes the shell under its overlay along |
| `test_inputline.py` | the `modules.inputline` input line: caret, multi-line wrapping, ⌃w/⌃u |
| `test_familiar_cli.py` | the `bin/familiar` CLI: `--version` against the formula's tag, rendering the generated config (includes, themes, unmaps), the managed block (insert/upsert/remove), `enable` selection flags, theme discovery and every theme has a look file |

Interactive rendering in real kitty is not covered by tests (it cannot be run outside kitty);
`styled` in the mock is the identity function, so handler output is deterministic and is checked against substrings.
