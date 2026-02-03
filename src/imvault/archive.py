"""Assemble tar.gz archives from iMessage conversations, then encrypt to .imv."""

import io
import json
import logging
import os
import tarfile
from importlib import resources
from typing import Any

from .crypto import encrypt_archive
from .db import IMMessageDB

logger = logging.getLogger(__name__)


def _read_template(name: str) -> str:
    """Read an HTML template from the templates package."""
    return resources.files("imvault.templates").joinpath(name).read_text(encoding="utf-8")


def _add_string_to_tar(tf: tarfile.TarFile, arcname: str, data: str) -> None:
    """Add a string as a file to an in-memory tar archive."""
    encoded = data.encode("utf-8")
    info = tarfile.TarInfo(name=arcname)
    info.size = len(encoded)
    tf.addfile(info, io.BytesIO(encoded))


def _add_bytes_to_tar(tf: tarfile.TarFile, arcname: str, data: bytes) -> None:
    """Add raw bytes as a file to an in-memory tar archive."""
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    tf.addfile(info, io.BytesIO(data))


def _safe_arcname(name: str) -> str:
    """Sanitize a filename for use in tar archives."""
    # Strip leading slashes and parent directory references
    name = name.lstrip("/")
    parts = [p for p in name.split("/") if p not in (".", "..")]
    return "/".join(parts)


def _copy_attachment(
    tf: tarfile.TarFile,
    attachment: dict[str, Any],
    prefix: str,
    rowid: int,
) -> str | None:
    """Copy an attachment file into the tar archive.

    Returns the archive-relative path, or None if the file is missing.
    """
    src_path = attachment.get("filename")
    if not src_path:
        return None

    src_path = os.path.expanduser(src_path)
    if not os.path.isfile(src_path):
        # Temp transcoding files are always cleaned up by macOS — not actionable.
        if "/T/" in src_path or "/tmp/" in src_path:
            logger.debug("Transient attachment missing (expected): %s", src_path)
        else:
            logger.warning("Attachment missing: %s", src_path)
        return None

    # Disambiguate with message ROWID
    basename = os.path.basename(src_path)
    arc_name = _safe_arcname(f"{prefix}/{rowid}_{basename}")

    try:
        tf.add(src_path, arcname=arc_name)
    except (OSError, PermissionError) as e:
        logger.warning("Could not read attachment %s: %s", src_path, e)
        return None

    return arc_name


def _prepare_messages(
    messages: list[dict[str, Any]], attachment_prefix: str, tf: tarfile.TarFile
) -> list[dict[str, Any]]:
    """Process messages: copy attachments into tar, return serializable list."""
    output = []
    for msg in messages:
        entry: dict[str, Any] = {
            "rowid": msg["rowid"],
            "guid": msg["guid"],
            "text": msg["text"],
            "date": msg["date"],
            "is_from_me": msg["is_from_me"],
            "sender": msg["sender"],
            "reactions": msg.get("reactions", []),
            "attachments": [],
        }
        for att in msg.get("attachments", []):
            arc_path = _copy_attachment(tf, att, attachment_prefix, msg["rowid"])
            if arc_path:
                entry["attachments"].append({
                    "path": arc_path,
                    "mime_type": att.get("mime_type"),
                    "transfer_name": att.get("transfer_name"),
                })
        output.append(entry)
    return output


class ArchiveBuilder:
    """Build an encrypted .imv archive from selected chats."""

    def __init__(
        self,
        db: IMMessageDB,
        password: str,
        output_path: str,
        chat_ids: list[int],
        progress=None,
    ):
        self.db = db
        self.password = password
        self.output_path = output_path
        self.chat_ids = chat_ids
        self.progress = progress  # callable(current: int, total: int) or None

    def build(self) -> str:
        """Build the archive and write to output_path. Returns the path."""
        if len(self.chat_ids) == 1:
            tar_bytes = self._build_single()
        else:
            tar_bytes = self._build_multi()

        encrypted = encrypt_archive(tar_bytes, self.password)

        with open(self.output_path, "wb") as f:
            f.write(encrypted)

        logger.info("Archive written to %s (%d bytes)", self.output_path, len(encrypted))
        return self.output_path

    def _build_single(self) -> bytes:
        """Build a single-chat tar.gz archive in memory."""
        chat_id = self.chat_ids[0]
        chats = self.db.list_chats()
        chat_meta = next((c for c in chats if c["chat_id"] == chat_id), None)

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            messages = self.db.get_messages(chat_id)
            processed = _prepare_messages(messages, "attachments", tf)

            data = {
                "chat": chat_meta or {"chat_id": chat_id},
                "messages": processed,
            }
            _add_string_to_tar(tf, "data.json", json.dumps(data, ensure_ascii=False, indent=2))

            html = _read_template("reader_single.html")
            _add_string_to_tar(tf, "index.html", html)

        if self.progress:
            self.progress(1, 1)

        return buf.getvalue()

    def _build_multi(self) -> bytes:
        """Build a multi-chat tar.gz archive in memory."""
        all_chats = self.db.list_chats()
        chat_map = {c["chat_id"]: c for c in all_chats}

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            manifest = []

            for i, chat_id in enumerate(self.chat_ids):
                chat_meta = chat_map.get(chat_id, {"chat_id": chat_id})
                messages = self.db.get_messages(chat_id)
                prefix = f"chats/{chat_id}/attachments"
                processed = _prepare_messages(messages, prefix, tf)

                chat_data = {
                    "chat": chat_meta,
                    "messages": processed,
                }
                data_path = f"chats/{chat_id}/data.json"
                _add_string_to_tar(
                    tf, data_path, json.dumps(chat_data, ensure_ascii=False, indent=2)
                )

                manifest.append({
                    "chat_id": chat_id,
                    "display_name": chat_meta.get("display_name", str(chat_id)),
                    "message_count": chat_meta.get("message_count", len(processed)),
                    "last_date": chat_meta.get("last_date"),
                    "data_path": data_path,
                })

                if self.progress:
                    self.progress(i + 1, len(self.chat_ids))

            _add_string_to_tar(
                tf, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )

            html = _read_template("reader_multi.html")
            _add_string_to_tar(tf, "index.html", html)

        return buf.getvalue()
