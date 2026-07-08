# session

[English](session.md) · [Русский](../ru/session.md)

Kitten for [kitty](https://sw.kovidgoyal.net/kitty/): a full-screen overlay for
browsing and managing Claude Code sessions — right from the terminal, on a hotkey.

![session — a project's session list with live status](../img/session.png)

The three screens — a project's sessions, the projects list, and the conversation preview:

![session — projects list](../img/session-projects.png)

![session — conversation preview](../img/session-preview.png)

## What it does

- **Projects → sessions navigation.** A list of projects (`~/.claude/projects`), and
  inside it — the sessions of the selected project. The current project (by window `cwd`)
  is marked `(here)`.
- **Live activity.** Shows which sessions are running **right now** and their
  status (`busy` / `idle` / `waiting: permission prompt`) — the data source is
  reliable, from the registry of live processes, not from file timestamps.
- **Resume / fork.** `o` (or `Enter`) — a new tab with `claude --resume <id>` in the
  project folder; `f` — the same, but `--fork-session` (fork the conversation without
  touching the original session).
- **New session / continue.** `n` — a new `claude` in the folder (the project, or the
  session's project); `c` (on the projects screen) — `claude --continue` (resume the
  project's last session). If the project window is already busy with a running `claude`,
  the new session opens beside it in a split — that needs the `splits` layout
  from the [config](../../config/README.md); on a stock kitty it just opens as a
  separate window instead (either way the running session isn't covered, and
  nothing breaks).
- **Worktree.** `w` — `claude --worktree <name>`: create an isolated git worktree and
  start a session in it (parallel work without touching the main working tree). The name
  is asked for in the input line; if empty, Claude generates it itself.
- **Conversation preview** with a transcript (`▌ You` / `▌ Claude` / tool calls)
  and text search (`/`, jump with `n` / `N`, matches highlighted).
- **Renaming** a session (`r`). Writes a `custom-title` entry into the session file — the
  same thing the `/rename` command does in Claude Code, so the name shows up both there
  and here.
- **List filter** by name (`/` in the list).
- Hides noise: sdk sessions (`entrypoint: sdk-cli`) and internal `~/.claude/…` folders
  are hidden by default; toggle with `a`.
- Shortcuts also work on a **Russian keyboard layout** (by key position).

## Setup

In `~/.config/kitty/kitty.conf` (or a separate include file):

```conf
map cmd+shift+s kitten /path/to/familiar/plugins/session.py
```

Reload the config with `Cmd+Ctrl+,` (macOS) or restart kitty. Open with: `cmd+shift+s`.

## Keys

**Mouse**: click a row — select; click the selected row again — open (enter the
project / preview the session).

**Lists (projects / sessions)**

| Key | Screen | Action |
|---|---|---|
| `↑/↓`, `PgUp`/`PgDn` | both | navigation |
| `g` / `G`, `Home`/`End` | both | to start / end |
| mouse click | both | select row · click again — open |
| `Enter` | both | open project / preview session |
| `→` | sessions | preview (on projects — open) |
| `n` | both | new session (`claude`) in the directory |
| `w` | both | worktree (`claude --worktree`) + new session |
| `c` | projects | continue (`claude --continue`) |
| `a` | projects | show all sessions / `cli` only |
| `o` | sessions | resume — new tab with `claude --resume` |
| `f` | sessions | fork — resume with `--fork-session` |
| `p` | sessions | preview conversation (same as `Enter`) |
| `r` | sessions | rename session |
| `/` | both | search (filter) the list |
| `Esc` | both | back (or clear the filter) |
| `q` | both | quit |

**Preview**

| Key | Action |
|---|---|
| `↑/↓`, `PgUp`/`PgDn`, `g`/`G`, `Home`/`End` | scroll |
| `/` | search the conversation text |
| `n` / `N` | next / previous match |
| `o` | resume |
| `f` | fork (resume with `--fork-session`) |
| `Esc` `←` | back |
| `q` | quit |

## How activity is determined

Claude Code keeps a registry of live processes in `~/.claude/sessions/<pid>.json`
(`sessionId`, `cwd`, `status`, `waitingFor`). The plugin reads it and checks that the
`pid` is alive — so active sessions are detected precisely, with a real status, not by
mtime.

## Data sources

- `~/.claude/projects/<enc>/<uuid>.jsonl` — sessions (`<enc>` is the project path with
  `/` and `.` replaced by `-`). The title comes from `custom-title` (`/rename`),
  otherwise `ai-title`, otherwise the first message.
- `~/.claude/sessions/<pid>.json` — registry of running sessions and their statuses.
