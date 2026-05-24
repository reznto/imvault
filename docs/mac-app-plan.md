# imvault macOS App — Implementation Plan

Handoff document for building a native macOS GUI for imvault. This is the source of truth — read it before starting work.

## What you're building

A native macOS app (`.app` bundle, distributed as a DMG) that wraps the existing imvault CLI for non-technical users. They double-click the app, grant permissions, pick conversations from a list, set an export path + password, and get an encrypted `.imv` archive. They can also open existing `.imv` archives in an embedded viewer (no terminal, no `imvault view archive.imv` invocation).

## Current state of the CLI

- CLI repo: `reznto/imvault` (the repo this doc lives in)
- Latest release: **v0.2.1** — pin to this version in the sidecar bundling script.
- Homebrew tap: `reznto/homebrew-imvault` (already shipping v0.2.1 with full Python resource blocks).
- Working commands: `list`, `browse`, `export`, `view`, `merge`, `inspect`.
- Encryption: AES-256-GCM, Argon2id KDF, v2 chunked format.
- HTML reader: self-contained, served by `src/imvault/viewer.py` over a local HTTP server with the templates in `src/imvault/templates/`.

The CLI is the source of truth for crypto + DB parsing + viewer HTML. The Mac app does not reimplement any of this — it invokes the CLI as a subprocess.

## Architectural decisions (locked in)

| Decision | Choice | Why |
|---|---|---|
| Stack | **SwiftUI front-end + bundled Python sidecar** (called "Stack A" in conversation) | Native UX, zero crypto/DB rewrite. Stack C (full Swift rewrite) is the fallback if the sidecar approach proves brittle in practice. |
| Repo layout | **Separate repo** — `appdromeda-tech/imvault-mac` | Different GitHub account (associated with Apple Dev Program). Independent release cadence (Mac app releases are heavier than CLI). Survives a future C pivot cleanly. |
| Min macOS | **14 Sonoma** | ~2.5 years old, covers nearly all iMessage users. Gives `@Observable`, `Inspector`, modern SwiftUI APIs. Bump to 15 only if a specific API requires it. |
| Distribution | **Direct DMG download**, signed + notarized via Developer ID | Mac App Store sandboxing makes reading `chat.db` painful (requires re-granting via NSOpenPanel each session). Skip MAS. |
| Apple Dev Program | **Enrolled** | Signing identity available. Notarization is mandatory — without it, Gatekeeper blocks the app for non-tech users (defeats the goal). |
| Auto-update | **None initially.** Add [Sparkle](https://sparkle-project.org) later if needed. | Ship simple first. |
| Sandbox | **No app sandbox.** | Required for raw chat.db access without nagging the user. Hardened runtime still on (required for notarization). |
| Logo / branding | **None yet** — placeholder icon for now. | Defer. |

## How the app talks to the CLI

Subprocess via `Process` in Swift, invoking `Contents/Resources/python-runtime/bin/imvault`. The CLI needs additions for the app to consume output reliably — these are **PRs to the imvault repo**, shipped as a v0.3.0 release the Mac app pins to:

### Required CLI additions (do these first, before the app needs them)

1. **`imvault list --json`** — emit chat list as a JSON array on stdout. Each entry: `{chat_id, display_name, participant_count, message_count, last_message_at}`. Current output is human-readable only.
2. **`imvault inspect --json`** — JSON wrapper around the existing summary. Per-chat breakdowns when `--by-chat` is passed.
3. **`imvault export --progress-json`** — emit one JSON object per line on stderr while exporting: `{event: "chat_started"|"chat_done"|"attachment", chat_id, processed, total}`. Lets the Swift side drive a progress bar.
4. **`imvault export --password-fd N`** — read the export password from file descriptor N instead of an interactive prompt. Avoids shipping the password on the command line (where it'd appear in `ps`). Same for `view --password-fd`, `merge --password-fd`, `inspect --password-fd`.

These are small additions — a day or two of Python work — and they unblock the Mac app cleanly. **Do them before scaffolding the Swift side.**

### Files you'll touch in this repo

- `src/imvault/cli.py` — add the flags above
- `src/imvault/db.py` — already has the data the JSON output needs, may need a serialization helper
- `tests/test_cli.py` — add tests for `--json` and `--password-fd` paths

## Sidecar bundling

Use [`python-build-standalone`](https://github.com/astral-sh/python-build-standalone) (the astral-sh / pyoxidizer fork) for a fully self-contained Python runtime. Standard system Python is not bundlable for distribution.

Script outline (commit as `scripts/build-sidecar.sh` in the Mac repo):

```bash
# 1. Download python-build-standalone for the target arch
# 2. Extract to build/python-runtime/
# 3. ./build/python-runtime/bin/python3 -m pip install imvault==0.2.1  (or current pin)
# 4. Strip __pycache__, tests, unused stdlib modules to shrink bundle
# 5. Codesign every .dylib, .so, and binary inside the runtime
#    (codesign --force --options runtime --sign "Developer ID Application: ...")
# 6. Output: build/python-runtime/ ready to copy into .app Resources/
```

**Critical for notarization:** every Mach-O binary in the bundled runtime must be signed individually with the hardened runtime. Notarization fails if even one `.so` is unsigned. Script this — don't do it by hand.

Target both `arm64` and `x86_64` initially; ship a universal `.app` or two DMGs. For minimum effort, ship arm64-only and add Intel later if there's demand.

Expected `.app` size: ~80–120 MB (Python runtime + cryptography + pyobjc dominate).

## Plan phases

### Phase 0 — CLI prep (in *this* repo, ~1–2 days)
- Add `--json` to `list` and `inspect`.
- Add `--progress-json` to `export`.
- Add `--password-fd N` to all commands that prompt for passwords.
- Add tests.
- Cut v0.3.0, push, update Homebrew tap.

### Phase 1 — Mac repo scaffolding (~1–2 days)
- Create `appdromeda-tech/imvault-mac` (empty repo with README + LICENSE).
- New SwiftUI Xcode project `ImvaultApp`, min deployment 14.0.
- `.gitignore` for Xcode (use the standard one).
- Set up the App Group / entitlements file:
  - `com.apple.security.personal-information.addressbook` (Contacts)
  - **No** `com.apple.security.app-sandbox` (need raw chat.db access)
- README documenting build steps (Xcode version, signing identity, sidecar build).

### Phase 2 — Python sidecar bundling (~1–2 days)
- `scripts/build-sidecar.sh` per the outline above, pinning imvault to the CLI release from Phase 0.
- Xcode Run Script build phase that runs the script and copies `build/python-runtime/` to `$BUILT_PRODUCTS_DIR/$WRAPPER_NAME/Contents/Resources/`.
- Swift wrapper `IMVaultCLI.swift` with typed wrappers:
  ```swift
  struct IMVaultCLI {
      static func list() async throws -> [Chat]
      static func export(chatIDs: [Int], to url: URL, password: String,
                         progress: @escaping (ExportProgress) -> Void) async throws
      static func inspect(archive: URL, password: String) async throws -> ArchiveInfo
      // ... etc
  }
  ```
  These wrap `Process` invocations and parse JSON output. Password goes through a pipe to `--password-fd`, never the command line.

### Phase 3 — Permissions onboarding (~1 day)
- App launch checks: can it read `~/Library/Messages/chat.db`? Can it access Contacts (via `CNContactStore.authorizationStatus`)?
- If FDA missing: show a screen explaining what FDA is and why it's needed, with a button that deep-links to `x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles`. **FDA cannot be granted programmatically** — the user must add the app in System Settings. After granting, the app needs to be relaunched.
- If Contacts denied: show a "names will appear as phone numbers" notice, offer to re-prompt or skip.

### Phase 4 — UI (~3–5 days)
- Three-pane window:
  - Sidebar: conversation list with search/filter, multi-select via checkboxes.
  - Main pane: selected conversation preview (recent messages) — optional for v1, can defer.
  - Bottom bar: "Export N selected…" button.
- Export sheet:
  - Output path picker (NSSavePanel, defaults to ~/Documents/imvault_export.imv)
  - Password field with confirmation
  - Progress sheet with ProgressView driven by `--progress-json`
- "Open archive" entry point: `.imv` file → file open dialog or drag-drop → password sheet → embedded `WKWebView` serving the reader HTML.
  - Two options for serving HTML: (a) launch `imvault view archive.imv --port N` as a subprocess and load `http://localhost:N` in WebView, or (b) decrypt to temp dir via a `--export-html` variant and load file URLs. **(a) is simpler** — reuses the existing viewer.py logic verbatim.

### Phase 5 — Distribution (~1–2 days)
- Sign the .app with Developer ID Application cert.
- `xcrun notarytool submit --wait` for notarization.
- `xcrun stapler staple` to embed the ticket.
- Build DMG with [`create-dmg`](https://github.com/sindresorhus/create-dmg).
- Set up a download page or just attach the DMG to GitHub releases on `imvault-mac`.

### Phase 6 — Polish (later)
- Sparkle auto-updates.
- Universal binary (arm64 + x86_64) if Intel demand surfaces.
- Logo + branded icon.
- Inline message preview in main pane.
- Drag-drop export (drag chats from sidebar onto Finder).

## Open decisions (defer until they bite)

- **App icon / branding** — placeholder for now.
- **Intel vs ARM-only initial release** — ARM-only is simpler; add Intel if requested.
- **Crash reporting** — Sentry / Crashlytics / nothing? Defer.
- **Telemetry** — almost certainly nothing, given the privacy positioning of the tool. Confirm before adding anything.

## Things to NOT do

- **Don't** rewrite crypto, KDF, or chat.db parsing in Swift unless/until pivoting to Stack C. The CLI is the source of truth.
- **Don't** sandbox the app. It cannot read chat.db sandboxed without per-session NSOpenPanel grants.
- **Don't** submit to Mac App Store as part of v1. Direct download only.
- **Don't** ship without notarization. Non-tech users will hit Gatekeeper and bounce.
- **Don't** put the password on the command line — always use `--password-fd` via a pipe.
- **Don't** check Apple credentials, signing certs, or `.p12` files into git. Use Xcode-managed signing or env vars in CI.

## First commit to `imvault-mac`

Suggested initial structure:

```
imvault-mac/
├── README.md                 # Build instructions, signing setup
├── LICENSE                   # MIT (match CLI)
├── .gitignore                # Standard Xcode gitignore
├── scripts/
│   └── build-sidecar.sh      # Python runtime bundling
└── ImvaultApp/               # Xcode project (created in Phase 1)
```

The README should answer: how to install Xcode + clone + run `build-sidecar.sh` + open in Xcode + sign with my cert + build.

## Context this doc doesn't capture

This plan was built up across a conversation that also covered: shipping v0.2.0 and v0.2.1 to brew (including discovering and fixing a broken formula that silently shipped without Python deps), and a `__version__` drift bug fixed via `importlib.metadata`. None of that is load-bearing for the Mac app work — just history. Git log on this repo has the details if you need them.
