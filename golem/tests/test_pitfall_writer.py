"""Tests for golem.pitfall_writer — full coverage."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from golem.pitfall_writer import (
    _HEADER,
    _SECTION_COMMENT,
    _SECTION_START,
    format_pitfalls_section,
    parse_pitfalls_section,
    update_agents_md,
)


def test_parse_pitfalls_section_empty():
    before, pitfalls, after = parse_pitfalls_section("")
    assert before == ""
    assert pitfalls == []
    assert after == ""


def test_parse_pitfalls_section_with_pitfalls():
    content = (
        _SECTION_START + _SECTION_COMMENT + "- first pitfall\n" + "- second pitfall\n"
    )
    before, pitfalls, after = parse_pitfalls_section(content)
    assert before == ""
    assert pitfalls == ["first pitfall", "second pitfall"]
    assert after == ""


def test_parse_pitfalls_section_preserves_other_content():
    content = (
        "# Header\n"
        "Some intro text\n"
        + _SECTION_START
        + _SECTION_COMMENT
        + "- pitfall one\n"
        + "## Other Section\n"
        + "Other content\n"
    )
    before, pitfalls, after = parse_pitfalls_section(content)
    assert "# Header\n" in before
    assert pitfalls == ["pitfall one"]
    assert "## Other Section\n" in after
    assert "Other content\n" in after


def test_format_pitfalls_section():
    pitfalls = ["pitfall a", "pitfall b"]
    result = format_pitfalls_section(pitfalls)
    assert result.startswith(_SECTION_START)
    assert _SECTION_COMMENT in result
    assert "- pitfall a\n" in result
    assert "- pitfall b\n" in result


def test_update_creates_new_file(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    update_agents_md(["new pitfall here"], agents_md_path=agents_md)
    assert agents_md.exists()
    content = agents_md.read_text()
    assert "new pitfall here" in content
    assert _SECTION_START in content


def test_update_appends_to_existing(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(
        _HEADER + _SECTION_START + _SECTION_COMMENT + "- existing pitfall\n"
    )
    update_agents_md(["brand new pitfall"], agents_md_path=agents_md)
    content = agents_md.read_text()
    assert "existing pitfall" in content
    assert "brand new pitfall" in content


def test_update_dedup_against_existing(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(
        _HEADER
        + _SECTION_START
        + _SECTION_COMMENT
        + "- always run black before committing code\n"
    )
    # This is similar enough (>= 0.6 token overlap) to be a duplicate
    update_agents_md(["always run black before committing"], agents_md_path=agents_md)
    content = agents_md.read_text()
    # Should not add another line with near-duplicate
    pitfall_lines = [line for line in content.splitlines() if line.startswith("- ")]
    assert len(pitfall_lines) == 1


def test_update_preserves_other_sections(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(
        "# Title\n"
        "Intro\n"
        + _SECTION_START
        + _SECTION_COMMENT
        + "- old pitfall\n"
        + "## Other\n"
        + "Keep this\n"
    )
    update_agents_md(["new pitfall added"], agents_md_path=agents_md)
    content = agents_md.read_text()
    assert "# Title\n" in content
    assert "Intro\n" in content
    assert "## Other\n" in content
    assert "Keep this\n" in content
    assert "new pitfall added" in content


def test_update_atomic_write(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    replace_calls = []
    original_replace = __import__("os").replace

    def mock_replace(src, dst):
        replace_calls.append((src, dst))
        return original_replace(src, dst)

    with patch("golem.pitfall_writer.os.replace", side_effect=mock_replace):
        update_agents_md(["a new pitfall"], agents_md_path=agents_md)

    assert len(replace_calls) == 1
    src, dst = replace_calls[0]
    assert str(dst) == str(agents_md)
    # Source should be a temp file (different from destination)
    assert src != dst


def test_update_empty_pitfalls(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    update_agents_md([], agents_md_path=agents_md)
    assert not agents_md.exists()


def test_update_cleans_up_on_write_error(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    with patch("golem.pitfall_writer.fcntl.flock", side_effect=OSError("lock error")):
        with pytest.raises(OSError, match="lock error"):
            update_agents_md(["will fail"], agents_md_path=agents_md)
    assert not agents_md.exists()
    # Also verify no temp files remain
    temp_files = list(tmp_path.glob(".agents_md_*"))
    assert temp_files == []


def test_update_cleans_up_ignores_unlink_error(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    with patch("golem.pitfall_writer.fcntl.flock", side_effect=OSError("lock error")):
        with patch(
            "golem.pitfall_writer.os.unlink", side_effect=OSError("unlink failed")
        ):
            with pytest.raises(OSError, match="lock error"):
                update_agents_md(["will fail"], agents_md_path=agents_md)


def test_integration_mock_session(tmp_path):
    """Mock a completed session, extract pitfalls, verify AGENTS.md updated."""
    from golem.pitfall_extractor import extract_pitfalls

    session_dict = {
        "validation_concerns": ["Always write tests before code"],
        "validation_test_failures": [],
        "errors": [],
        "retry_count": 0,
        "validation_summary": "",
    }
    pitfalls = extract_pitfalls([session_dict])
    assert pitfalls

    agents_md = tmp_path / "AGENTS.md"
    update_agents_md(pitfalls, agents_md_path=agents_md)

    content = agents_md.read_text()
    assert "always write tests before code" in content
    assert _SECTION_START in content
