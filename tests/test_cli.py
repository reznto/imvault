"""Tests for imvault.cli — Click command interface."""

import json
import os
import sqlite3
import subprocess
import sys
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

    def test_view_passes_no_browser_flag(self, runner, mock_chat_db, tmp_path):
        output = str(tmp_path / "view_no_browser.imv")
        runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--all", "-o", output],
            input="pw\npw\n",
        )

        with patch("imvault.viewer.view_archive") as mock_view:
            mock_view.return_value = None
            result = runner.invoke(
                cli,
                ["view", "--no-browser", output],
                input="pw\n",
            )
            assert result.exit_code == 0
            assert mock_view.call_args.kwargs["no_browser"] is True
            assert mock_view.call_args.kwargs["progress_json"] is False

    def test_view_passes_progress_json_flag(self, runner, mock_chat_db, tmp_path):
        output = str(tmp_path / "view_progress.imv")
        runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--all", "-o", output],
            input="pw\npw\n",
        )

        with patch("imvault.viewer.view_archive") as mock_view:
            mock_view.return_value = None
            result = runner.invoke(
                cli,
                ["view", "--progress-json", output],
                input="pw\n",
            )
            assert result.exit_code == 0
            assert mock_view.call_args.kwargs["progress_json"] is True

    def test_view_progress_json_emits_json_error(
        self, runner, mock_chat_db, tmp_path
    ):
        output = str(tmp_path / "view_pj_err.imv")
        runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--all", "-o", output],
            input="pw\npw\n",
        )

        with patch("imvault.viewer.view_archive") as mock_view:
            mock_view.side_effect = ValueError("Decryption failed — wrong password")
            result = runner.invoke(
                cli,
                ["view", "--progress-json", output],
                input="wrong-pw\n",
            )
            assert result.exit_code != 0
            # With --progress-json, errors must come back as a JSON envelope on
            # stderr so the GUI can parse them.
            assert '"error"' in result.output
            assert '"Decryption failed' in result.output


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


def _piped_password_fd(*passwords: str) -> int:
    """Create a pipe, write the given passwords (one per line), return the read fd."""
    read_fd, write_fd = os.pipe()
    payload = "".join(f"{p}\n" for p in passwords).encode()
    os.write(write_fd, payload)
    os.close(write_fd)
    return read_fd


class TestListJson:
    def test_list_json_emits_array(self, runner, mock_chat_db):
        result = runner.invoke(cli, ["--db-path", mock_chat_db, "list", "--json"])
        assert result.exit_code == 0, result.output
        # Find the JSON line (filter out any other output)
        json_line = next(
            line for line in result.output.splitlines() if line.startswith("[")
        )
        chats = json.loads(json_line)
        assert isinstance(chats, list)
        assert len(chats) == 2
        for chat in chats:
            assert set(chat.keys()) == {
                "chat_id",
                "display_name",
                "participant_count",
                "message_count",
                "last_message_at",
            }
            assert isinstance(chat["chat_id"], int)
            assert isinstance(chat["participant_count"], int)
            assert isinstance(chat["message_count"], int)

        names = {c["display_name"] for c in chats}
        assert "Alice" in names
        assert "Group Chat" in names

    def test_list_json_db_not_found(self, runner, tmp_path):
        result = runner.invoke(
            cli, ["--db-path", str(tmp_path / "nope.db"), "list", "--json"]
        )
        assert result.exit_code != 0


class TestInspectJson:
    def test_inspect_json_emits_object(self, runner, mock_chat_db, tmp_path):
        archive = str(tmp_path / "archive.imv")

        result = runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--all", "-o", archive],
            input="testpass\ntestpass\n",
        )
        assert result.exit_code == 0

        result = runner.invoke(
            cli,
            ["inspect", archive, "--json"],
            input="testpass\n",
        )
        assert result.exit_code == 0, result.output
        json_line = next(
            line for line in result.output.splitlines() if line.startswith("{")
        )
        payload = json.loads(json_line)
        assert "archives" in payload
        assert len(payload["archives"]) == 1
        info = payload["archives"][0]
        assert info["chat_count"] == 2
        assert info["messages"] > 0
        # by_chat data is always present in info["chats"]
        assert isinstance(info["chats"], list)
        assert len(info["chats"]) == 2

    def test_inspect_json_compare_attachments(self, runner, mock_chat_db, tmp_path):
        source = str(tmp_path / "src.imv")
        target = str(tmp_path / "tgt.imv")

        runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--all", "-o", source],
            input="testpass\ntestpass\n",
        )
        runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--all", "-o", target],
            input="testpass\ntestpass\n",
        )

        result = runner.invoke(
            cli,
            ["inspect", source, target, "--compare-attachments", "--json"],
            input="testpass\n",
        )
        assert result.exit_code == 0, result.output
        json_line = next(
            line for line in result.output.splitlines() if line.startswith("{")
        )
        payload = json.loads(json_line)
        assert "compare_attachments" in payload
        cmp = payload["compare_attachments"]
        assert cmp["source"] == source
        assert cmp["target"] == target
        assert "missing_from_target" in cmp


class TestExportProgressJson:
    def test_progress_json_emits_events_on_stderr(self, runner, mock_chat_db, tmp_path):
        output = str(tmp_path / "p.imv")
        result = runner.invoke(
            cli,
            [
                "--db-path",
                mock_chat_db,
                "export",
                "--all",
                "-o",
                output,
                "--progress-json",
            ],
            input="testpass\ntestpass\n",
        )
        assert result.exit_code == 0, result.output

        events = []
        for line in result.stderr.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            events.append(json.loads(line))

        # We have 2 chats in the mock, so expect 2 chat_started + 2 chat_done at minimum.
        kinds = [e["event"] for e in events]
        assert kinds.count("chat_started") == 2
        assert kinds.count("chat_done") == 2

        # Each event has the required fields.
        for event in events:
            assert set(event.keys()) >= {"event", "chat_id", "processed", "total"}
            assert event["total"] == 2


class TestPasswordFd:
    def test_export_reads_password_from_fd(self, runner, mock_chat_db, tmp_path):
        output = str(tmp_path / "fd.imv")
        fd = _piped_password_fd("fdpass")
        try:
            result = runner.invoke(
                cli,
                [
                    "--db-path",
                    mock_chat_db,
                    "export",
                    "--all",
                    "-o",
                    output,
                    "--password-fd",
                    str(fd),
                ],
            )
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        assert result.exit_code == 0, result.output
        assert os.path.exists(output)
        assert "Archive saved" in result.output

    def test_export_password_fd_never_on_argv(
        self, runner, mock_chat_db, tmp_path
    ):
        """Sanity check: confirm we can drive export without putting the
        password into argv. The password lives only in the pipe."""
        output = str(tmp_path / "fd2.imv")
        fd = _piped_password_fd("super-secret-pw")
        try:
            result = runner.invoke(
                cli,
                [
                    "--db-path",
                    mock_chat_db,
                    "export",
                    "--all",
                    "-o",
                    output,
                    "--password-fd",
                    str(fd),
                ],
            )
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        assert result.exit_code == 0
        # The password must not appear in any CLI-emitted output either.
        assert "super-secret-pw" not in result.output

    def test_inspect_reads_password_from_fd(self, runner, mock_chat_db, tmp_path):
        archive = str(tmp_path / "fd_inspect.imv")

        result = runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--all", "-o", archive],
            input="testpass\ntestpass\n",
        )
        assert result.exit_code == 0

        fd = _piped_password_fd("testpass")
        try:
            result = runner.invoke(
                cli,
                ["inspect", archive, "--password-fd", str(fd)],
            )
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        assert result.exit_code == 0, result.output
        assert "Chats:" in result.output

    def test_view_reads_password_from_fd(self, runner, mock_chat_db, tmp_path):
        archive = str(tmp_path / "fd_view.imv")
        runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--all", "-o", archive],
            input="testpass\ntestpass\n",
        )

        fd = _piped_password_fd("testpass")
        try:
            with patch("imvault.viewer.view_archive") as mock_view:
                result = runner.invoke(
                    cli,
                    ["view", archive, "--password-fd", str(fd)],
                )
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        assert result.exit_code == 0, result.output
        assert mock_view.call_args.args[1] == "testpass"

    def test_merge_reads_passwords_from_fd(self, runner, mock_chat_db, tmp_path):
        archive_one = str(tmp_path / "m1.imv")
        archive_two = str(tmp_path / "m2.imv")
        merged = str(tmp_path / "m_out.imv")

        runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--chat", "1", "-o", archive_one],
            input="srcpass\nsrcpass\n",
        )
        runner.invoke(
            cli,
            ["--db-path", mock_chat_db, "export", "--all", "-o", archive_two],
            input="srcpass\nsrcpass\n",
        )

        # Shared input password (one line) + output password (one line).
        fd = _piped_password_fd("srcpass", "newpass")
        try:
            result = runner.invoke(
                cli,
                [
                    "merge",
                    archive_one,
                    archive_two,
                    "-o",
                    merged,
                    "--password-fd",
                    str(fd),
                ],
            )
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        assert result.exit_code == 0, result.output
        assert "Merged archive saved" in result.output

    def test_password_fd_empty_stream_errors(self, runner, mock_chat_db, tmp_path):
        # Close the write end immediately — no password available.
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        try:
            result = runner.invoke(
                cli,
                [
                    "--db-path",
                    mock_chat_db,
                    "export",
                    "--all",
                    "-o",
                    str(tmp_path / "empty.imv"),
                    "--password-fd",
                    str(read_fd),
                ],
            )
        finally:
            try:
                os.close(read_fd)
            except OSError:
                pass
        assert result.exit_code != 0
        assert "no password available" in result.output
