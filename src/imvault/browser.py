"""Browse iMessage conversations directly from chat.db without export."""

import http.server
import json
import logging
import os
import socket
import sys
import threading
import webbrowser
from functools import partial
from importlib import resources
from typing import Any
from urllib.parse import unquote

from .db import IMMessageDB

logger = logging.getLogger(__name__)


def _find_free_port() -> int:
    """Find a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _read_template(name: str) -> str:
    """Read an HTML template from the templates package."""
    return resources.files("imvault.templates").joinpath(name).read_text(encoding="utf-8")


class _BrowseHandler(http.server.BaseHTTPHandler):
    """HTTP handler that serves chat data directly from the database."""

    def __init__(self, request, client_address, server, *, db: IMMessageDB, chats: list):
        self.db = db
        self.chats = chats
        self.chat_map = {c["chat_id"]: c for c in chats}
        super().__init__(request, client_address, server)

    def log_message(self, format, *args):  # noqa: A002
        pass

    def _send_json(self, data: Any) -> None:
        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(content))
        self.end_headers()
        self.wfile.write(content)

    def _send_html(self, content: str) -> None:
        encoded = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(encoded))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_file(self, path: str) -> None:
        """Send a file from the filesystem (for attachments)."""
        path = os.path.expanduser(path)
        if not os.path.isfile(path):
            self.send_error(404, "File not found")
            return

        # Guess content type
        ext = os.path.splitext(path)[1].lower()
        content_types = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".gif": "image/gif", ".heic": "image/heic", ".webp": "image/webp",
            ".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/mp4",
            ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav",
            ".pdf": "application/pdf",
        }
        content_type = content_types.get(ext, "application/octet-stream")

        try:
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", size)
            self.end_headers()

            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    self.wfile.write(chunk)
        except (OSError, BrokenPipeError, ConnectionResetError):
            pass

    def _send_error(self, code: int, message: str) -> None:
        self.send_error(code, message)

    def do_GET(self):  # noqa: N802
        path = unquote(self.path)

        try:
            if path == "/" or path == "/index.html":
                self._send_html(_read_template("reader_multi.html"))

            elif path == "/manifest.json":
                manifest = []
                for chat in self.chats:
                    manifest.append({
                        "chat_id": chat["chat_id"],
                        "display_name": chat["display_name"],
                        "message_count": chat["message_count"],
                        "last_date": chat["last_date"],
                        "data_path": f"chats/{chat['chat_id']}/data.json",
                    })
                self._send_json(manifest)

            elif path.startswith("/chats/") and path.endswith("/data.json"):
                # Extract chat_id from path: /chats/{id}/data.json
                parts = path.split("/")
                if len(parts) >= 3:
                    try:
                        chat_id = int(parts[2])
                    except ValueError:
                        self._send_error(400, "Invalid chat ID")
                        return

                    if chat_id not in self.chat_map:
                        self._send_error(404, "Chat not found")
                        return

                    chat_meta = self.chat_map[chat_id]
                    messages = self.db.get_messages(chat_id)

                    # Rewrite attachment paths to use our proxy endpoint
                    for msg in messages:
                        for att in msg.get("attachments", []):
                            if att.get("filename"):
                                # Encode the path for URL
                                att["path"] = f"/attachment?path={att['filename']}"

                    chat_data = {
                        "chat": chat_meta,
                        "messages": messages,
                    }
                    self._send_json(chat_data)
                else:
                    self._send_error(400, "Invalid path")

            elif path.startswith("/attachment?path="):
                # Serve attachment from original location
                att_path = path[len("/attachment?path="):]
                self._send_file(att_path)

            else:
                self._send_error(404, "Not found")

        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            logger.exception("Error handling request: %s", path)
            try:
                self._send_error(500, str(e))
            except Exception:
                pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass


def browse_database(db_path: str, resolver=None) -> None:
    """Browse iMessage conversations directly from chat.db."""
    print(f"Opening database: {db_path}")

    db = IMMessageDB(db_path, resolver=resolver)

    print("Loading conversations...")
    chats = db.list_chats()

    if not chats:
        print("No conversations found.")
        db.close()
        return

    print(f"Found {len(chats)} conversation(s).")

    port = _find_free_port()
    handler = partial(_BrowseHandler, db=db, chats=chats)

    server = http.server.HTTPServer(("127.0.0.1", port), handler)

    url = f"http://127.0.0.1:{port}/index.html"
    print(f"\nBrowsing at {url}")
    print("Press Ctrl+C to stop.")

    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        db.close()
        print("\nShutting down.")
