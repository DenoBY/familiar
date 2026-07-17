# Changelog

[English](CHANGELOG.md) · [Русский](CHANGELOG.ru.md)

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [SemVer](https://semver.org/).

## [0.22.0] — 2026-07-17

### Changed

- review: files staged as new and then deleted from disk (git status `AD`)
  no longer appear in the file tree — relative to HEAD they are not a
  change. Real deletions of tracked files are still shown.
- CLI: `familiar status` prints `latest: up to date` instead of repeating
  the version number in brackets; the number appears only when an update
  is available (`latest: X.Y.Z — brew upgrade familiar`).

## [0.21.0] — 2026-07-16

### Changed

- review: `Shift+click` / `Shift+↑↓` in the file tree now work like classic
  GUI lists — the range is painted from a fixed anchor (the focused file
  where multi-select started), and re-extending back toward the anchor
  unmarks the rows that left the range. `Shift+↑/↓` skips rows that add
  nothing to the range (expanded folders) — no empty steps. Unmarking a
  single file or folder is `⌥+click`.
- review: while multi-select is active, the selection highlight belongs to
  the marks only — the cursor row without a mark is drawn as a regular row,
  so `⌥+click` visibly deselects the current file. The first `⌥+click` takes
  the active file into the selection too (like `⌘+click` in GUI lists), so
  you get two selected files, not a moved highlight. The tree selection never
  goes empty: `⌥+click` on the last marked row keeps it marked (drop it with
  plain navigation or Esc), and mark clicks never switch the viewed diff.

### Fixed

- review: `⌥+click` on a tree row no longer moves the cursor or steals focus
  from the diff — it only toggles the mark, so the currently viewed file
  stays selected.

## [0.20.0] — 2026-07-16

### Added

- review: **multi-select in the file tree.** `Shift+↑/↓` paints a range of
  files from the cursor; `Shift+click` extends the selection up to the clicked
  row (like GUI file lists — the file under the cursor stays selected);
  `⌥+click` toggles a single file or a whole folder, and `Shift+click` on an
  already-marked row unmarks it. A range marks exactly
  what you see: an expanded folder contributes nothing (its files are their
  own rows), a collapsed one contributes all its files and lights up itself.
  Marked rows look like the
  regular selection; plain navigation (arrows, click, Home/End, `g`/`G`) or
  Esc drops the marks. `⌘c` copies all marked paths as `@path` mentions, one
  per line — ready to paste into a Claude prompt.
- terminal config: **Shift+click is passed through to the kittens.** kitty
  handles `shift+left` itself even when a program grabs the mouse, so kittens
  never saw it; `config/keys/mouse.conf` unmaps those grabbed-mode bindings.
  Trade-off: inside mouse-grabbing programs (vim with mouse, htop) Shift+drag
  no longer starts kitty's own text selection — the ungrabbed shell is
  unaffected.

## [0.19.0] — 2026-07-15

### Added

- **Send review comments straight into Claude's prompt.** In `review`, `s`
  collects all comments into markdown — like `w` — but instead of the
  clipboard it closes the overlay and pastes the text right into the window
  the overlay was opened over (where Claude is usually waiting). The paste is
  bracketed, so a multi-line review lands as one block without being submitted
  by the newlines. `w` (copy to clipboard) works as before.

### Changed

- **Esc at the top level now asks before closing.** In `log`, `review` and
  `session`, Esc at the bottom of the cascade brings up a centered kitty-style
  confirmation dialog with Yes/No buttons instead of silently doing nothing:
  `y`/`Enter` closes the overlay, `n`/`Esc` keeps it open, ←/→/Tab switch the
  buttons, and the buttons are clickable (pointer cursor on hover). `q` and
  `⌃c` still quit immediately — `⌃c` works even while the dialog is open.

## [0.18.0] — 2026-07-14

### Changed

- **Esc no longer closes kitten overlays.** In `log`, `review` and `session`
  Esc still walks the cascade back (selection → search → filter → screen), but
  at the top level it does nothing instead of quitting — so a stray Esc can't
  dismiss the overlay. Quit with `q` or `⌃c`. As a bonus, Esc at the top level
  of `log` and `review` now clears an applied filter.

## [0.17.0] — 2026-07-14

### Added

- **Update notification.** Once a day a kitten checks GitHub for a newer release
  (in a background thread, cached in `~/.cache/familiar/update.json`) and shows a
  one-time footer hint: `familiar X.Y.Z is out — brew upgrade familiar`.
  `familiar status` now prints the installed version and the latest available one.
  Set `FAMILIAR_UPDATE_CHECK=0` to opt out of both the check and the hint.
  The version number now lives in the `VERSION` file at the repo root — the single
  source for the CLI and the kittens.

## [0.16.0] — 2026-07-14

### Changed

- **Tab bar simplified to `index: project❘topic`.** The 🔔 bell symbol and the
  OSC 9;4 progress percent introduced in 0.15.0 are gone: the percent was never
  rendered (kitty 0.47 treats that escape as a notification), and notifications
  are better handled outside the terminal.

## [0.15.0] — 2026-07-14

### Changed

- **Smarter tab titles for the Claude Code workflow** (`look/tabs.conf`). Titles are
  capped at 30 cells so several sessions fit the bar; a tab running Claude Code
  (detected by the `✳ ` the CLI puts in the window title) shows `project❘topic`
  instead of the raw title, while plain shell tabs are left as is. A red 🔔 marks a
  background tab whose bell rang (Claude's terminal-bell notification), and a task
  progress percent is shown when the program reports one (OSC 9;4).

## [0.14.0] — 2026-07-13

### Changed

- **Documentation moved to the [GitHub wiki](https://github.com/DenoBY/familiar/wiki).**
  The `docs/` folder (per-kitten pages and screenshots) is gone from the repo and the
  Homebrew payload; the README links to the wiki instead. The wiki is bilingual
  (English + Russian pages).

### Fixed

- **`⌃o` (back after a definition jump) works on the Russian layout.** The terminal
  config maps `ctrl+<cyrillic>` to C0 bytes (`send_text`), so on ЙЦУКЕН the chord
  arrives as text, not as a key event — the review viewer now decodes those bytes for
  all its ctrl shortcuts (`⌃o` back, `⌃u`/`⌃d` scroll, `⌃w`/`⌃u` while typing a
  comment).
- **Go to definition is fast on large repos.** The definition search now greps only
  files of the click's language (falling back to a repo-wide pass over source files),
  so heavy untracked logs and minified bundles no longer stall the viewer — on a real
  Laravel repo a jump dropped from ~3 s of frozen UI to under 0.1 s.

### Added

- **Go to definition understands PHP and Ruby syntax.** `$this->method()`,
  `Order::create()`, `?->`, `static::` and Ruby `Foo::Bar` now resolve the receiver:
  `$this`/`static` methods are looked up in the current file first, and a call through
  an imported class (`use App\Models\Order; Order::create()`) jumps straight into that
  class's file. The same receiver resolution now works for Python (`mod.func`) and JS
  (`Api.get`) imports too, and PHP 8.1 `enum` declarations are recognized.
- **Demo stand generator** (`tools/demo_stand.py`): builds a demo git repo with
  photogenic history, branches and uncommitted changes plus a fake Claude Code storage
  with live sessions — a reproducible scene for retaking the wiki screenshots after UI
  changes.

## [0.13.0] — 2026-07-11

### Added

- **Go to definition in the review viewer.** ⌥-click a symbol in the diff — or select a
  word and press `d` — to jump to where it is defined. The jump happens inside the viewer
  with a back stack (`⌃o` to return): a definition in a changed file opens its diff, a
  definition in an unchanged file opens read-only. When a symbol has several definitions a
  picker lists them (`1`–`9` or click to choose). Resolution is context-aware: `obj.name`
  prefers methods, `name(` prefers declarations, and imported names are resolved to the
  exact file via the current file's imports (Python `from a.b import x` / relative `.`/`..`;
  JS/TS `import`/`require` with extension and `index` resolution; PHP `use` via composer
  PSR-4; Go package symbols via `go.mod`). Everything else falls back to a repo-wide
  `git grep`, new untracked files included — no index or language server needed.
  ⌘-click is not possible: the terminal mouse protocol carries only Shift/Alt/Ctrl, never
  Cmd, so the trigger is ⌥-click.
- **Mouse gestures in the review diff.** Double-click a word to select just that word (for
  copy), and click a line number to comment on that line — the `c`/Enter hotkey still works.
  The pointer turns into a hand over line numbers and, while ⌥ is held, over identifiers you
  can jump to. Selecting text now keeps the syntax highlighting and just adds a background.

## [0.12.0] — 2026-07-10

### Added

- **Mouse pointer shapes.** In the diff viewer (review, log) and the session preview the
  pointer now reflects what is under it: a text (I-beam) cursor over code and other
  selectable text, and a hand over the clickable spots — folders in the file tree and the
  collapsed-context gaps in the diff (review, log), foldable entries in the session preview.
  Everywhere else it stays the arrow. Turning on mouse tracking used to force the arrow
  everywhere, hiding where you can select versus click.

### Changed

- **A theme is two data files now, no Python.** It used to take a palette dict in
  `plugins/modules/theme.py` and a `THEMES` tuple in `bin/familiar`. A theme is now
  `config/look/<name>.conf` (terminal colors) plus `config/palette/<name>.conf` (the
  kittens' syntax highlighting, one `role value` per line). Both the `--theme` flag and the
  kittens find themes by scanning `config/palette/`, so there is no list to keep in sync,
  and any role you omit inherits the default `ghostty` color. See `config/README.md`.

### Fixed

- log: a failed `fetch` or `push` could flash the wrong error. The background network call
  and the foreground git commands wrote to one shared error slot, so scrolling the commit
  list while a push ran could overwrite the message before it was shown. The network calls
  now hand their error back directly.

### Internal

- A style pass over the kittens against the project guide: the session preview moved to
  `modules/session/preview.py`, the diff-pane key handling shared by review and log lifted
  into `DiffTreeView`, the pointer logic factored into a `PointerCursor` mixin, type hints
  filled in across the handlers, and the docs resynced with the code.

## [0.11.0] — 2026-07-10

### Added

- log: `p` — `git push` the current branch. It asks first, the way `-` does in review: the
  footer spells out how many commits will travel and where (`push 3 commits to
  origin/main?`), and only `y` confirms — a mistyped key publishes nothing. A branch with no
  upstream is created on `origin` (`push -u`) and bound to it, the way an IDE does. The
  `p push` hint shows only while something is unpushed; the network call runs in the
  background, so the UI never freezes.

## [0.10.0] — 2026-07-10

### Changed

- The default theme is now named `ghostty` after the palette it actually is, not `default`.
  Switch back with `familiar enable --theme ghostty`; the old `--theme default` is gone.
- The Darcula theme also restyles the tab bar and the diff backgrounds inside the kittens.
  Writing your own theme: see `config/README.md`.
- log: the header counter now reads `300+` while the history is not fully loaded. It used to
  print the number of loaded commits with no hint, so `(300)` read as "this branch has 300
  commits".

### Fixed

- log: fast scrolling stuttered — the detail panel called git on every cursor step (`show`
  plus `branch --contains`, which walks every ref in the repository). Details are now
  fetched once the scrolling settles, the way the diff already loads in the file tree;
  until then the panel shows what the commit list already knows.
- The Darcula terminal text was tinted blue: it took the editor's `TEXT` (`#a9b7c6`) where
  the JetBrains scheme has a separate `CONSOLE_NORMAL_OUTPUT` (`#bbbbbb`) for the console.
- Darcula: word-diff highlighting drowned in the line background. The line is darker now and
  the word stays saturated — twice the contrast between them, and text on top reads better.
- review, log: clicking a folder in the tree folded it right away, so a slightly-off click
  rearranged the tree under the cursor. The first click now selects the folder, a second
  click on it folds it — the way the commit list in `log` already behaved.

### Internal

- `ruff` never checked `bin/familiar` (no `.py` extension), where 22 line-length violations
  had piled up. It does now, and `ruff check .` runs in CI.
- A test asserts the CLI's theme list matches the kittens' palettes: were they to drift,
  `--theme` would accept a name and silently paint with the default palette.

## [0.9.0] — 2026-07-10

### Added

- **Color themes** (`familiar enable --theme darcula`): the JetBrains Darcula palette for
  the terminal *and* for the syntax highlighting inside the kittens. Colors are taken from
  the official `Darcula` scheme in JetBrains/intellij-community, so keywords, strings,
  numbers, docstrings and decorators land on the exact hues the IDE uses. Without
  `--terminal`/`--all` only the kittens are recolored — familiar leaves your terminal look
  alone unless asked. `--theme default` keeps the previous appearance, and `familiar status`
  reports the active theme.
- The kittens now render truecolor when a theme calls for it (`kitty.fast_data_types.Color`),
  instead of rounding every hue to the 256-color cube.

## [0.8.0] — 2026-07-10

### Removed

- review: the **staged** and **vs \<branch\>** git scopes, and with them the `s` key.
  The kitten now always shows the uncommitted changes of the working tree (vs `HEAD`),
  which is the only scope that saw any use. `+` still stages files, as before.

### Fixed

- log: the footer offered `a current branch` while you were already on the current
  branch — it now names the mode `a` switches *to*, like the neighbouring hints do.

## [0.7.0] — 2026-07-10

### Added

- review, log: **final code** view (`v`) — the file as an IDE shows it after the merge:
  no `+`/`−` signs, no removed lines, no fill inside the lines. Edits are marked in the
  gutter (`▎` green — added, `▎` blue — modified, `▔` red — code was cut here). Jumps,
  search, comments and copying work as in the diff, and the cursor keeps its line when
  you switch views.
- review, log: a **change map** on the scrollbar right of the diff — colored ticks show
  where the edits are, so a long file tells you at a glance where to scroll.
- **IDE-grade syntax highlighting** via Pygments: functions, types, `self`, decorators,
  docstrings and f-strings, instead of the previous strings/comments/numbers/keywords.
  Pygments ships vendored in `plugins/vendor`, so there is still nothing to install; if
  it is unavailable, the built-in regex lexer takes over.

### Changed

- Flash messages above the footer clear themselves after 2.5 s — the footer hints come
  back without waiting for the next keypress.

### Fixed

- review, log: when a block of lines was replaced by fewer lines, the gutter showed only
  `▎ modified` and the deletion went unmarked; `▔` is now placed where the code was cut.
- Syntax colors no longer shift by one line below a form feed, a lone `\r`, or U+2028 —
  the color map is split on exactly the separators `str.splitlines()` uses.

## [0.6.0] — 2026-07-10

### Added

- review: `-` reverts the file or folder under the cursor back to `HEAD` — both the
  working tree and the index. Untracked files have no version to restore, so they are
  deleted from disk; the confirmation prompt spells that out. Nothing happens until `y`
  is pressed: `Enter`, `Esc` and every other key cancel.

## [0.5.0] — 2026-07-10

### Added

- review: untracked files are gathered into an **Unversioned Files** group at the bottom
  of the tree, collapsed by default — a pile of new files no longer buries the changes
  you opened the review for.
- review: `+` stages what's under the cursor — a file, a whole folder, or every
  untracked file at once (`+` on the group node). The hint only appears when `git add`
  would actually do something; hidden noisy folders are never staged.
- review, log: **scrollbars** in both panes. The wheel now scrolls the pane it's over
  instead of moving the selection (and no longer reloads the diff on every notch);
  the arrows keep moving the selection and pull the view back to the cursor.
- review: **multi-line comments** — `Shift+Enter` inserts a line break, long text wraps
  by words, and the input area grows up to a third of the screen.
- review, log: `Ctrl+W` erases the word before the cursor and `Ctrl+U` erases the whole
  text while typing (readline habits). While the input is open they no longer scroll the
  diff.
- review: `u` reports how many ignored files it showed or hid — useful when they all end
  up inside the collapsed Unversioned Files group.

### Changed

- review, log: `⌘c` and `⌘shift+c` now copy an **@-mention relative to the repository
  root** (`@plugins/review.py`, `@plugins/review.py#L42`, `@plugins/review.py#L42-58` for
  a selected range, `@plugins/` for a folder) instead of an absolute `path:line`. This is
  the form Claude Code expects; it resolves `@path` against the directory it was started
  in.
- review: `w` now clears the comments after copying them to the clipboard — the exported
  review lives in the clipboard, and the `●` markers only got in the way of the next pass.

### Fixed

- review, log: the file counter in the header counted tree *rows*, so files inside
  collapsed folders (and inside the new Unversioned Files group) were missing from it.
  Pressing `u` on a project whose ignored files are all untracked looked like a no-op.

## [0.4.0] — 2026-07-09

### Added

- session: exploration (searching, reading, listing) collapses into a summary line
  ("Searched for 2 patterns, read 1 file, listed 1 directory"), the way Claude Code
  does it; click it or press `Ctrl+o` to expand. Commands, file edits, plans and calls
  whose output is an error always stay visible in full.
- session: a question to the user (`AskUserQuestion`) renders the way Claude Code does —
  "User answered Claude's questions:" with "· question → answer" lines instead of the raw
  tool_result blob; a question dismissed with `Esc` shows "User rejected Claude's
  questions".

### Fixed

- session: the preview now shows only the active branch of the conversation. A session
  file is a tree: a prompt cancelled with `Esc` or edited afterwards stayed in it as a
  dead branch, and the kitten drew every draft in a row.
- session: `Enter` in the preview no longer expands all folded output — while reading a
  transcript it only got in the way; `Ctrl+o` still does it.

## [0.3.0] — 2026-07-09

### Added

- session: the conversation preview is now a Claude Code-style transcript — tool
  calls show their argument (`⏺ Bash(git status)`) and the output follows on `⎿`
  lines. Paths inside the project are shown relative to it. Your own turns are
  marked with `>` on a full-width background, so questions stand out from answers.
- session preview: file edits render like Claude Code — `⏺ Update(tests/x.py)`, a
  `Added N lines, removed M lines` summary and a diff with line numbers, taken
  from the record's `structuredPatch` (the raw "file updated successfully" text
  is replaced); changed lines get the same background the `review` diff uses,
  word-diff highlights what exactly changed inside a line, the diff is never
  folded away, and `Write` reports the number of lines written.
- session preview: Claude's answers render markdown — bold, italic, inline code,
  headings, lists, and fenced code blocks with syntax highlighting (the same
  lexer the diff panes use, now shared as `modules.highlight`).
- session preview: markdown tables render as framed tables (box-drawing rules
  between every row, cells wrapped to fit) instead of collapsing into a
  paragraph of pipes.
- session preview: leaving plan mode renders as `⏺ Updated plan` with the plan
  itself in a frame (markdown, syntax-highlighted code), followed by `⎿ Plan
  approved` / `⎿ Plan rejected`; a file read collapses to `⎿ Read 402 lines`, and
  a subagent run to `⎿ Done (1 tool use · 25.5k tokens · 18s)`.
- session preview: long tool output, plans and file contents are folded and say
  so (`… +121 lines (ctrl+o to expand)`) — click a folded line to expand that
  one entry, or press `Ctrl+o` / `Enter` to expand all folded output at once.
- session preview: select text with the mouse (a span within a line, or whole
  lines across them) and copy it with `⌘c`.
- session preview: `[` / `]` jump to the previous / next user turn — the prompts
  are the table of contents of a long conversation.
- session: background agents (`kind: bg` in the live-process registry) are marked
  with `◆` and a `bg idle` / `bg busy` status. Claude Code refuses to attach to a
  live agent, so `o` / `Enter` on such a session no longer starts a `claude
  --resume` that is bound to fail; it suggests stopping the agent, attaching via
  `claude agents`, or forking the conversation (`f` still works — a live process
  does not block a fork).

### Fixed

- session preview: a failed tool call is no longer indistinguishable from a
  successful one — errors are shown in red, and the output is no longer cut to
  200 characters on a single line.
- session preview: user turns no longer show internal wrappers
  (`<local-command-caveat>`, `<system-reminder>`, command tags) nor background
  task reports (`<task-notification>` — kilobytes of JSON the user never typed).
- session preview: pasted images are no longer shown as a separate turn holding
  a cache path — they hang under their prompt as `⎿ [Image #13]`, like in Claude
  Code.
- review/log: mouse drag selection within a line now includes the character
  under the cursor — the last character is no longer dropped from the copy.
- review/log/session: shortcuts with modifiers (`Ctrl+c`, `Ctrl+d` / `Ctrl+u`,
  `⌘c`, `⌘⇧c`) now work on the Russian keyboard layout. They were matched by the
  literal character, so on ЙЦУКЕН `⌘с` (Cyrillic „с“) did nothing; the layout is
  now mapped in the shared `modules.keylayout.chord`, which also compares the
  full set of modifiers — `⌘c` no longer fires on `⌘⇧c`.

## [0.2.1] — 2026-07-09

### Changed

- `familiar status` reports `wired root:` — the installation baked into
  `familiar.conf`, i.e. the code kitty actually runs — alongside the `app root:`
  of the copy you invoked, and warns when the two differ.

### Fixed

- `familiar status` no longer reports `terminal: no` for a terminal config
  wired from a different installation (detection was tied to the invoked copy's
  path).

## [0.2.0] — 2026-07-09

### Added

- `familiar enable --terminal` with no kitten names — terminal-only mode.
- `familiar --version`.
- `$XDG_CONFIG_HOME/kitty` fallback when locating the kitty config directory
  (matches kitty's own resolution order).
- CI (GitHub Actions): tests + `brew style`.

### Changed

- Flicker-free rendering: every frame is applied atomically via kitty's
  synchronized output (mode 2026).
- Smooth fast scrolling in the file tree: the diff loads once the scroll
  settles instead of on every wheel step.
- Faster diffs on large files: expensive diff analysis is computed once per
  file and reused on horizontal scroll and gap expansion.
- Binary files show a `(binary file)` placeholder instead of a garbled diff.
- When a git call fails (`index.lock`, corrupted repo), the kittens show the
  actual git error instead of an empty "no changes"/"no commits".

### Fixed

- review: branch scope diffs against the merge-base, so commits that landed
  on the base branch no longer show up as "reverse" changes.
- review: `+/−` stats no longer disappear for files renamed inside
  subdirectories.
- review: staged scope works in a repository without commits; base-branch
  detection handles branch names with slashes (`release/1.0`).
- log: `git fetch` no longer freezes the UI (runs in the background).
- session: session titles and diffs read files as UTF-8 regardless of the
  locale; renaming a session is safe with non-ASCII content.
- `familiar disable --restore` restores kitty.conf from `.bak` even when the
  managed block was already removed.
- Conflicting `familiar enable` combinations (`--all` with names or
  `--kittens`) now fail with an error instead of being silently ignored.
- kitty.conf is written atomically (temp file + `os.replace`).
- `familiar status` detects terminal mode by the exact `include` line.

## [0.1.0] — 2026-07-09

First release: session/review/log kittens, terminal config, the `familiar`
CLI (enable/disable/status), installation via a Homebrew tap.
