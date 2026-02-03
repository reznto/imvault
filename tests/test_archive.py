"""Tests for imvault.archive — archive assembly."""

import io
import json
import tarfile

import pytest

from imvault.archive import ArchiveBuilder
from imvault.crypto import decrypt_archive
from imvault.db import IMMessageDB


class TestSingleChatArchive:
    """Test single-chat archive generation."""

    def test_creates_imv_file(self, mock_chat_db, tmp_path):
        output = str(tmp_path / "test.imv")
        password = "test-pw"

        with IMMessageDB(mock_chat_db) as db:
            chats = db.list_chats()
            chat_id = chats[0]["chat_id"]
            builder = ArchiveBuilder(db, password, output, [chat_id])
            result = builder.build()

        assert result == output
        with open(output, "rb") as f:
            data = f.read()
        assert data[:8] == b"IMVAULT1"

    def test_archive_contains_index_html(self, mock_chat_db, tmp_path):
        output = str(tmp_path / "test.imv")
        password = "test-pw"

        with IMMessageDB(mock_chat_db) as db:
            chats = db.list_chats()
            chat_id = chats[0]["chat_id"]
            builder = ArchiveBuilder(db, password, output, [chat_id])
            builder.build()

        with open(output, "rb") as f:
            encrypted = f.read()
        tar_gz = decrypt_archive(encrypted, password)

        with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
            names = tf.getnames()

        assert "index.html" in names
        assert "data.json" in names

    def test_data_json_schema(self, mock_chat_db, tmp_path):
        output = str(tmp_path / "test.imv")
        password = "test-pw"

        with IMMessageDB(mock_chat_db) as db:
            chats = db.list_chats()
            chat_id = chats[0]["chat_id"]
            builder = ArchiveBuilder(db, password, output, [chat_id])
            builder.build()

        with open(output, "rb") as f:
            encrypted = f.read()
        tar_gz = decrypt_archive(encrypted, password)

        with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
            data_file = tf.extractfile("data.json")
            data = json.loads(data_file.read())

        assert "chat" in data
        assert "messages" in data
        assert isinstance(data["messages"], list)
        assert len(data["messages"]) > 0

        msg = data["messages"][0]
        assert "rowid" in msg
        assert "guid" in msg
        assert "text" in msg
        assert "date" in msg
        assert "is_from_me" in msg
        assert "sender" in msg
        assert "reactions" in msg
        assert "attachments" in msg


class TestMultiChatArchive:
    """Test multi-chat archive generation."""

    def test_creates_multi_chat_archive(self, mock_chat_db, tmp_path):
        output = str(tmp_path / "multi.imv")
        password = "test-pw"

        with IMMessageDB(mock_chat_db) as db:
            chats = db.list_chats()
            all_ids = [c["chat_id"] for c in chats]
            builder = ArchiveBuilder(db, password, output, all_ids)
            builder.build()

        with open(output, "rb") as f:
            encrypted = f.read()
        tar_gz = decrypt_archive(encrypted, password)

        with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
            names = tf.getnames()

        assert "index.html" in names
        assert "manifest.json" in names

    def test_manifest_json_schema(self, mock_chat_db, tmp_path):
        output = str(tmp_path / "multi.imv")
        password = "test-pw"

        with IMMessageDB(mock_chat_db) as db:
            chats = db.list_chats()
            all_ids = [c["chat_id"] for c in chats]
            builder = ArchiveBuilder(db, password, output, all_ids)
            builder.build()

        with open(output, "rb") as f:
            encrypted = f.read()
        tar_gz = decrypt_archive(encrypted, password)

        with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
            manifest_file = tf.extractfile("manifest.json")
            manifest = json.loads(manifest_file.read())

        assert isinstance(manifest, list)
        assert len(manifest) == 2

        for entry in manifest:
            assert "chat_id" in entry
            assert "display_name" in entry
            assert "message_count" in entry
            assert "data_path" in entry

    def test_each_chat_has_data_json(self, mock_chat_db, tmp_path):
        output = str(tmp_path / "multi.imv")
        password = "test-pw"

        with IMMessageDB(mock_chat_db) as db:
            chats = db.list_chats()
            all_ids = [c["chat_id"] for c in chats]
            builder = ArchiveBuilder(db, password, output, all_ids)
            builder.build()

        with open(output, "rb") as f:
            encrypted = f.read()
        tar_gz = decrypt_archive(encrypted, password)

        with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
            manifest_file = tf.extractfile("manifest.json")
            manifest = json.loads(manifest_file.read())

            for entry in manifest:
                data_file = tf.extractfile(entry["data_path"])
                assert data_file is not None
                chat_data = json.loads(data_file.read())
                assert "chat" in chat_data
                assert "messages" in chat_data


class TestArchiveDecryptRoundTrip:
    """Test that archives can be encrypted and decrypted."""

    def test_round_trip(self, mock_chat_db, tmp_path):
        output = str(tmp_path / "roundtrip.imv")
        password = "round-trip-test"

        with IMMessageDB(mock_chat_db) as db:
            chats = db.list_chats()
            builder = ArchiveBuilder(db, password, output, [chats[0]["chat_id"]])
            builder.build()

        with open(output, "rb") as f:
            encrypted = f.read()

        tar_gz = decrypt_archive(encrypted, password)

        # Verify it's a valid tar.gz
        with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
            assert len(tf.getnames()) > 0
