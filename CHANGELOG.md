# Changelog

## 0.4.1

Streaming encrypt for the export path — the symmetric counterpart of
0.4.0's streaming decrypt. Closes the last memory-bound operation in
the archive pipeline.

### Added
- `crypto.encrypt_archive_from_file(input_path, output_path, password, progress=None)`
  streams a tar.gz through AES-GCM chunk-by-chunk and writes the
  encrypted .imv directly to disk. Memory bounded by `CHUNK_SIZE`
  rather than by the payload size. Output is byte-equivalent (under
  decryption) to what the in-memory `encrypt_archive` would produce,
  so this is a drop-in replacement on the write side without a format
  bump.

### Changed
- `archive.ArchiveBuilder` (export) now writes the tar.gz to a temp
  file alongside the eventual `.imv` output, then stream-encrypts it
  into place. Peak memory for a 5 GB single-chat export drops from
  ~10 GB to a few MB.
- `archive.MergedArchiveBuilder` (merge) gets the same treatment.
- `export --progress-json` emits a new `encrypt_progress` event during
  the encrypt phase (after the last `chat_done`, before completion):
  `{"event": "encrypt_progress", "chat_id": null, "processed": N, "total": M}`.
  Older consumers that only know the chat/attachment events silently
  ignore it; opting in is purely additive.

### Notes
- `encrypt_archive(plaintext, password) -> bytes` is unchanged for
  callers that want the in-memory API.
- No format change. Existing `.imv` archives keep working under both
  the new and old code paths.

## 0.4.0

Streaming viewer + structured event output. The viewer no longer holds the
entire archive in memory while decrypting, and GUI consumers now get the same
machine-readable event stream that `export` already had.

### Added
- `crypto.decrypt_archive_to_file(input_path, output_path, password, progress=None)`
  streams `.imv` archives chunk-by-chunk through AES-GCM and writes the
  decrypted tar.gz directly to disk. Memory use is bounded by `CHUNK_SIZE`
  instead of by the archive size, so 5 GB-class archives no longer peak at
  ~10 GB of RAM. v1 archives still need to fit in memory because the format
  is a single AEAD block.
- `imvault view --no-browser` — skip the auto-launched system browser.
  Use this when a GUI is embedding the served URL in its own webview.
- `imvault view --progress-json` — emit one JSON event per line on stderr
  (`decrypt_progress`, `extract_progress`, `ready`) instead of the
  human-readable progress text. The `ready` event includes `url` and
  `port`. Pair with `--no-browser` from a GUI consumer.

### Changed
- `imvault view` now stream-decrypts to a temp file inside the existing
  per-session tempdir, then extracts from that file. Memory footprint of
  the viewer dropped by roughly the archive size.

### Notes
- `decrypt_archive(data, password) -> bytes` still works for callers that
  want the in-memory API (export's `inspect` path uses it). Nothing
  breaking.

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
