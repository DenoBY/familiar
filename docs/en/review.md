# review

[English](review.md) · [Русский](../ru/review.md)

A kitten for [kitty](https://sw.kovidgoyal.net/kitty/): a two-pane overlay for
reviewing uncommitted git changes. On the left, a tree of changed files; on the
right, a **unified diff** with **syntax highlighting**, live as you navigate.

![review — a full-file diff with syntax highlighting](../img/review.png)

Two view modes — the whole file, or just the changed hunks:

![review — hunks-only view](../img/review-hunks.png)

## What it can do

- **Git scopes** (toggle `s`): **working** — uncommitted (vs `HEAD`),
  **staged** — what's in the index (vs `HEAD`), **vs \<branch\>** — the diff from the
  base branch (`main`/`master`, auto-detected). The current scope is shown in the header.
- On the left, a **tree** of files (folders in blue, files colored by status `M`/`A`/`D`/`R`).
- On the right, the **unified diff** of the selected file: additions in green `+`, deletions in red `−`,
  context with **syntax highlighting** (strings, comments, numbers, keywords —
  by file extension, no external dependencies). Updates instantly as you move.
- **Word-diff**: in a removed/added line pair, the words that actually changed
  are highlighted more brightly, not the whole line.
- **Two view modes** (`a`): hunks only (changes with context) or the **whole file**
  expanded, with changes marked inline.
- **Jump between changes** (`[` / `]`) — across edit blocks within the diff (in both modes).
- **Per-file line stats** in the tree (`+added −removed`), like in an IDE/GitHub.
- **Sticky header**: while scrolling, the enclosing function/class is pinned at the top.
- **Horizontal scroll** for long lines (`h` / `l`).
- **Comments → markdown** — you comment on lines right in the diff; pressing `w` collects all
  comments into markdown and copies them to the clipboard to feed back to Claude
  ("here are the comments, fix them"). Closes the review → edit loop.
- **Refresh** (`r`) — rescan changes without reopening the overlay (handy while Claude
  is still editing files).
- **Open in editor** (`e`) — open the current file at the visible line. The editor is chosen
  by project config: `.idea/` → JetBrains (PhpStorm/IDEA/PyCharm/…), `.vscode/` → VS Code,
  `.cursor/` → Cursor, `.zed/` → Zed — the **whole project** opens focused on the line,
  and the **overlay stays open**. If there's no config — `$VISUAL`/`$EDITOR`, otherwise
  `vim` in a new tab (in which case the overlay closes — a terminal editor needs a terminal).
- **Search the diff** (`/`, navigate with `n` / `N`) with match highlighting.
- Filter the tree by file name (`f`), Russian keyboard layout for shortcuts.

The project folder and git root are determined from the `cwd` of the window the hotkey was pressed in.

## Setup

```sh
familiar enable review
```

Reload the config with `Cmd+Ctrl+,` (macOS) or restart kitty. Open: `cmd+shift+r`.

Minimal fallback — a manual `map` in `~/.config/kitty/kitty.conf` (or an
included file):

```conf
map cmd+shift+r kitten /path/to/familiar/plugins/review.py
```

Unlike `familiar enable`, this bare map lacks the toggle-to-close behavior, the
guard against re-opening the overlay on top of itself, the Cyrillic key
duplicates, and the `cmd+c` / `cmd+shift+c` pass-through for copying inside the
overlay.

## Keys

| Key | Action |
|---|---|
Two focus areas: the **tree** (left, navigate files) and the **diff** (right, cursor over
lines for comments). Switch with `Tab` or the arrows `←` (tree) / `→` (diff).

**Mouse**: click a file in the tree to select it; click a diff line to place the cursor,
**double-click** to open a comment; click the `┈` separator to reveal hidden lines.
(While mouse capture is on, select text for copying with `Shift` held down.)

**Tree focus**

| Key | Action |
|---|---|
| `↑/↓` | navigate files (the diff on the right updates) |
| `g` / `G` | first / last file |
| `Enter` `Space` | collapse/expand folder |
| `→` `Tab` | go to the diff (cursor over lines) |
| `s` | git scope: working → staged → vs branch |
| `r` | rescan changes (refresh) |
| `u` | show/hide noisy folders (`.idea`, `node_modules`, `__pycache__`, …) |
| `f` | filter the tree by file name |
| `q` `Esc` | quit |

**Diff focus** (`→`/`Tab` from the tree; `←`/`Tab`/`Esc` — back to the tree)

| Key | Action |
|---|---|
| `↑/↓` | move cursor over diff lines |
| `g` / `G` | to the start / end of the diff |
| `Enter` | on the `┈` separator — reveal hidden context lines |
| `Enter` / `c` | comment on the line under the cursor (empty — delete one) |
| `{` / `}` | jump to the previous / next comment (`●`) |
| `w` | collect all comments into markdown and copy to the clipboard |
| `x` | delete all comments |
| `[` / `]` | previous / next change |

**Common (in both focus areas)**

| Key | Action |
|---|---|
| `PgUp` `PgDn` | scroll the diff (also `Ctrl+U` / `Ctrl+D`) |
| `h` / `l` | horizontal scroll of the diff (long lines) |
| `a` | view mode: hunks only ↔ whole file |
| `/` `n`/`N` | search the diff and jump between matches |
| `⌘c` | copy: in the tree — the file path, in the diff — the selection / line under the cursor |
| `⌘shift+c` | copy `path:line` (in the tree — the file path) |
| `e` | open the file in the project IDE (`.idea`/`.vscode`/`.cursor`/`.zed`) or `$EDITOR` |

File statuses (colored as in an IDE): `A` added — green, `M` modified — blue,
`D` deleted — red, `R` renamed — cyan, `?` untracked (new, not yet in git) —
**red** (the file is shown but marked as not added to git).

Noisy IDE folders (`.idea`, `.vscode`, `node_modules`, `__pycache__`, `dist`, `venv`,
etc.) are hidden by default — as in an IDE; `u` shows them.

## Working with Claude Code

### Comments back to Claude

1. `Tab` — go to the diff, `↑/↓` — land on a line.
2. `Enter` or `c` — write a comment (a `●` appears next to the line).
3. Go through all the spots, across different files.
4. `w` — all comments are collected into markdown and **copied to the clipboard**:

   ```markdown
   # Review comments

   ## app/Http/Controllers/UserController.php
   - **L42** `return $user->save();`
     no permission check, add authorize()
   ```

5. Paste (`Cmd+V`) into the Claude chat: "here are the review comments, fix them."

### Paths and lines

Besides collecting comments, the diff is a quick way to point Claude Code at a specific
spot in the code:

- `⌘c` copies the **absolute path** of the selected file (tree focus) or the
  **selection / line under the cursor** (diff focus).
- `⌘shift+c` copies `path:line` — the absolute path with the line number under the cursor.

Claude Code reads `path/to/file.py:42` as an exact reference and opens/edits that very
spot — no need to describe it in words ("in such-and-such file, somewhere near that
function"). Land on a line in the diff → `⌘shift+c` → `Cmd+V` into the prompt.

## Git scopes (what's compared with what)

Switched with `s`, the current one is shown in the header:

| Scope | Files | "Before" → "After" | Answers the question |
|---|---|---|---|
| **working** | `git status` (+ untracked) | `HEAD:file` → file on disk | what you've changed since the last commit |
| **staged** | `git diff --cached` | `HEAD:file` → version from the index (`:file`) | what will go into the next commit |
| **vs \<branch\>** | `git diff <base>` | `<base>:file` → file on disk | what's new in the branch relative to `main` |

The base branch (`base`) is auto-detected: `origin/HEAD` → `main` → `master` → `develop`.
