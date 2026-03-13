"""Tests for golem.pitfall_writer — full coverage."""

from pathlib import Path
from unittest.mock import patch

import pytest

from golem.pitfall_extractor import (
    CATEGORY_ANTIPATTERNS,
    CATEGORY_ARCHITECTURE,
    CATEGORY_COVERAGE,
)
from golem.pitfall_writer import (
    _AUTO_COMMENT,
    _HEADER,
    _LEGACY_COMMENT,
    _LEGACY_SECTION,
    _parse_section_bullets,
    _preamble,
    format_agents_md,
    parse_agents_md,
    update_agents_md,
)

# -- _parse_section_bullets --------------------------------------------------


def test_parse_section_bullets_missing():
    items, remaining = _parse_section_bullets("no header here", "## Missing\n")
    assert items == []
    assert remaining == "no header here"


def test_parse_section_bullets_basic():
    content = "## Foo\n- item one\n- item two\n"
    items, remaining = _parse_section_bullets(content, "## Foo\n")
    assert items == ["item one", "item two"]
    assert remaining == ""


def test_parse_section_bullets_strips_auto_comment():
    content = "## Foo\n" + _AUTO_COMMENT + "- item\n"
    items, _ = _parse_section_bullets(content, "## Foo\n")
    assert items == ["item"]


def test_parse_section_bullets_strips_legacy_comment():
    content = "## Foo\n" + _LEGACY_COMMENT + "- item\n"
    items, _ = _parse_section_bullets(content, "## Foo\n")
    assert items == ["item"]


def test_parse_section_bullets_stops_at_next_header():
    content = "## Foo\n- item\n## Bar\nother\n"
    items, remaining = _parse_section_bullets(content, "## Foo\n")
    assert items == ["item"]
    assert "## Bar\n" in remaining
    assert "other\n" in remaining


def test_parse_section_bullets_preserves_before():
    content = "Preamble\n## Foo\n- item\n"
    items, remaining = _parse_section_bullets(content, "## Foo\n")
    assert items == ["item"]
    assert "Preamble\n" in remaining


# -- parse_agents_md ---------------------------------------------------------


def test_parse_agents_md_empty():
    result = parse_agents_md("")
    assert all(v == [] for v in result.values())


def test_parse_agents_md_categorized():
    content = (
        _HEADER
        + "\n"
        + "## Recurring Antipatterns\n"
        + "- antipattern: dead code\n"
        + "\n"
        + "## Coverage & Verification Gaps\n"
        + "- no independent verification was run\n"
        + "\n"
        + "## Architecture Notes\n"
        + "- cross-module private access\n"
    )
    result = parse_agents_md(content)
    assert result[CATEGORY_ANTIPATTERNS] == ["antipattern: dead code"]
    assert result[CATEGORY_COVERAGE] == ["no independent verification was run"]
    assert result[CATEGORY_ARCHITECTURE] == ["cross-module private access"]


def test_parse_agents_md_legacy_migration():
    """Legacy '## Known Pitfalls' entries get classified into categories."""
    content = (
        _HEADER
        + _LEGACY_SECTION
        + _LEGACY_COMMENT
        + "- antipattern: empty exception handler\n"
        + "- no independent verification was run\n"
        + "- cross-module private access in writer\n"
    )
    result = parse_agents_md(content)
    assert "antipattern: empty exception handler" in result[CATEGORY_ANTIPATTERNS]
    assert "no independent verification was run" in result[CATEGORY_COVERAGE]
    assert "cross-module private access in writer" in result[CATEGORY_ARCHITECTURE]


# -- _preamble ---------------------------------------------------------------


def test_preamble_with_header():
    content = _HEADER + "\n" + "## Recurring Antipatterns\n- item\n"
    result = _preamble(content)
    assert result == _HEADER + "\n"


def test_preamble_with_legacy():
    content = _HEADER + _LEGACY_SECTION + "- item\n"
    result = _preamble(content)
    assert result == _HEADER


def test_preamble_no_sections():
    content = "Just some text\n"
    result = _preamble(content)
    assert result == content


# -- format_agents_md --------------------------------------------------------


def test_format_agents_md_all_categories():
    categorized = {
        CATEGORY_ANTIPATTERNS: ["dead code found"],
        CATEGORY_COVERAGE: ["missing tests"],
        CATEGORY_ARCHITECTURE: ["cross-module issue"],
    }
    result = format_agents_md(_HEADER, categorized)
    assert "## Recurring Antipatterns\n" in result
    assert "- dead code found\n" in result
    assert "## Coverage & Verification Gaps\n" in result
    assert "- missing tests\n" in result
    assert "## Architecture Notes\n" in result
    assert "- cross-module issue\n" in result


def test_format_agents_md_empty_categories_omitted():
    categorized = {
        CATEGORY_ANTIPATTERNS: ["item"],
        CATEGORY_COVERAGE: [],
        CATEGORY_ARCHITECTURE: [],
    }
    result = format_agents_md(_HEADER, categorized)
    assert "## Recurring Antipatterns\n" in result
    assert "## Coverage" not in result
    assert "## Architecture" not in result


def test_format_agents_md_no_preamble():
    categorized = {CATEGORY_ANTIPATTERNS: ["item"]}
    result = format_agents_md("", categorized)
    assert result.startswith(_HEADER)
    assert _AUTO_COMMENT in result


# -- update_agents_md --------------------------------------------------------


def test_update_creates_new_file(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    update_agents_md(
        ["antipattern: empty exception handler in module"],
        agents_md_path=agents_md,
    )
    assert agents_md.exists()
    content = agents_md.read_text()
    assert "antipattern: empty exception handler in module" in content
    assert "## Recurring Antipatterns\n" in content


def test_update_appends_to_existing(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(
        _HEADER
        + "\n"
        + "## Recurring Antipatterns\n"
        + "- existing antipattern entry\n"
    )
    update_agents_md(
        ["antipattern: brand new pattern found"],
        agents_md_path=agents_md,
    )
    content = agents_md.read_text()
    assert "existing antipattern entry" in content
    assert "antipattern: brand new pattern found" in content


def test_update_dedup_against_existing(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(
        _HEADER
        + "\n"
        + "## Recurring Antipatterns\n"
        + "- antipattern: always run black before committing code\n"
    )
    # Similar enough (>= 0.6 token overlap) to be a duplicate
    update_agents_md(
        ["antipattern: always run black before committing"],
        agents_md_path=agents_md,
    )
    content = agents_md.read_text()
    pitfall_lines = [line for line in content.splitlines() if line.startswith("- ")]
    assert len(pitfall_lines) == 1


def test_update_categorizes_new_pitfalls(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    update_agents_md(
        [
            "antipattern: dead code after return statement",
            "no independent verification was run for this module",
            "cross-module private access in writer module",
        ],
        agents_md_path=agents_md,
    )
    content = agents_md.read_text()
    assert "## Recurring Antipatterns\n" in content
    assert "## Coverage & Verification Gaps\n" in content
    assert "## Architecture Notes\n" in content


def test_update_preserves_preamble(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(
        "# Custom Title\nCustom intro\n\n"
        + "## Recurring Antipatterns\n"
        + "- old item\n"
    )
    update_agents_md(
        ["antipattern: new item found here"],
        agents_md_path=agents_md,
    )
    content = agents_md.read_text()
    assert content.startswith("# Custom Title\nCustom intro\n")
    assert "antipattern: new item found here" in content


def test_update_atomic_write(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    replace_calls = []
    original_replace = __import__("os").replace

    def mock_replace(src, dst):
        replace_calls.append((src, dst))
        return original_replace(src, dst)

    with patch("golem.pitfall_writer.os.replace", side_effect=mock_replace):
        update_agents_md(
            ["antipattern: a new pitfall for testing"],
            agents_md_path=agents_md,
        )

    assert len(replace_calls) == 1
    src, dst = replace_calls[0]
    assert str(dst) == str(agents_md)
    assert src != dst


def test_update_empty_pitfalls(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    update_agents_md([], agents_md_path=agents_md)
    assert not agents_md.exists()


def test_update_cleans_up_temp_on_replace_error(tmp_path):
    """When os.replace fails inside the lock, temp file is removed and error re-raised."""
    agents_md = tmp_path / "AGENTS.md"
    with patch(
        "golem.pitfall_writer.os.replace", side_effect=OSError("replace failed")
    ):
        with pytest.raises(OSError, match="replace failed"):
            update_agents_md(
                ["antipattern: will fail on replace"],
                agents_md_path=agents_md,
            )
    assert not agents_md.exists()
    temp_files = list(tmp_path.glob(".agents_md_*"))
    assert temp_files == []


def test_update_cleans_up_temp_ignores_unlink_error_on_replace_fail(tmp_path):
    """When both os.replace and os.unlink fail, the original error propagates."""
    agents_md = tmp_path / "AGENTS.md"
    with (
        patch(
            "golem.pitfall_writer.os.replace",
            side_effect=OSError("replace failed"),
        ),
        patch(
            "golem.pitfall_writer.os.unlink",
            side_effect=OSError("unlink failed"),
        ),
    ):
        with pytest.raises(OSError, match="replace failed"):
            update_agents_md(
                ["antipattern: will fail on replace and unlink"],
                agents_md_path=agents_md,
            )


def test_update_migrates_legacy_format(tmp_path):
    """Legacy '## Known Pitfalls' gets migrated to categorized sections."""
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(
        _HEADER
        + _LEGACY_SECTION
        + _LEGACY_COMMENT
        + "- antipattern: empty exception handler in module\n"
        + "- no independent verification was run for task\n"
    )
    update_agents_md(
        ["cross-module private access in writer module"],
        agents_md_path=agents_md,
    )
    content = agents_md.read_text()
    # Legacy section should be gone, replaced by categories
    assert _LEGACY_SECTION not in content
    assert "## Recurring Antipatterns\n" in content
    assert "## Coverage & Verification Gaps\n" in content
    assert "## Architecture Notes\n" in content


def test_integration_mock_session(tmp_path):
    """Mock a completed session, extract pitfalls, verify AGENTS.md updated."""
    from golem.pitfall_extractor import extract_pitfalls

    session_dict = {
        "validation_concerns": ["antipattern: dead code after return in module"],
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
    assert "antipattern: dead code after return in module" in content
    assert "## Recurring Antipatterns\n" in content
