"""Tests for imvault.archive — archive assembly."""

import io
import json
import sqlite3
import tarfile

import pytest

from imvault.archive import ArchiveBuilder
from imvault.archive import MergedArchiveBuilder
from imvault.archive import _make_attachment_ref
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


class TestMergedArchive:
    """Test deduplicated archive merging."""

    def test_merge_overlapping_archives_dedupes_messages(self, mock_chat_db, tmp_path):
        password = "test-pw"
        archive_one = str(tmp_path / "one.imv")
        archive_two = str(tmp_path / "two.imv")
        merged = str(tmp_path / "merged.imv")

        with IMMessageDB(mock_chat_db) as db:
            ArchiveBuilder(db, password, archive_one, [1]).build()
            ArchiveBuilder(db, password, archive_two, [1, 2]).build()

        builder = MergedArchiveBuilder(
            [(archive_one, password), (archive_two, password)],
            password,
            merged,
        )
        builder.build()

        with open(merged, "rb") as f:
            encrypted = f.read()
        tar_gz = decrypt_archive(encrypted, password)

        with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
            manifest = json.loads(tf.extractfile("manifest.json").read())
            all_messages = []
            for entry in manifest:
                chat_data = json.loads(tf.extractfile(entry["data_path"]).read())
                all_messages.extend(chat_data["messages"])

        guids = [message["guid"] for message in all_messages]
        assert len(guids) == 6
        assert len(guids) == len(set(guids))
        assert builder.stats["duplicates"] == 3

    def test_merge_archive_with_current_dedupes_same_attachment(self, mock_chat_db, tmp_path):
        password = "test-pw"
        archive_one = str(tmp_path / "one.imv")
        merged = str(tmp_path / "merged.imv")
        attachment_path = tmp_path / "photo.jpg"
        attachment_path.write_bytes(b"same attachment bytes")

        conn = sqlite3.connect(mock_chat_db)
        conn.execute(
            "INSERT INTO attachment (ROWID, filename, mime_type, transfer_name) VALUES (?, ?, ?, ?)",
            (1, str(attachment_path), "image/jpeg", "photo.jpg"),
        )
        conn.execute(
            "INSERT INTO message_attachment_join (message_id, attachment_id) VALUES (?, ?)",
            (1, 1),
        )
        conn.execute("UPDATE message SET cache_has_attachments = 1 WHERE ROWID = 1")
        conn.commit()
        conn.close()

        with IMMessageDB(mock_chat_db) as db:
            ArchiveBuilder(db, password, archive_one, [1]).build()

        with IMMessageDB(mock_chat_db) as db:
            MergedArchiveBuilder(
                [(archive_one, password)],
                password,
                merged,
                current_db=db,
                current_chat_ids=[1],
            ).build()

        with open(merged, "rb") as f:
            encrypted = f.read()
        tar_gz = decrypt_archive(encrypted, password)

        with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
            chat_data = json.loads(tf.extractfile("data.json").read())
            first = chat_data["messages"][0]
            attachment_members = [name for name in tf.getnames() if name.startswith("attachments/")]

        assert first["guid"] == "msg-001"
        assert len(first["attachments"]) == 1
        assert len(attachment_members) == 1

    def test_merge_missing_current_attachment_does_not_replace_archived_copy(
        self,
        mock_chat_db,
        tmp_path,
    ):
        password = "test-pw"
        archive_one = str(tmp_path / "one.imv")
        merged = str(tmp_path / "merged.imv")
        attachment_path = tmp_path / "photo.jpg"
        attachment_path.write_bytes(b"archived attachment bytes")

        conn = sqlite3.connect(mock_chat_db)
        conn.execute(
            "INSERT INTO attachment (ROWID, filename, mime_type, transfer_name) VALUES (?, ?, ?, ?)",
            (1, str(attachment_path), "image/jpeg", "photo.jpg"),
        )
        conn.execute(
            "INSERT INTO message_attachment_join (message_id, attachment_id) VALUES (?, ?)",
            (1, 1),
        )
        conn.execute("UPDATE message SET cache_has_attachments = 1 WHERE ROWID = 1")
        conn.commit()
        conn.close()

        with IMMessageDB(mock_chat_db) as db:
            ArchiveBuilder(db, password, archive_one, [1]).build()

        attachment_path.unlink()

        with IMMessageDB(mock_chat_db) as db:
            MergedArchiveBuilder(
                [(archive_one, password)],
                password,
                merged,
                current_db=db,
                current_chat_ids=[1],
            ).build()

        with open(merged, "rb") as f:
            encrypted = f.read()
        tar_gz = decrypt_archive(encrypted, password)

        with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
            chat_data = json.loads(tf.extractfile("data.json").read())
            first = chat_data["messages"][0]
            attachment_members = [name for name in tf.getnames() if name.startswith("attachments/")]

        assert first["guid"] == "msg-001"
        assert len(first["attachments"]) == 1
        assert len(attachment_members) == 1

    def test_merge_writer_removes_duplicate_attachment_refs(self, tmp_path):
        ref = _make_attachment_ref(
            data=b"same bytes",
            mime_type="image/jpeg",
            transfer_name="photo.jpg",
            original_path="attachments/photo.jpg",
        )
        builder = MergedArchiveBuilder([], "pw", str(tmp_path / "unused.imv"))

        tar_gz = builder._build_tar([{
            "chat": {"chat_id": 1, "display_name": "Alice"},
            "messages": [{
                "rowid": 1,
                "guid": "msg-001",
                "text": "photo",
                "date": "2024-01-15T12:00:00",
                "is_from_me": False,
                "sender": "Alice",
                "reactions": [],
                "_attachment_refs": [ref, ref],
            }],
        }])

        with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
            chat_data = json.loads(tf.extractfile("data.json").read())
            attachment_members = [name for name in tf.getnames() if name.startswith("attachments/")]

        assert len(chat_data["messages"][0]["attachments"]) == 1
        assert len(attachment_members) == 1

    def test_merge_duplicate_reactions_are_combined(self, mock_chat_db, tmp_path):
        password = "test-pw"
        archive_one = str(tmp_path / "one.imv")
        archive_two = str(tmp_path / "two.imv")
        merged = str(tmp_path / "merged.imv")

        with IMMessageDB(mock_chat_db) as db:
            ArchiveBuilder(db, password, archive_one, [1]).build()
            ArchiveBuilder(db, password, archive_two, [1]).build()

        MergedArchiveBuilder(
            [(archive_one, password), (archive_two, password)],
            password,
            merged,
        ).build()

        with open(merged, "rb") as f:
            encrypted = f.read()
        tar_gz = decrypt_archive(encrypted, password)

        with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
            chat_data = json.loads(tf.extractfile("data.json").read())

        first = chat_data["messages"][0]
        assert first["guid"] == "msg-001"
        assert len(first["reactions"]) == 1

    def test_merge_archive_with_current_database(self, mock_chat_db, tmp_path):
        password = "test-pw"
        archive_one = str(tmp_path / "one.imv")
        merged = str(tmp_path / "merged.imv")

        with IMMessageDB(mock_chat_db) as db:
            ArchiveBuilder(db, password, archive_one, [1]).build()

        with IMMessageDB(mock_chat_db) as db:
            builder = MergedArchiveBuilder(
                [(archive_one, password)],
                password,
                merged,
                current_db=db,
                current_chat_ids=[1, 2],
            )
            builder.build()

        with open(merged, "rb") as f:
            encrypted = f.read()
        tar_gz = decrypt_archive(encrypted, password)

        with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
            manifest = json.loads(tf.extractfile("manifest.json").read())
            all_messages = []
            for entry in manifest:
                chat_data = json.loads(tf.extractfile(entry["data_path"]).read())
                all_messages.extend(chat_data["messages"])

        guids = [message["guid"] for message in all_messages]
        assert len(guids) == 6
        assert len(guids) == len(set(guids))
        assert builder.stats["duplicates"] == 3
