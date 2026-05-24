# Changelog

## 0.3.0

Machine-readable CLI for GUI / scripting consumers. These additions unblock
the macOS app (`appdromeda-tech/imvault-mac`), which invokes the CLI as a
bundled Python sidecar and parses its output programmatically.

### Added
- `imvault list --json` — emit a JSON array of chats on stdout:
  `{chat_id, display_name, participant_count, message_count, last_message_at}`.
- `imvault inspect --json` — emit a JSON object describing each inspected
  archive (chat counts, message counts, attachment counts, and per-chat
  breakdowns). When `--compare-attachments` is also set, the result includes
  a `compare_attachments` block.
- `imvault export --progress-json` — emit one JSON event per line on stderr
  while exporting: `{event, chat_id, processed, total}` with events
  `chat_started`, `chat_done`, and `attachment`. Lets a GUI drive a progress
  bar without scraping a terminal progress bar.
- `--password-fd N` on `export`, `view`, `merge`, and `inspect` — read
  passwords from the given file descriptor (one password per line) instead
  of an interactive prompt. Keeps passwords off `argv` (where they would
  appear in `ps`).

### Notes
- `view --password-fd` and `inspect --password-fd` read a single password
  line. `merge --password-fd` reads either one input password + one output
  password (default) or one password per archive + one output password
  (with `--separate-passwords`). `export --password-fd` reads a single
  password; the interactive confirmation prompt is bypassed.
- Existing human-readable output paths are unchanged.

## 0.2.1

Load `__version__` from package metadata via `importlib.metadata` to fix
version drift between `pyproject.toml` and the installed package.

## 0.2.0

`merge`, `inspect`, `browse`, and chunked v2 archive encryption.

## 0.1.0

Initial release: `list`, `export`, `view`.
