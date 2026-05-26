"""Assemble tar.gz archives from iMessage conversations, then encrypt to .imv."""

import io
import json
import logging
import os
import tarfile
import tempfile
import hashlib
from dataclasses import dataclass
from importlib import resources
from typing import Any

from .crypto import decrypt_archive, encrypt_archive, encrypt_archive_from_file
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


def _safe_name_part(name: str) -> str:
    """Sanitize a single filename component for generated archive paths."""
    safe = []
    for char in name:
        if char.isalnum() or char in ("-", "_", "."):
            safe.append(char)
        else:
            safe.append("_")
    value = "".join(safe).strip("._")
    return value or "item"


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
    messages: list[dict[str, Any]],
    attachment_prefix: str,
    tf: tarfile.TarFile,
    on_attachment=None,
) -> list[dict[str, Any]]:
    """Process messages: copy attachments into tar, return serializable list.

    If on_attachment is given, it's called with no args after each attachment
    is written. Used by ArchiveBuilder to emit progress events.
    """
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
                if on_attachment is not None:
                    on_attachment()
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
        event_callback=None,
    ):
        self.db = db
        self.password = password
        self.output_path = output_path
        self.chat_ids = chat_ids
        self.progress = progress  # callable(current: int, total: int) or None
        # event_callback(event: str, *, chat_id: int|None, processed: int, total: int)
        self.event_callback = event_callback

    def _emit(
        self,
        event: str,
        *,
        chat_id: int | None,
        processed: int,
        total: int | None = None,
    ) -> None:
        if self.event_callback is not None:
            self.event_callback(
                event,
                chat_id=chat_id,
                processed=processed,
                total=total if total is not None else len(self.chat_ids),
            )

    def build(self) -> str:
        """Build the archive and stream-encrypt it to ``output_path``.

        Layout: the tar.gz payload is written to a temp file alongside
        ``output_path`` (so the rename is on-volume), then stream-encrypted
        chunk-by-chunk into ``output_path``. Memory bounded by CHUNK_SIZE
        regardless of payload size.
        """
        output_dir = os.path.dirname(os.path.abspath(self.output_path)) or "."
        fd, tar_path = tempfile.mkstemp(
            prefix=".imvault_tar_",
            suffix=".tar.gz",
            dir=output_dir,
        )
        os.close(fd)
        try:
            with tarfile.open(tar_path, mode="w:gz") as tf:
                if len(self.chat_ids) == 1:
                    self._populate_single(tf)
                else:
                    self._populate_multi(tf)

            def _on_encrypt(done: int, total: int) -> None:
                self._emit(
                    "encrypt_progress",
                    chat_id=None,
                    processed=done,
                    total=total,
                )

            encrypt_archive_from_file(
                tar_path,
                self.output_path,
                self.password,
                progress=_on_encrypt,
            )
        finally:
            try:
                os.remove(tar_path)
            except OSError:
                pass

        final_size = os.path.getsize(self.output_path)
        logger.info(
            "Archive written to %s (%d bytes)", self.output_path, final_size
        )
        return self.output_path

    def _populate_single(self, tf: tarfile.TarFile) -> None:
        """Populate a single-chat tar.gz into the given open tarfile."""
        chat_id = self.chat_ids[0]
        chats = self.db.list_chats()
        chat_meta = next((c for c in chats if c["chat_id"] == chat_id), None)

        self._emit("chat_started", chat_id=chat_id, processed=0)

        messages = self.db.get_messages(chat_id)
        on_attachment = (
            (lambda: self._emit("attachment", chat_id=chat_id, processed=0))
            if self.event_callback
            else None
        )
        processed = _prepare_messages(
            messages, "attachments", tf, on_attachment=on_attachment
        )

        data = {
            "chat": chat_meta or {"chat_id": chat_id},
            "messages": processed,
        }
        _add_string_to_tar(tf, "data.json", json.dumps(data, ensure_ascii=False, indent=2))

        html = _read_template("reader_single.html")
        _add_string_to_tar(tf, "index.html", html)

        if self.progress:
            self.progress(1, 1)
        self._emit("chat_done", chat_id=chat_id, processed=1)

    def _populate_multi(self, tf: tarfile.TarFile) -> None:
        """Populate a multi-chat tar.gz into the given open tarfile."""
        all_chats = self.db.list_chats()
        chat_map = {c["chat_id"]: c for c in all_chats}

        manifest = []

        for i, chat_id in enumerate(self.chat_ids):
            self._emit("chat_started", chat_id=chat_id, processed=i)

            chat_meta = chat_map.get(chat_id, {"chat_id": chat_id})
            messages = self.db.get_messages(chat_id)
            prefix = f"chats/{chat_id}/attachments"
            on_attachment = (
                (lambda cid=chat_id, idx=i: self._emit(
                    "attachment", chat_id=cid, processed=idx
                ))
                if self.event_callback
                else None
            )
            processed = _prepare_messages(
                messages, prefix, tf, on_attachment=on_attachment
            )

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
            self._emit("chat_done", chat_id=chat_id, processed=i + 1)

        _add_string_to_tar(
            tf, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )

        html = _read_template("reader_multi.html")
        _add_string_to_tar(tf, "index.html", html)


@dataclass
class _ArchivedAttachment:
    """Attachment bytes copied from an existing archive."""

    data: bytes
    mime_type: str | None
    transfer_name: str | None
    original_path: str
    digest: str


def _normalize_chat_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _chat_merge_key(chat: dict[str, Any]) -> str:
    """Build a stable-enough chat key across archives.

    chat.ROWID is intentionally not used because it is local to a chat.db.
    """
    style = chat.get("style") or ""
    participants = sorted(
        p for p in (_normalize_chat_token(p) for p in chat.get("participants", [])) if p
    )
    if participants:
        return f"style:{style}:participants:{'|'.join(participants)}"

    identifier = _normalize_chat_token(chat.get("chat_identifier"))
    if identifier:
        return f"style:{style}:identifier:{identifier}"

    return f"style:{style}:name:{_normalize_chat_token(chat.get('display_name'))}"


def _message_score(message: dict[str, Any]) -> int:
    """Score message completeness so duplicate GUIDs keep the richer copy."""
    score = 0
    if message.get("text"):
        score += 2
    if message.get("date"):
        score += 1
    if message.get("sender"):
        score += 1
    score += 3 * len(message.get("_attachment_refs", message.get("attachments", [])))
    score += 2 * len(message.get("reactions", []))
    return score


def _merge_reactions(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = []
    seen = set()
    for reaction in [*left, *right]:
        key = (
            reaction.get("type"),
            reaction.get("sender"),
            reaction.get("date"),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(reaction)
    return merged


def _merge_message(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Merge duplicate message GUIDs, preserving the richer message payload."""
    if _message_score(incoming) > _message_score(existing):
        base = dict(incoming)
        other = existing
    else:
        base = dict(existing)
        other = incoming

    base["reactions"] = _merge_reactions(
        base.get("reactions", []),
        other.get("reactions", []),
    )

    base["_attachment_refs"] = _dedupe_attachment_refs([
        *base.get("_attachment_refs", []),
        *other.get("_attachment_refs", []),
    ])
    return base


def _dedupe_attachment_refs(refs: list[_ArchivedAttachment]) -> list[_ArchivedAttachment]:
    deduped = []
    seen = set()
    for ref in refs:
        key = _attachment_dedupe_key(ref)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped


def _attachment_dedupe_key(ref: _ArchivedAttachment) -> tuple[str, str, int]:
    return (ref.digest, ref.mime_type or "", len(ref.data))


def _make_attachment_ref(
    data: bytes,
    mime_type: str | None,
    transfer_name: str | None,
    original_path: str,
) -> _ArchivedAttachment:
    return _ArchivedAttachment(
        data=data,
        mime_type=mime_type,
        transfer_name=transfer_name,
        original_path=original_path,
        digest=hashlib.sha256(data).hexdigest(),
    )


def _sort_message_key(message: dict[str, Any]) -> tuple[str, int]:
    return (message.get("date") or "", int(message.get("rowid") or 0))


def _read_json_member(tf: tarfile.TarFile, name: str) -> Any:
    member = tf.extractfile(name)
    if member is None:
        raise ValueError(f"Archive is missing {name}.")
    return json.loads(member.read().decode("utf-8"))


def _read_archive_chats(archive_path: str, password: str) -> list[dict[str, Any]]:
    """Decrypt an archive and return chat data with attachment bytes attached."""
    with open(archive_path, "rb") as f:
        encrypted = f.read()
    try:
        tar_gz = decrypt_archive(encrypted, password)
    except ValueError as e:
        raise ValueError(f"{archive_path}: {e}") from e

    chats = []
    with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
        names = set(tf.getnames())
        if "manifest.json" in names:
            manifest = _read_json_member(tf, "manifest.json")
            data_paths = [entry["data_path"] for entry in manifest]
        elif "data.json" in names:
            data_paths = ["data.json"]
        else:
            raise ValueError(f"{archive_path} is not a readable imvault archive.")

        for data_path in data_paths:
            chat_data = _read_json_member(tf, data_path)
            for message in chat_data.get("messages", []):
                refs = []
                for attachment in message.get("attachments", []):
                    path = attachment.get("path")
                    if not path or path not in names:
                        continue
                    member = tf.extractfile(path)
                    if member is None:
                        continue
                    refs.append(_make_attachment_ref(
                        data=member.read(),
                        mime_type=attachment.get("mime_type"),
                        transfer_name=attachment.get("transfer_name"),
                        original_path=path,
                    ))
                message["_attachment_refs"] = refs
            chats.append(chat_data)

    return chats


def inspect_archive(archive_path: str, password: str) -> dict[str, Any]:
    """Return message and attachment counts for an encrypted .imv archive."""
    with open(archive_path, "rb") as f:
        encrypted = f.read()
    try:
        tar_gz = decrypt_archive(encrypted, password)
    except ValueError as e:
        raise ValueError(f"{archive_path}: {e}") from e

    with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
        names = set(tf.getnames())
        if "manifest.json" in names:
            manifest = _read_json_member(tf, "manifest.json")
            data_paths = [entry["data_path"] for entry in manifest]
        elif "data.json" in names:
            data_paths = ["data.json"]
        else:
            raise ValueError(f"{archive_path} is not a readable imvault archive.")

        chats = []
        referenced_paths = set()
        missing_paths = set()
        for data_path in data_paths:
            chat_data = _read_json_member(tf, data_path)
            chat = chat_data.get("chat", {})
            messages = chat_data.get("messages", [])
            attachment_entries = 0
            attachment_present = 0
            attachment_missing = 0

            for message in messages:
                for attachment in message.get("attachments", []):
                    path = attachment.get("path")
                    if not path:
                        continue
                    attachment_entries += 1
                    if path in names:
                        attachment_present += 1
                        referenced_paths.add(path)
                    else:
                        attachment_missing += 1
                        missing_paths.add(path)

            chats.append({
                "display_name": chat.get("display_name") or chat.get("chat_identifier") or str(chat.get("chat_id")),
                "chat_id": chat.get("chat_id"),
                "messages": len(messages),
                "attachment_entries": attachment_entries,
                "attachment_present": attachment_present,
                "attachment_missing": attachment_missing,
            })

        return {
            "path": archive_path,
            "chats": chats,
            "chat_count": len(chats),
            "messages": sum(chat["messages"] for chat in chats),
            "attachment_entries": sum(chat["attachment_entries"] for chat in chats),
            "attachment_present": sum(chat["attachment_present"] for chat in chats),
            "attachment_missing": sum(chat["attachment_missing"] for chat in chats),
            "unique_attachment_files": len(referenced_paths),
            "missing_attachment_files": len(missing_paths),
        }


def archive_attachment_digests(archive_path: str, password: str, progress=None) -> set[str]:
    """Return SHA-256 digests for attachment files referenced by an archive."""
    with open(archive_path, "rb") as f:
        encrypted = f.read()
    try:
        tar_gz = decrypt_archive(encrypted, password)
    except ValueError as e:
        raise ValueError(f"{archive_path}: {e}") from e

    digests = set()
    with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
        names = set(tf.getnames())
        if "manifest.json" in names:
            manifest = _read_json_member(tf, "manifest.json")
            data_paths = [entry["data_path"] for entry in manifest]
        elif "data.json" in names:
            data_paths = ["data.json"]
        else:
            raise ValueError(f"{archive_path} is not a readable imvault archive.")

        referenced_paths = set()
        for data_path in data_paths:
            chat_data = _read_json_member(tf, data_path)
            for message in chat_data.get("messages", []):
                for attachment in message.get("attachments", []):
                    path = attachment.get("path")
                    if path and path in names:
                        referenced_paths.add(path)

        for index, path in enumerate(referenced_paths, start=1):
            member = tf.extractfile(path)
            if member is not None:
                digests.add(hashlib.sha256(member.read()).hexdigest())
            if progress:
                progress(index, len(referenced_paths))

    return digests


def _attachment_ref_from_file(attachment: dict[str, Any]) -> _ArchivedAttachment | None:
    src_path = attachment.get("filename")
    if not src_path:
        return None

    src_path = os.path.expanduser(src_path)
    if not os.path.isfile(src_path):
        if "/T/" in src_path or "/tmp/" in src_path:
            logger.debug("Transient attachment missing (expected): %s", src_path)
        else:
            logger.warning("Attachment missing: %s", src_path)
        return None

    try:
        with open(src_path, "rb") as f:
            data = f.read()
    except (OSError, PermissionError) as e:
        logger.warning("Could not read attachment %s: %s", src_path, e)
        return None

    return _make_attachment_ref(
        data=data,
        mime_type=attachment.get("mime_type"),
        transfer_name=attachment.get("transfer_name"),
        original_path=src_path,
    )


def _read_current_chats(db: IMMessageDB, chat_ids: list[int]) -> list[dict[str, Any]]:
    all_chats = db.list_chats()
    chat_map = {chat["chat_id"]: chat for chat in all_chats}
    chats = []

    for chat_id in chat_ids:
        chat = chat_map.get(chat_id, {"chat_id": chat_id})
        messages = db.get_messages(chat_id)
        for message in messages:
            refs = []
            for attachment in message.get("attachments", []):
                ref = _attachment_ref_from_file(attachment)
                if ref is not None:
                    refs.append(ref)
            message["_attachment_refs"] = refs
        chats.append({
            "chat": chat,
            "messages": messages,
        })

    return chats


class MergedArchiveBuilder:
    """Build a deduplicated archive from encrypted archives and optional chat.db data."""

    def __init__(
        self,
        archives: list[tuple[str, str]],
        password: str,
        output_path: str,
        current_db: IMMessageDB | None = None,
        current_chat_ids: list[int] | None = None,
        progress=None,
        status=None,
    ):
        self.archives = archives
        self.password = password
        self.output_path = output_path
        self.current_db = current_db
        self.current_chat_ids = current_chat_ids or []
        self.progress = progress
        self.status = status
        self.stats = {
            "archives": len(archives),
            "current_chats": len(self.current_chat_ids),
            "chats": 0,
            "messages": 0,
            "duplicates": 0,
            "attachments_written": 0,
            "attachments_deduped": 0,
        }

    def build(self) -> str:
        merged_chats = self._merge_inputs()
        if self.status:
            self.status(
                f"Building archive payload ({self.stats['messages']} messages across "
                f"{self.stats['chats']} chats)..."
            )

        output_dir = os.path.dirname(os.path.abspath(self.output_path)) or "."
        fd, tar_path = tempfile.mkstemp(
            prefix=".imvault_tar_",
            suffix=".tar.gz",
            dir=output_dir,
        )
        os.close(fd)
        try:
            with tarfile.open(tar_path, mode="w:gz") as tf:
                self._populate_tar(tf, merged_chats)

            tar_size = os.path.getsize(tar_path)
            if self.status:
                self.status(f"Encrypting archive payload ({tar_size} bytes)...")

            encrypt_archive_from_file(
                tar_path,
                self.output_path,
                self.password,
                progress=None,
            )
        finally:
            try:
                os.remove(tar_path)
            except OSError:
                pass

        final_size = os.path.getsize(self.output_path)
        if self.status:
            self.status(f"Wrote encrypted archive ({final_size} bytes).")
        logger.info(
            "Merged archive written to %s (%d bytes)", self.output_path, final_size
        )
        return self.output_path

    def _merge_inputs(self) -> list[dict[str, Any]]:
        chats_by_key: dict[str, dict[str, Any]] = {}
        total_sources = len(self.archives) + (1 if self.current_db and self.current_chat_ids else 0)
        completed_sources = 0

        for archive_path, archive_password in self.archives:
            for chat_data in _read_archive_chats(archive_path, archive_password):
                self._merge_chat(chats_by_key, chat_data)

            completed_sources += 1
            if self.progress:
                self.progress(completed_sources, total_sources)

        if self.current_db and self.current_chat_ids:
            for chat_data in _read_current_chats(self.current_db, self.current_chat_ids):
                self._merge_chat(chats_by_key, chat_data)

            completed_sources += 1
            if self.progress:
                self.progress(completed_sources, total_sources)

        merged_chats = []
        for index, merged in enumerate(chats_by_key.values(), start=1):
            messages = sorted(merged["messages_by_guid"].values(), key=_sort_message_key)
            chat = dict(merged["chat"])
            chat["chat_id"] = index
            chat["message_count"] = len(messages)
            chat["last_date"] = messages[-1].get("date") if messages else chat.get("last_date")
            merged_chats.append({
                "chat": chat,
                "messages": messages,
            })

        merged_chats.sort(
            key=lambda item: item["chat"].get("last_date") or "",
            reverse=True,
        )
        self.stats["chats"] = len(merged_chats)
        self.stats["messages"] = sum(len(c["messages"]) for c in merged_chats)
        return merged_chats

    def _merge_chat(
        self,
        chats_by_key: dict[str, dict[str, Any]],
        chat_data: dict[str, Any],
    ) -> None:
        chat = chat_data.get("chat", {})
        key = _chat_merge_key(chat)
        merged = chats_by_key.setdefault(key, {
            "chat": dict(chat),
            "messages_by_guid": {},
        })

        for message in chat_data.get("messages", []):
            guid = message.get("guid")
            if not guid:
                guid = f"missing-guid:{key}:{message.get('date')}:{message.get('rowid')}"

            messages_by_guid = merged["messages_by_guid"]
            if guid in messages_by_guid:
                messages_by_guid[guid] = _merge_message(messages_by_guid[guid], message)
                self.stats["duplicates"] += 1
            else:
                messages_by_guid[guid] = message

    def _populate_tar(
        self,
        tf: tarfile.TarFile,
        merged_chats: list[dict[str, Any]],
    ) -> None:
        if len(merged_chats) == 1:
            chat_data = self._write_chat(tf, merged_chats[0], "attachments")
            _add_string_to_tar(
                tf, "data.json", json.dumps(chat_data, ensure_ascii=False, indent=2)
            )
            _add_string_to_tar(tf, "index.html", _read_template("reader_single.html"))
        else:
            manifest = []
            for index, merged_chat in enumerate(merged_chats, start=1):
                chat = dict(merged_chat["chat"])
                chat["chat_id"] = index
                merged_chat = {**merged_chat, "chat": chat}
                prefix = f"chats/{index}/attachments"
                chat_data = self._write_chat(tf, merged_chat, prefix)
                data_path = f"chats/{index}/data.json"
                _add_string_to_tar(
                    tf, data_path, json.dumps(chat_data, ensure_ascii=False, indent=2)
                )
                manifest.append({
                    "chat_id": index,
                    "display_name": chat.get("display_name", str(index)),
                    "message_count": len(chat_data["messages"]),
                    "last_date": chat.get("last_date"),
                    "data_path": data_path,
                })

            _add_string_to_tar(
                tf, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )
            _add_string_to_tar(tf, "index.html", _read_template("reader_multi.html"))

    def _write_chat(
        self,
        tf: tarfile.TarFile,
        merged_chat: dict[str, Any],
        attachment_prefix: str,
    ) -> dict[str, Any]:
        output_messages = []
        for message_index, message in enumerate(merged_chat["messages"], start=1):
            entry: dict[str, Any] = {
                "rowid": message.get("rowid"),
                "guid": message.get("guid"),
                "text": message.get("text"),
                "date": message.get("date"),
                "is_from_me": message.get("is_from_me"),
                "sender": message.get("sender"),
                "reactions": message.get("reactions", []),
                "attachments": [],
            }
            guid_part = _safe_name_part(str(message.get("guid") or message_index))
            original_refs = message.get("_attachment_refs", [])
            refs = _dedupe_attachment_refs(original_refs)
            self.stats["attachments_deduped"] += len(original_refs) - len(refs)
            self.stats["attachments_written"] += len(refs)
            for attachment_index, ref in enumerate(refs, start=1):
                original_name = os.path.basename(ref.original_path) or ref.transfer_name or "attachment"
                name = _safe_name_part(f"{guid_part}_{attachment_index}_{original_name}")
                arc_path = _safe_arcname(f"{attachment_prefix}/{name}")
                _add_bytes_to_tar(tf, arc_path, ref.data)
                entry["attachments"].append({
                    "path": arc_path,
                    "mime_type": ref.mime_type,
                    "transfer_name": ref.transfer_name,
                })
            output_messages.append(entry)

        return {
            "chat": merged_chat["chat"],
            "messages": output_messages,
        }
