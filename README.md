# imvault

Browse, search, and archive iMessage conversations from macOS. View messages directly from chat.db or export to encrypted, portable `.imv` files with a self-contained HTML reader.

## Features

- **Browse directly** — view conversations from chat.db without exporting
- **Encrypted archives** — export to AES-256-GCM encrypted `.imv` files with Argon2id key derivation
- **Self-contained HTML viewer** — iMessage-style bubble UI with search, dark mode, and media gallery
- **Interactive chat selector** — fuzzy-search conversations to export with a terminal UI
- **Portable** — `.imv` files can be stored anywhere and viewed on any machine with imvault
- **Privacy-first** — unencrypted data never touches disk during export

## Requirements

- macOS (reads from `~/Library/Messages/chat.db`)
- Python 3.9+
- **Full Disk Access** for Terminal (System Settings > Privacy & Security > Full Disk Access)

### Contacts Access (Optional)

On first run, macOS will prompt for Contacts access. This is used to resolve phone numbers and emails to contact names in exports.

- **Allow**: Conversations show names like "John Smith" instead of "+1234567890"
- **Deny**: Everything works, but participants appear as raw phone numbers/emails

You can change this later in System Settings > Privacy & Security > Contacts.

## Installation

```bash
pip install imvault
```

Or install from source:

```bash
git clone https://github.com/reznto/imvault.git
cd imvault
pip install -e .
```

## Usage

### List conversations

```bash
imvault list
```

### Browse conversations (no export)

Browse your iMessage conversations directly without creating an archive:

```bash
imvault browse
```

This opens the same viewer interface but reads directly from chat.db. No encryption, no archive file — just quick access to browse your messages. Useful for searching or reviewing conversations before deciding what to export.

To browse a backup database:
```bash
imvault browse --db-path /path/to/backup/chat.db
```

### Export conversations

Interactive fuzzy selector:
```bash
imvault export -o archive.imv
```

The selector supports:
- **Type** to filter conversations by name
- **Tab** to toggle selection
- **Shift+Tab** to toggle and move up
- **Ctrl+A** to select/deselect all visible
- **Enter** to confirm

Export specific chat by ID:
```bash
imvault export --chat 42 -o archive.imv
```

Export multiple specific chats:
```bash
imvault export --chat 42 --chat 57 -o archive.imv
```

Export all conversations:
```bash
imvault export --all -o archive.imv
```

A progress bar shows export status for multi-chat archives.

**Export options:**
| Option | Description |
|--------|-------------|
| `--all` | Export all conversations |
| `--chat ID` | Export specific chat ID (can be repeated) |
| `-o, --output PATH` | Output file path (default: `imvault_export.imv`) |

### View an archive

```bash
imvault view archive.imv
```

This decrypts the archive, starts a local HTTP server, and opens the iMessage-style reader in your browser. Press Ctrl+C to stop.

The viewer includes:
- **Messages view** — iMessage-style chat bubbles with search
- **Attachments view** — grid gallery of all photos and videos
- **Dark mode** — toggle or follows system preference

### Using a backup or recovered database

You can export from a chat.db file in a different location (e.g., recovered from a backup):

```bash
imvault list --db-path /path/to/recovered/chat.db
imvault export --db-path /path/to/recovered/chat.db -o backup.imv
```

Or set via environment variable:
```bash
export IMVAULT_DB_PATH=/path/to/recovered/chat.db
imvault list
```

### Global options

| Option | Description |
|--------|-------------|
| `--db-path PATH` | Custom path to chat.db (default: `~/Library/Messages/chat.db`) |
| `-v, --verbose` | Increase verbosity (`-v` for info, `-vv` for debug) |
| `--version` | Show version |
| `--help` | Show help |

## Security

- Archives are encrypted with AES-256-GCM
- Keys are derived using Argon2id (64 MB memory, 3 iterations)
- The file header (magic bytes, salt, nonce, version) is used as authenticated associated data (AAD)
- Unencrypted message data is never written to disk during export — tar.gz is assembled in memory
- During viewing, decrypted data is extracted to a temporary directory that is cleaned up on exit

## License

MIT
