"""Decrypt and view .imv archives in a local browser."""

import http.server
import json
import logging
import os
import socket
import sys
import tarfile
import tempfile
import threading
import webbrowser
from functools import partial
from importlib import resources

from .crypto import decrypt_archive_to_file

logger = logging.getLogger(__name__)


def _find_free_port() -> int:
    """Find a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _validate_tar_member(member: tarfile.TarInfo, dest: str) -> bool:
    """Check a tar member for path traversal attacks."""
    abs_dest = os.path.abspath(dest)
    member_path = os.path.abspath(os.path.join(dest, member.name))
    if not member_path.startswith(abs_dest + os.sep) and member_path != abs_dest:
        return False
    if member.issym() or member.islnk():
        return False
    return True


def _format_size(size: int) -> str:
    """Format byte size to human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _read_template(name: str) -> str:
    """Read a bundled HTML reader template."""
    return resources.files("imvault.templates").joinpath(name).read_text(encoding="utf-8")


def _refresh_reader_template(tmpdir: str) -> None:
    """Replace archived reader HTML with the current bundled template."""
    manifest_path = os.path.join(tmpdir, "manifest.json")
    data_path = os.path.join(tmpdir, "data.json")
    if os.path.exists(manifest_path):
        template_name = "reader_multi.html"
    elif os.path.exists(data_path):
        template_name = "reader_single.html"
    else:
        return

    index_path = os.path.join(tmpdir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(_read_template(template_name))


def _emit_view_event(event: str, **fields) -> None:
    """Write a single JSON event line to stderr for --progress-json consumers."""
    payload = {"event": event, **fields}
    sys.stderr.write(json.dumps(payload) + "\n")
    sys.stderr.flush()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that suppresses request logs and connection errors."""

    def log_message(self, format, *args):  # noqa: A002
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def finish(self):
        try:
            super().finish()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass


def view_archive(
    archive_path: str,
    password: str,
    *,
    no_browser: bool = False,
    progress_json: bool = False,
) -> None:
    """Decrypt an .imv archive, extract to temp dir, and serve via HTTP.

    Args:
        archive_path: path to the .imv archive.
        password: archive password.
        no_browser: if True, don't auto-launch the system browser. Useful when
            a GUI consumer is embedding the served URL in its own webview.
        progress_json: if True, emit machine-readable JSON event lines on stderr
            (one per line: {"event": "...", "processed": N, "total": M, ...})
            instead of the human-readable progress text. The final ``ready``
            event also carries ``url`` and ``port``.
    """
    if not os.path.isfile(archive_path):
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    file_size = os.path.getsize(archive_path)
    if not progress_json:
        print(f"Loading archive ({_format_size(file_size)})...")

    try:
        with tempfile.TemporaryDirectory(prefix="imvault_") as tmpdir:
            # Stream-decrypt directly to a temp file inside tmpdir. Memory is
            # bounded by CHUNK_SIZE instead of by the archive size.
            tar_gz_path = os.path.join(tmpdir, "_decrypted.tar.gz")

            def _on_decrypt_progress(done: int, total: int) -> None:
                if progress_json:
                    _emit_view_event(
                        "decrypt_progress", processed=done, total=total
                    )
                elif (done % 50 == 0 or done == total) and total > 0:
                    pct = done * 100 // total
                    print(
                        f"\r  Decrypting... {done}/{total} ({pct}%)",
                        end="",
                        flush=True,
                    )

            if not progress_json:
                print("  Decrypting...", end="", flush=True)
            decrypt_archive_to_file(
                archive_path, tar_gz_path, password, progress=_on_decrypt_progress
            )
            if not progress_json:
                print(" done")

            # Extract.
            if not progress_json:
                print("  Extracting files...", end="", flush=True)
            with tarfile.open(tar_gz_path, mode="r:gz") as tf:
                members = tf.getmembers()
                total = len(members)
                for i, member in enumerate(members):
                    if not _validate_tar_member(member, tmpdir):
                        logger.warning(
                            "Skipping suspicious tar member: %s", member.name
                        )
                        continue
                    tf.extract(member, tmpdir)
                    if progress_json:
                        # Emit every ~50 files (keeps the stream paced without
                        # spamming for tiny archives).
                        if (i + 1) % 50 == 0 or i + 1 == total:
                            _emit_view_event(
                                "extract_progress",
                                processed=i + 1,
                                total=total,
                            )
                    elif (i + 1) % 100 == 0 or i + 1 == total:
                        pct = (i + 1) * 100 // total
                        print(
                            f"\r  Extracting files... {i + 1}/{total} ({pct}%)",
                            end="",
                            flush=True,
                        )
            if not progress_json:
                print(" done")

            # Free disk by removing the decrypted .tar.gz now that it's extracted.
            try:
                os.remove(tar_gz_path)
            except OSError:
                pass

            _refresh_reader_template(tmpdir)

            port = _find_free_port()
            handler = partial(_QuietHandler, directory=tmpdir)
            server = http.server.HTTPServer(("127.0.0.1", port), handler)
            url = f"http://127.0.0.1:{port}/index.html"

            if progress_json:
                _emit_view_event("ready", url=url, port=port)
            else:
                print(f"\nServing archive at {url}")
                print("Press Ctrl+C to stop.")

            if not no_browser:
                # Open browser in a thread so we can start serving immediately.
                threading.Timer(0.5, lambda: webbrowser.open(url)).start()

            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.shutdown()
                if not progress_json:
                    print("\nShutting down.")

    except KeyboardInterrupt:
        if not progress_json:
            print("\n\nCancelled.")
        sys.exit(0)
