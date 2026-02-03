"""Shared fixtures for imvault tests."""

import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def mock_chat_db(tmp_path):
    """Create a mock iMessage chat.db with test data."""
    db_path = tmp_path / "chat.db"
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Create tables matching the iMessage schema
    cursor.executescript("""
        CREATE TABLE handle (
            ROWID INTEGER PRIMARY KEY,
            id TEXT NOT NULL
        );

        CREATE TABLE chat (
            ROWID INTEGER PRIMARY KEY,
            chat_identifier TEXT NOT NULL,
            display_name TEXT,
            style INTEGER DEFAULT 45
        );

        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            guid TEXT UNIQUE NOT NULL,
            text TEXT,
            attributedBody BLOB,
            date INTEGER,
            is_from_me INTEGER DEFAULT 0,
            associated_message_guid TEXT,
            associated_message_type INTEGER DEFAULT 0,
            handle_id INTEGER,
            cache_has_attachments INTEGER DEFAULT 0
        );

        CREATE TABLE attachment (
            ROWID INTEGER PRIMARY KEY,
            filename TEXT,
            mime_type TEXT,
            transfer_name TEXT
        );

        CREATE TABLE chat_message_join (
            chat_id INTEGER,
            message_id INTEGER,
            PRIMARY KEY (chat_id, message_id)
        );

        CREATE TABLE chat_handle_join (
            chat_id INTEGER,
            handle_id INTEGER,
            PRIMARY KEY (chat_id, handle_id)
        );

        CREATE TABLE message_attachment_join (
            message_id INTEGER,
            attachment_id INTEGER,
            PRIMARY KEY (message_id, attachment_id)
        );
    """)

    # Insert test handles
    cursor.executemany(
        "INSERT INTO handle (ROWID, id) VALUES (?, ?)",
        [
            (1, "+15551234567"),
            (2, "+15559876543"),
            (3, "+15555555555"),
        ],
    )

    # Insert test chats
    cursor.executemany(
        "INSERT INTO chat (ROWID, chat_identifier, display_name, style) VALUES (?, ?, ?, ?)",
        [
            (1, "+15551234567", "Alice", 45),
            (2, "chat123456", "Group Chat", 43),
        ],
    )

    # Link handles to chats
    cursor.executemany(
        "INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)",
        [
            (1, 1),
            (2, 1),
            (2, 2),
            (2, 3),
        ],
    )

    # Apple epoch nanoseconds for 2024-01-15 12:00:00 UTC
    # 2024-01-15 12:00:00 - 2001-01-01 00:00:00 = 727,012,800 seconds
    ts_base = 727012800_000_000_000  # nanoseconds

    # Insert messages for chat 1 (DM with Alice)
    messages = [
        (1, "msg-001", "Hey there!", None, ts_base, 0, None, 0, 1, 0),
        (2, "msg-002", "Hi! How are you?", None, ts_base + 60_000_000_000, 1, None, 0, None, 0),
        (3, "msg-003", "Doing great, thanks!", None, ts_base + 120_000_000_000, 0, None, 0, 1, 0),
        # A reaction to msg-001
        (4, "msg-004", "\u2764\ufe0f", None, ts_base + 130_000_000_000, 1, "p:0/msg-001", 2000, None, 0),
    ]
    cursor.executemany(
        "INSERT INTO message (ROWID, guid, text, attributedBody, date, is_from_me, "
        "associated_message_guid, associated_message_type, handle_id, cache_has_attachments) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        messages,
    )

    # Link messages to chat 1
    cursor.executemany(
        "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
        [(1, 1), (1, 2), (1, 3), (1, 4)],
    )

    # Insert messages for chat 2 (Group Chat)
    group_msgs = [
        (5, "msg-005", "Welcome everyone!", None, ts_base + 200_000_000_000, 1, None, 0, None, 0),
        (6, "msg-006", "Thanks for the invite", None, ts_base + 260_000_000_000, 0, None, 0, 2, 0),
        (7, "msg-007", "Glad to be here", None, ts_base + 320_000_000_000, 0, None, 0, 3, 0),
    ]
    cursor.executemany(
        "INSERT INTO message (ROWID, guid, text, attributedBody, date, is_from_me, "
        "associated_message_guid, associated_message_type, handle_id, cache_has_attachments) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        group_msgs,
    )

    cursor.executemany(
        "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
        [(2, 5), (2, 6), (2, 7)],
    )

    conn.commit()
    conn.close()

    return str(db_path)


@pytest.fixture
def mock_attachment(tmp_path):
    """Create a mock attachment file."""
    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    att_file = att_dir / "test_image.jpg"
    att_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # Fake JPEG header
    return str(att_file)
