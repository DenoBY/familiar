# Changelog

[English](CHANGELOG.md) · [Русский](CHANGELOG.ru.md)

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [SemVer](https://semver.org/).

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
