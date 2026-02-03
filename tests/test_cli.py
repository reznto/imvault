"""Tests for imvault.cli — Click command interface."""

from unittest.mock import patch

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
