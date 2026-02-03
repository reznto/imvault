"""Read-only access to the iMessage chat.db SQLite database."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from .constants import APPLE_EPOCH, NANOSECOND_THRESHOLD, TAPBACK_MAP

if TYPE_CHECKING:
    from .contacts import ContactResolver

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.expanduser("~/Library/Messages/chat.db")

# SQL queries
_SQL_LIST_CHATS = """
SELECT
    c.ROWID,
    c.chat_identifier,
    c.display_name,
    c.style,
    COUNT(DISTINCT cm.message_id) AS message_count,
    MAX(m.date) AS last_date,
    GROUP_CONCAT(DISTINCT h.id) AS participants
FROM chat c
JOIN chat_message_join cm ON cm.chat_id = c.ROWID
JOIN message m ON m.ROWID = cm.message_id
LEFT JOIN chat_handle_join ch ON ch.chat_id = c.ROWID
LEFT JOIN handle h ON h.ROWID = ch.handle_id
GROUP BY c.ROWID
ORDER BY last_date DESC
"""

_SQL_GET_MESSAGES = """
SELECT
    m.ROWID,
    m.guid,
    m.text,
    m.attributedBody,
    m.date,
    m.is_from_me,
    m.associated_message_guid,
    m.associated_message_type,
    m.handle_id,
    h.id AS sender_id,
    m.cache_has_attachments
FROM message m
JOIN chat_message_join cm ON cm.message_id = m.ROWID
LEFT JOIN handle h ON h.ROWID = m.handle_id
WHERE cm.chat_id = ?
ORDER BY m.date ASC
"""

_SQL_GET_ATTACHMENTS = """
SELECT
    a.ROWID,
    a.filename,
    a.mime_type,
    a.transfer_name
FROM attachment a
JOIN message_attachment_join ma ON ma.attachment_id = a.ROWID
WHERE ma.message_id = ?
"""


def _convert_timestamp(ts: int | None) -> str | None:
    """Convert Apple Core Data timestamp to ISO 8601 string.

    Apple stores timestamps as either seconds or nanoseconds since 2001-01-01.
    """
    if ts is None or ts == 0:
        return None
    if ts > NANOSECOND_THRESHOLD:
        seconds = ts / 1_000_000_000
    else:
        seconds = ts
    dt = APPLE_EPOCH + timedelta(seconds=seconds)
    return dt.isoformat()


def _has_text_content(s: str) -> bool:
    """Return True if *s* contains at least one letter, digit, or non-ASCII char.

    Used to distinguish real message text from binary format artifacts like "+!".
    """
    return any(c.isalnum() or ord(c) > 127 for c in s)


def _strip_format_prefix(text: str) -> str:
    """Strip leading typedstream format bytes that leak from attributedBody parsing.

    The binary format uses '+' (0x2B) as a type marker followed by a single
    length/encoding byte before the actual text.  When those bytes are printable
    ASCII they can end up in the extracted string (e.g. '+V', '+=', '+5').
    We detect this by checking if the third character (the real text start)
    is a letter or non-ASCII character like an emoji.  This only runs on text
    pulled from attributedBody, never on the text column.
    """
    if len(text) > 2 and text[0] == "+":
        third = text[2]
        if third.isalpha() or ord(third) > 127:
            stripped = text[2:]
            if _has_text_content(stripped):
                return stripped
    return text


def _parse_text(text: str | None, attributed_body: bytes | None) -> str | None:
    """Extract message text, falling back to attributedBody parsing."""
    if text:
        return text

    if not attributed_body:
        return None

    # Try nska_deserialize first
    try:
        import nska_deserialize as nd
        import io

        plist_obj = nd.deserialize_plist(io.BytesIO(attributed_body))
        if isinstance(plist_obj, dict):
            ns_objects = plist_obj.get("$objects", [])
            for obj in ns_objects:
                if isinstance(obj, str) and len(obj) > 1 and obj not in (
                    "$null",
                    "NSString",
                    "NSMutableString",
                    "NSAttributedString",
                    "NSMutableAttributedString",
                    "NSObject",
                ) and _has_text_content(obj):
                    return _strip_format_prefix(obj)
    except Exception:
        logger.debug("nska_deserialize failed, trying manual parse")

    # Manual fallback: look for the text between known markers
    try:
        content = attributed_body
        # Try to find text after "NSString" / "NSMutableString" marker.
        # The bytes immediately after the marker are class metadata (version,
        # encoding, length) — typically 3-8 bytes.  Start scanning from
        # offset 3 to skip past them and avoid leaking format bytes like
        # '+' (0x2B) or '!' (0x21) into the extracted text.
        markers = [b"NSString", b"NSMutableString"]
        for marker in markers:
            idx = content.find(marker)
            if idx != -1:
                rest = content[idx + len(marker):]
                for i in range(3, min(20, len(rest))):
                    for end in range(len(rest), i, -1):
                        try:
                            candidate = rest[i:end].decode("utf-8")
                            if (
                                len(candidate) > 1
                                and candidate.isprintable()
                                and not candidate.startswith("NS")
                                and _has_text_content(candidate)
                            ):
                                return _strip_format_prefix(candidate)
                        except (UnicodeDecodeError, ValueError):
                            continue

        # Last resort: look for the longest printable UTF-8 substring
        # The text is usually stored with a leading length indicator
        text_start = content.find(b"\x01+")
        if text_start != -1:
            remaining = content[text_start + 2:]
            try:
                # Read length byte
                length = remaining[0]
                text_bytes = remaining[1: 1 + length]
                return _strip_format_prefix(text_bytes.decode("utf-8"))
            except (IndexError, UnicodeDecodeError):
                pass
    except Exception:
        logger.debug("Manual attributedBody parse failed")

    return None


def _strip_reaction_guid(guid: str) -> str:
    """Strip tapback prefixes from associated_message_guid.

    Reaction GUIDs have prefixes like 'p:0/GUID' or 'bp:GUID'.
    """
    if guid.startswith("p:"):
        # Format: p:N/GUID
        slash = guid.find("/")
        if slash != -1:
            return guid[slash + 1:]
    elif guid.startswith("bp:"):
        return guid[3:]
    return guid


class IMMessageDB:
    """Read-only interface to the iMessage chat.db database."""

    def __init__(
        self,
        db_path: str | None = None,
        resolver: ContactResolver | None = None,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.resolver = resolver
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(
                f"chat.db not found at {self.db_path}\n"
                "Make sure the path is correct and that Terminal (or your app) "
                "has Full Disk Access in System Settings > Privacy & Security."
            )
        uri = f"file:{self.db_path}?mode=ro"
        try:
            self.conn = sqlite3.connect(uri, uri=True)
        except sqlite3.OperationalError as e:
            if "unable to open" in str(e).lower() or "authorization denied" in str(e).lower():
                raise PermissionError(
                    f"Cannot open chat.db at {self.db_path}\n"
                    "Grant Full Disk Access to Terminal (or your app) in "
                    "System Settings > Privacy & Security > Full Disk Access."
                ) from e
            raise
        self.conn.row_factory = sqlite3.Row

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _resolve(self, identifier: str) -> str:
        """Resolve an identifier to a contact name, falling back to the original."""
        if self.resolver is not None:
            name = self.resolver.resolve(identifier)
            if name is not None:
                return name
        return identifier

    def list_chats(self) -> list[dict[str, Any]]:
        """Return all chat threads with metadata."""
        cursor = self.conn.execute(_SQL_LIST_CHATS)
        chats = []
        for row in cursor:
            participants = row["participants"]
            participant_list = (
                [p.strip() for p in participants.split(",") if p.strip()]
                if participants
                else []
            )
            display = row["display_name"] or row["chat_identifier"]

            # Resolve display name if it still looks like a raw identifier
            if display == row["chat_identifier"]:
                display = self._resolve(display)

            # Resolve participant identifiers to contact names
            participant_list = [self._resolve(p) for p in participant_list]

            chats.append({
                "chat_id": row["ROWID"],
                "chat_identifier": row["chat_identifier"],
                "display_name": display,
                "style": row["style"],  # 43 = group, 45 = DM
                "message_count": row["message_count"],
                "last_date": _convert_timestamp(row["last_date"]),
                "participants": participant_list,
            })
        return chats

    def get_messages(self, chat_id: int) -> list[dict[str, Any]]:
        """Return all messages for a chat, with attachments and reactions attached."""
        cursor = self.conn.execute(_SQL_GET_MESSAGES, (chat_id,))
        raw_messages = []
        for row in cursor:
            text = _parse_text(row["text"], row["attributedBody"])
            msg: dict[str, Any] = {
                "rowid": row["ROWID"],
                "guid": row["guid"],
                "text": text,
                "date": _convert_timestamp(row["date"]),
                "is_from_me": bool(row["is_from_me"]),
                "sender": "me" if row["is_from_me"] else self._resolve(row["sender_id"] or "unknown"),
                "associated_message_guid": row["associated_message_guid"],
                "associated_message_type": row["associated_message_type"],
                "attachments": [],
                "reactions": [],
            }
            # Fetch attachments
            if row["cache_has_attachments"]:
                att_cursor = self.conn.execute(_SQL_GET_ATTACHMENTS, (row["ROWID"],))
                for att in att_cursor:
                    filename = att["filename"]
                    if filename:
                        filename = os.path.expanduser(filename)
                    msg["attachments"].append({
                        "rowid": att["ROWID"],
                        "filename": filename,
                        "mime_type": att["mime_type"],
                        "transfer_name": att["transfer_name"],
                    })
            raw_messages.append(msg)

        return _separate_reactions(raw_messages)


def _separate_reactions(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split tapback reactions from regular messages and attach them to targets."""
    # Build GUID -> message index
    guid_map: dict[str, dict[str, Any]] = {}
    regular: list[dict[str, Any]] = []
    reactions: list[dict[str, Any]] = []

    for msg in messages:
        assoc_type = msg["associated_message_type"] or 0
        if assoc_type == 0:
            # Normal message
            guid_map[msg["guid"]] = msg
            regular.append(msg)
        elif assoc_type in TAPBACK_MAP:
            # Standard tapback — will be attached to target message
            reactions.append(msg)
        else:
            # Other associated messages: emoji reactions (2006/3006),
            # edits, unsends, etc. — filter from output.
            logger.debug(
                "Filtering associated message type %d: %s",
                assoc_type,
                (msg.get("text") or "")[:80],
            )

    # Attach reactions to their target messages
    for rxn in reactions:
        target_guid = rxn.get("associated_message_guid", "")
        if target_guid:
            target_guid = _strip_reaction_guid(target_guid)
        target = guid_map.get(target_guid)
        if target:
            assoc_type = rxn["associated_message_type"]
            reaction_name = TAPBACK_MAP.get(assoc_type, "Unknown")
            target["reactions"].append({
                "type": reaction_name,
                "sender": rxn["sender"],
                "date": rxn["date"],
            })
        else:
            logger.debug("Reaction target not found: %s", target_guid)

    # Clean up internal fields from output
    for msg in regular:
        msg.pop("associated_message_guid", None)
        msg.pop("associated_message_type", None)

    return regular
