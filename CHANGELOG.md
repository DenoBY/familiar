# Changelog

[English](CHANGELOG.md) · [Русский](CHANGELOG.ru.md)

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [SemVer](https://semver.org/).

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
