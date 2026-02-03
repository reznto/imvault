"""Decrypt and view .imv archives in a local browser."""

import http.server
import logging
import os
import socket
import sys
import tarfile
import tempfile
import threading
import webbrowser
from functools import partial
from io import BytesIO

from .crypto import decrypt_archive

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


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that suppresses request logs and broken-pipe errors."""

    def log_message(self, format, *args):  # noqa: A002
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except BrokenPipeError:
            pass

    def finish(self):
        try:
            super().finish()
        except BrokenPipeError:
            pass


def view_archive(archive_path: str, password: str) -> None:
    """Decrypt an .imv archive, extract to temp dir, and serve via HTTP."""
    if not os.path.isfile(archive_path):
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    file_size = os.path.getsize(archive_path)
    print(f"Loading archive ({_format_size(file_size)})...")

    data = None
    tar_gz = None

    try:
        # Read file
        print("  Reading encrypted data...", end="", flush=True)
        with open(archive_path, "rb") as f:
            data = f.read()
        print(" done")

        # Decrypt
        print("  Decrypting...", end="", flush=True)
        tar_gz = decrypt_archive(data, password)
        # Free encrypted data memory
        del data
        data = None
        print(" done")

        with tempfile.TemporaryDirectory(prefix="imvault_") as tmpdir:
            # Extract tar.gz to temp directory
            print("  Extracting files...", end="", flush=True)
            with tarfile.open(fileobj=BytesIO(tar_gz), mode="r:gz") as tf:
                members = tf.getmembers()
                total = len(members)
                for i, member in enumerate(members):
                    if not _validate_tar_member(member, tmpdir):
                        logger.warning("Skipping suspicious tar member: %s", member.name)
                        continue
                    tf.extract(member, tmpdir)
                    # Show progress every 100 files or at the end
                    if (i + 1) % 100 == 0 or i + 1 == total:
                        pct = (i + 1) * 100 // total
                        print(f"\r  Extracting files... {i + 1}/{total} ({pct}%)", end="", flush=True)
            # Free decrypted data memory
            del tar_gz
            tar_gz = None
            print(" done")

            port = _find_free_port()
            handler = partial(_QuietHandler, directory=tmpdir)

            server = http.server.HTTPServer(("127.0.0.1", port), handler)

            url = f"http://127.0.0.1:{port}/index.html"
            print(f"\nServing archive at {url}")
            print("Press Ctrl+C to stop.")

            # Open browser in a thread so we can start serving immediately
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()

            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.shutdown()
                print("\nShutting down.")

    except KeyboardInterrupt:
        print("\n\nCancelled.")
        # Clean up any allocated memory
        if data is not None:
            del data
        if tar_gz is not None:
            del tar_gz
        sys.exit(0)
