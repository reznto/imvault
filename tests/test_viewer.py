"""Tests for imvault.viewer."""

from imvault.viewer import _refresh_reader_template


def test_refresh_reader_template_replaces_single_archive_index(tmp_path):
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")
    (tmp_path / "index.html").write_text("old", encoding="utf-8")

    _refresh_reader_template(str(tmp_path))

    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "dateJump" in index
    assert "monthJump" in index


def test_refresh_reader_template_replaces_multi_archive_index(tmp_path):
    (tmp_path / "manifest.json").write_text("[]", encoding="utf-8")
    (tmp_path / "index.html").write_text("old", encoding="utf-8")

    _refresh_reader_template(str(tmp_path))

    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "sidebarSearch" in index
    assert "dateJump" in index
