"""Tests for imvault.db — iMessage database reader."""

from unittest.mock import MagicMock

import pytest

from imvault.contacts import ContactResolver, _normalize_phone
from imvault.db import IMMessageDB, _convert_timestamp, _parse_text, _strip_reaction_guid


class TestConvertTimestamp:
    """Test Apple timestamp conversion."""

    def test_nanosecond_timestamp(self):
        # 2024-01-15 12:00:00 UTC in Apple nanoseconds
        # Seconds from 2001-01-01 to 2024-01-15 12:00:00 = 727012800
        ts = 727012800_000_000_000
        result = _convert_timestamp(ts)
        assert result is not None
        assert "2024-01-15" in result
        assert "12:00:00" in result

    def test_second_timestamp(self):
        # Older macOS used seconds
        ts = 727012800
        result = _convert_timestamp(ts)
        assert result is not None
        assert "2024-01-15" in result

    def test_none_timestamp(self):
        assert _convert_timestamp(None) is None

    def test_zero_timestamp(self):
        assert _convert_timestamp(0) is None


class TestParseText:
    """Test message text extraction."""

    def test_plain_text_column(self):
        assert _parse_text("Hello world", None) == "Hello world"

    def test_plain_text_preferred_over_blob(self):
        assert _parse_text("Hello", b"blob data") == "Hello"

    def test_none_text_and_none_blob(self):
        assert _parse_text(None, None) is None

    def test_empty_text_and_none_blob(self):
        assert _parse_text("", None) is None


class TestStripReactionGuid:
    """Test GUID prefix stripping for reactions."""

    def test_p_prefix(self):
        assert _strip_reaction_guid("p:0/msg-001") == "msg-001"

    def test_bp_prefix(self):
        assert _strip_reaction_guid("bp:msg-002") == "msg-002"

    def test_no_prefix(self):
        assert _strip_reaction_guid("msg-003") == "msg-003"

    def test_p_with_different_index(self):
        assert _strip_reaction_guid("p:1/msg-004") == "msg-004"


class TestIMMessageDB:
    """Test the database reader with a mock chat.db."""

    def test_open_db(self, mock_chat_db):
        db = IMMessageDB(mock_chat_db)
        assert db.conn is not None
        db.close()

    def test_context_manager(self, mock_chat_db):
        with IMMessageDB(mock_chat_db) as db:
            chats = db.list_chats()
            assert len(chats) > 0

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            IMMessageDB(str(tmp_path / "nonexistent.db"))

    def test_list_chats(self, mock_chat_db):
        with IMMessageDB(mock_chat_db) as db:
            chats = db.list_chats()

        assert len(chats) == 2

        # Find Alice's chat
        alice = next(c for c in chats if c["display_name"] == "Alice")
        assert alice["chat_identifier"] == "+15551234567"
        assert alice["style"] == 45
        assert alice["message_count"] >= 3  # 3 regular + 1 reaction
        assert "+15551234567" in alice["participants"]

        # Find group chat
        group = next(c for c in chats if c["display_name"] == "Group Chat")
        assert group["style"] == 43
        assert group["message_count"] >= 3
        assert len(group["participants"]) == 3

    def test_list_chats_sorted_by_date(self, mock_chat_db):
        with IMMessageDB(mock_chat_db) as db:
            chats = db.list_chats()

        # Group chat has later timestamps, should be first
        assert chats[0]["display_name"] == "Group Chat"

    def test_get_messages_dm(self, mock_chat_db):
        with IMMessageDB(mock_chat_db) as db:
            chats = db.list_chats()
            alice = next(c for c in chats if c["display_name"] == "Alice")
            messages = db.get_messages(alice["chat_id"])

        # Should have 3 regular messages (reaction is separated out)
        assert len(messages) == 3

        assert messages[0]["text"] == "Hey there!"
        assert messages[0]["is_from_me"] is False
        assert messages[0]["sender"] == "+15551234567"

        assert messages[1]["text"] == "Hi! How are you?"
        assert messages[1]["is_from_me"] is True
        assert messages[1]["sender"] == "me"

    def test_reactions_attached(self, mock_chat_db):
        with IMMessageDB(mock_chat_db) as db:
            chats = db.list_chats()
            alice = next(c for c in chats if c["display_name"] == "Alice")
            messages = db.get_messages(alice["chat_id"])

        # First message should have a "Loved" reaction
        msg0 = messages[0]
        assert len(msg0["reactions"]) == 1
        assert msg0["reactions"][0]["type"] == "Loved"
        assert msg0["reactions"][0]["sender"] == "me"

    def test_get_messages_group(self, mock_chat_db):
        with IMMessageDB(mock_chat_db) as db:
            chats = db.list_chats()
            group = next(c for c in chats if c["display_name"] == "Group Chat")
            messages = db.get_messages(group["chat_id"])

        assert len(messages) == 3
        assert messages[0]["is_from_me"] is True
        assert messages[1]["sender"] == "+15559876543"
        assert messages[2]["sender"] == "+15555555555"

    def test_messages_have_dates(self, mock_chat_db):
        with IMMessageDB(mock_chat_db) as db:
            chats = db.list_chats()
            messages = db.get_messages(chats[0]["chat_id"])

        for msg in messages:
            assert msg["date"] is not None
            assert "2024" in msg["date"]

    def test_messages_have_guids(self, mock_chat_db):
        with IMMessageDB(mock_chat_db) as db:
            messages = db.get_messages(1)

        guids = [m["guid"] for m in messages]
        assert len(guids) == len(set(guids))  # All unique


class TestNormalizePhone:
    """Test phone number normalization."""

    def test_strip_non_digits(self):
        assert "5551234567" in _normalize_phone("(555) 123-4567")

    def test_plus_country_code(self):
        variants = _normalize_phone("+15551234567")
        assert "15551234567" in variants
        assert "5551234567" in variants

    def test_ten_digit_adds_country_code(self):
        variants = _normalize_phone("5551234567")
        assert "5551234567" in variants
        assert "15551234567" in variants

    def test_empty_string(self):
        assert _normalize_phone("") == []

    def test_non_phone_chars_only(self):
        assert _normalize_phone("---") == []


class TestContactResolver:
    """Test ContactResolver with mocked internals."""

    @pytest.fixture
    def resolver(self):
        """Create a ContactResolver with a pre-populated lookup (skip macOS Contacts)."""
        r = ContactResolver.__new__(ContactResolver)
        r._lookup = {
            "15551234567": "Alice Smith",
            "5551234567": "Alice Smith",
            "15559876543": "Bob Jones",
            "5559876543": "Bob Jones",
            "alice@example.com": "Alice Smith",
        }
        r._loaded = True
        return r

    def test_resolve_phone_with_plus(self, resolver):
        assert resolver.resolve("+15551234567") == "Alice Smith"

    def test_resolve_phone_without_plus(self, resolver):
        assert resolver.resolve("15551234567") == "Alice Smith"

    def test_resolve_ten_digit(self, resolver):
        assert resolver.resolve("5551234567") == "Alice Smith"

    def test_resolve_formatted_phone(self, resolver):
        assert resolver.resolve("(555) 123-4567") == "Alice Smith"

    def test_resolve_email(self, resolver):
        assert resolver.resolve("alice@example.com") == "Alice Smith"

    def test_resolve_email_case_insensitive(self, resolver):
        assert resolver.resolve("Alice@Example.COM") == "Alice Smith"

    def test_resolve_unknown_returns_none(self, resolver):
        assert resolver.resolve("+19999999999") is None

    def test_resolve_empty_lookup(self):
        r = ContactResolver.__new__(ContactResolver)
        r._lookup = {}
        r._loaded = False
        assert r.resolve("+15551234567") is None


class TestIMMessageDBWithResolver:
    """Test IMMessageDB contact resolution integration."""

    @pytest.fixture
    def mock_resolver(self):
        resolver = MagicMock()
        resolver.resolve = MagicMock(side_effect=lambda x: {
            "+15551234567": "Alice Smith",
            "+15559876543": "Bob Jones",
            "+15555555555": "Charlie Brown",
        }.get(x))
        return resolver

    def test_list_chats_resolves_participants(self, mock_chat_db, mock_resolver):
        with IMMessageDB(mock_chat_db, resolver=mock_resolver) as db:
            chats = db.list_chats()

        # Alice's chat has display_name set, so it won't be resolved
        alice_chat = next(c for c in chats if c["display_name"] == "Alice")
        # But her participant entry should be resolved
        assert "Alice Smith" in alice_chat["participants"]

        # Group chat participants should all be resolved
        group = next(c for c in chats if c["display_name"] == "Group Chat")
        assert "Alice Smith" in group["participants"]
        assert "Bob Jones" in group["participants"]
        assert "Charlie Brown" in group["participants"]

    def test_get_messages_resolves_sender(self, mock_chat_db, mock_resolver):
        with IMMessageDB(mock_chat_db, resolver=mock_resolver) as db:
            messages = db.get_messages(1)

        # Non-from-me messages should have resolved sender
        msg0 = messages[0]
        assert msg0["sender"] == "Alice Smith"

        # from_me messages stay as "me"
        msg1 = messages[1]
        assert msg1["sender"] == "me"

    def test_get_messages_group_resolves_senders(self, mock_chat_db, mock_resolver):
        with IMMessageDB(mock_chat_db, resolver=mock_resolver) as db:
            chats = db.list_chats()
            group = next(c for c in chats if c["display_name"] == "Group Chat")
            messages = db.get_messages(group["chat_id"])

        assert messages[0]["sender"] == "me"
        assert messages[1]["sender"] == "Bob Jones"
        assert messages[2]["sender"] == "Charlie Brown"

    def test_no_resolver_keeps_identifiers(self, mock_chat_db):
        """Without a resolver, behavior is unchanged from before."""
        with IMMessageDB(mock_chat_db) as db:
            chats = db.list_chats()
            messages = db.get_messages(1)

        alice_chat = next(c for c in chats if c["display_name"] == "Alice")
        assert "+15551234567" in alice_chat["participants"]
        assert messages[0]["sender"] == "+15551234567"
