"""Tests for imvault.cli — Click command interface."""

from unittest.mock import patch
import sqlite3

import pytest
from click.testing import CliRunner

from imvault.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


class TestVersion:
    def test_version_flag(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "imvault" in result.output


class TestListCommand:
    def test_list_chats(self, runner, mock_chat_db):
        result = runner.invoke(cli, ["--db-path", mock_chat_db, "list"])
        assert result.exit_code == 0
        assert "Alice" in result.output
        assert "Group Chat" in result.output
        assert "conversation(s) total" in result.output

    def test_list_db_not_found(self, runner, tmp_path):
        db_path = str(tmp_path / "nonexistent.db")
        result = runner.invoke(cli, ["--db-path", db_path, "list"])
        assert result.exit_code != 0
        assert "Error" in result.output

    def test_list_shows_message_counts(self, runner, mock_chat_db):
        result = runner.invoke(cli, ["--db-path", mock_chat_db, "list"])
        assert result.exit_code == 0
        # Should contain numeric message counts
        assert "Messages" in result.output


class TestExportCommand:
    def test_export_all(self, runner, mock_chat_db, tmp_path):
        output = str(tmp_path / "export.imv")
        result = runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--all", "-o", output],
            input="testpass\ntestpass\n",
        )
        assert result.exit_code == 0
        assert "Archive saved" in result.output

    def test_export_single_chat(self, runner, mock_chat_db, tmp_path):
        output = str(tmp_path / "single.imv")
        result = runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--chat", "1", "-o", output],
            input="testpass\ntestpass\n",
        )
        assert result.exit_code == 0
        assert "Archive saved" in result.output

    def test_export_invalid_chat_id(self, runner, mock_chat_db, tmp_path):
        output = str(tmp_path / "bad.imv")
        result = runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--chat", "999", "-o", output],
            input="testpass\ntestpass\n",
        )
        assert result.exit_code != 0

    def test_export_db_not_found(self, runner, tmp_path):
        db_path = str(tmp_path / "nope.db")
        output = str(tmp_path / "export.imv")
        result = runner.invoke(
            cli,
            ["--db-path", db_path, "export", "--all", "-o", output],
            input="testpass\ntestpass\n",
        )
        assert result.exit_code != 0


class TestMergeCommand:
    def test_merge_archives(self, runner, mock_chat_db, tmp_path):
        archive_one = str(tmp_path / "one.imv")
        archive_two = str(tmp_path / "two.imv")
        merged = str(tmp_path / "merged.imv")

        result = runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--chat", "1", "-o", archive_one],
            input="testpass\ntestpass\n",
        )
        assert result.exit_code == 0

        result = runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--all", "-o", archive_two],
            input="testpass\ntestpass\n",
        )
        assert result.exit_code == 0

        result = runner.invoke(
            cli,
            ["merge", archive_one, archive_two, "-o", merged],
            input="testpass\nmergedpass\nmergedpass\n",
        )
        assert result.exit_code == 0
        assert "Merged archive saved" in result.output
        assert "duplicate messages removed" in result.output

    def test_merge_requires_two_archives(self, runner, tmp_path):
        archive = tmp_path / "one.imv"
        archive.write_bytes(b"not really an archive")

        result = runner.invoke(cli, ["merge", str(archive)], input="testpass\n")
        assert result.exit_code != 0
        assert "at least two archives" in result.output

    def test_merge_archive_with_current_database(self, runner, mock_chat_db, tmp_path):
        archive = str(tmp_path / "one.imv")
        merged = str(tmp_path / "merged.imv")

        result = runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--chat", "1", "-o", archive],
            input="testpass\ntestpass\n",
        )
        assert result.exit_code == 0

        result = runner.invoke(
            cli,
            [
                "--db-path",
                mock_chat_db,
                "merge",
                archive,
                "--with-current",
                "--all",
                "-o",
                merged,
            ],
            input="testpass\nmergedpass\nmergedpass\n",
        )
        assert result.exit_code == 0
        assert "plus 2 current conversation(s)" in result.output
        assert "3 duplicate messages removed" in result.output

    def test_merge_current_flags_require_with_current(self, runner, tmp_path):
        archive_one = tmp_path / "one.imv"
        archive_two = tmp_path / "two.imv"
        archive_one.write_bytes(b"not really an archive")
        archive_two.write_bytes(b"not really an archive")

        result = runner.invoke(
            cli,
            ["merge", str(archive_one), str(archive_two), "--all"],
            input="testpass\n",
        )
        assert result.exit_code != 0
        assert "can only be used with --with-current" in result.output


class TestViewCommand:
    def test_view_nonexistent_file(self, runner, tmp_path):
        result = runner.invoke(
            cli,
            ["view", str(tmp_path / "nonexistent.imv")],
            input="testpass\n",
        )
        assert result.exit_code != 0

    def test_view_wrong_password(self, runner, mock_chat_db, tmp_path):
        # First create an archive
        output = str(tmp_path / "view_test.imv")
        runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--all", "-o", output],
            input="correct-pass\ncorrect-pass\n",
        )

        # Try to view with wrong password (mock the viewer to avoid HTTP server)
        with patch("imvault.viewer.view_archive") as mock_view:
            mock_view.side_effect = ValueError("Decryption failed — wrong password or corrupted archive.")
            result = runner.invoke(
                cli,
                ["view", output],
                input="wrong-pass\n",
            )
            assert result.exit_code != 0
            assert "Error" in result.output


class TestInspectCommand:
    def test_inspect_archive(self, runner, mock_chat_db, tmp_path):
        archive = str(tmp_path / "archive.imv")

        result = runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--all", "-o", archive],
            input="testpass\ntestpass\n",
        )
        assert result.exit_code == 0

        result = runner.invoke(
            cli,
            ["inspect", archive],
            input="testpass\n",
        )
        assert result.exit_code == 0
        assert "Chats:" in result.output
        assert "Messages:" in result.output
        assert "Attachments:" in result.output

    def test_inspect_by_chat(self, runner, mock_chat_db, tmp_path):
        archive = str(tmp_path / "archive.imv")

        result = runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--all", "-o", archive],
            input="testpass\ntestpass\n",
        )
        assert result.exit_code == 0

        result = runner.invoke(
            cli,
            ["inspect", archive, "--by-chat"],
            input="testpass\n",
        )
        assert result.exit_code == 0
        assert "Alice" in result.output
        assert "Group Chat" in result.output

    def test_inspect_compare_attachments(self, runner, mock_chat_db, tmp_path):
        source = str(tmp_path / "source.imv")
        target = str(tmp_path / "target.imv")
        attachment_path = tmp_path / "photo.jpg"
        attachment_path.write_bytes(b"photo bytes")

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

        result = runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--chat", "1", "-o", source],
            input="testpass\ntestpass\n",
        )
        assert result.exit_code == 0

        result = runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--all", "-o", target],
            input="testpass\ntestpass\n",
        )
        assert result.exit_code == 0

        result = runner.invoke(
            cli,
            ["inspect", source, target, "--compare-attachments"],
            input="testpass\n",
        )
        assert result.exit_code == 0
        assert "Attachment containment" in result.output
        assert "Source attachments present in target: 1" in result.output
        assert "Missing from target: 0" in result.output

    def test_inspect_compare_attachments_requires_two_archives(self, runner, tmp_path):
        archive = tmp_path / "archive.imv"
        archive.write_bytes(b"not an archive")

        result = runner.invoke(
            cli,
            ["inspect", str(archive), "--compare-attachments"],
            input="testpass\n",
        )
        assert result.exit_code != 0
        assert "requires exactly two archives" in result.output
