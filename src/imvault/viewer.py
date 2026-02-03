"""Decrypt and view .imv archives in a local browser."""

import http.server
import logging
import os
import socket
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

    with open(archive_path, "rb") as f:
        data = f.read()

    tar_gz = decrypt_archive(data, password)

    with tempfile.TemporaryDirectory(prefix="imvault_") as tmpdir:
        # Extract tar.gz to temp directory
        with tarfile.open(fileobj=BytesIO(tar_gz), mode="r:gz") as tf:
            for member in tf.getmembers():
                if not _validate_tar_member(member, tmpdir):
                    logger.warning("Skipping suspicious tar member: %s", member.name)
                    continue
                tf.extract(member, tmpdir)

        port = _find_free_port()
        handler = partial(_QuietHandler, directory=tmpdir)

        server = http.server.HTTPServer(("127.0.0.1", port), handler)

        url = f"http://127.0.0.1:{port}/index.html"
        print(f"Serving archive at {url}")
        print("Press Ctrl+C to stop.")

        # Open browser in a thread so we can start serving immediately
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")
        finally:
            server.shutdown()
