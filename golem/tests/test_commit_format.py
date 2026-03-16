"""Tests for core.commit_format and golem.committer modules."""

# pylint: disable=missing-class-docstring,missing-function-docstring

from textwrap import dedent
from unittest.mock import patch

import pytest

from golem.core.commit_format import (
    CommitFormat,
    _clear_cache,
    _parse,
    load_commit_format,
)


class TestCommitFormat:
    def test_main_tags_str(self):
        fmt = CommitFormat(
            main_tags=("A", "B", "C"),
            sub_tags_hw=(),
            sub_tags_areas=(),
            sub_tags_chips=(),
        )
        assert fmt.main_tags_str == "A, B, C"

    def test_sub_tags_hw_str(self):
        fmt = CommitFormat(
            main_tags=(),
            sub_tags_hw=("X", "Y"),
            sub_tags_areas=(),
            sub_tags_chips=(),
        )
        assert fmt.sub_tags_hw_str == "X, Y"

    def test_sub_tags_areas_str(self):
        fmt = CommitFormat(
            main_tags=(),
            sub_tags_hw=(),
            sub_tags_areas=("P", "Q"),
            sub_tags_chips=(),
        )
        assert fmt.sub_tags_areas_str == "P, Q"

    def test_sub_tags_chips_str(self):
        fmt = CommitFormat(
            main_tags=(),
            sub_tags_hw=(),
            sub_tags_areas=(),
            sub_tags_chips=("AGUR", "QTM5"),
        )
        assert fmt.sub_tags_chips_str == "AGUR, QTM5"

    def test_empty_tuples_give_empty_strings(self):
        fmt = CommitFormat(
            main_tags=(),
            sub_tags_hw=(),
            sub_tags_areas=(),
            sub_tags_chips=(),
        )
        assert fmt.main_tags_str == ""
        assert fmt.sub_tags_hw_str == ""
        assert fmt.sub_tags_areas_str == ""
        assert fmt.sub_tags_chips_str == ""

    def test_prompt_vars_keys(self):
        fmt = CommitFormat(
            main_tags=("FEATURE",),
            sub_tags_hw=("ALM",),
            sub_tags_areas=("CORE",),
            sub_tags_chips=("AGUR",),
        )
        v = fmt.prompt_vars()
        assert set(v.keys()) == {
            "main_tags",
            "sub_tags_hw",
            "sub_tags_areas",
            "sub_tags_chips",
        }
        assert v["main_tags"] == "FEATURE"
        assert v["sub_tags_hw"] == "ALM"
        assert v["sub_tags_areas"] == "CORE"
        assert v["sub_tags_chips"] == "AGUR"

    def test_frozen(self):
        fmt = CommitFormat(
            main_tags=("A",),
            sub_tags_hw=(),
            sub_tags_areas=(),
            sub_tags_chips=(),
        )
        with pytest.raises(AttributeError):
            fmt.main_tags = ("B",)  # type: ignore[misc]


class TestParse:
    def test_full_yaml(self):
        raw = {
            "main_tags": ["FEATURE", "BUG", "FIX"],
            "sub_tags": {
                "hardware": ["ALM", "FTO"],
                "areas": ["CORE", "INFRA"],
                "chips": ["AGUR"],
            },
        }
        fmt = _parse(raw)
        assert fmt.main_tags == ("FEATURE", "BUG", "FIX")
        assert fmt.sub_tags_hw == ("ALM", "FTO")
        assert fmt.sub_tags_areas == ("CORE", "INFRA")
        assert fmt.sub_tags_chips == ("AGUR",)

    def test_missing_sub_tags(self):
        raw = {"main_tags": ["A"]}
        fmt = _parse(raw)
        assert not fmt.sub_tags_hw
        assert not fmt.sub_tags_areas
        assert not fmt.sub_tags_chips

    def test_empty_dict(self):
        fmt = _parse({})
        assert not fmt.main_tags


class TestLoadCommitFormat:
    def test_loads_real_file(self):
        _clear_cache()
        fmt = load_commit_format()
        assert "FEATURE" in fmt.main_tags
        assert "BUG" in fmt.main_tags
        assert "FIX" in fmt.main_tags
        assert "DOC" not in fmt.main_tags, "DOC removed in favor of DOCS"
        assert "DOCS" in fmt.main_tags
        assert "CORE" in fmt.sub_tags_areas

    def test_missing_file_returns_empty(self, tmp_path):
        _clear_cache()
        fmt = load_commit_format(tmp_path / "nonexistent.yaml")
        assert not fmt.main_tags
        _clear_cache()

    def test_custom_file(self, tmp_path):
        _clear_cache()
        cfg = tmp_path / "custom.yaml"
        cfg.write_text(dedent("""\
            main_tags:
              - X
              - Y
            sub_tags:
              hardware:
                - HW1
              areas:
                - A1
            """))
        fmt = load_commit_format(cfg)
        assert fmt.main_tags == ("X", "Y")
        assert fmt.sub_tags_hw == ("HW1",)
        assert fmt.sub_tags_areas == ("A1",)
        _clear_cache()

    def test_oserror_on_mtime_returns_cached(self, tmp_path):
        _clear_cache()
        cfg = tmp_path / "cached.yaml"
        cfg.write_text("main_tags:\n  - CACHED\n")
        fmt1 = load_commit_format(cfg)
        assert fmt1.main_tags == ("CACHED",)

        with patch(
            "golem.core.commit_format.os.path.getmtime", side_effect=OSError("gone")
        ):
            fmt2 = load_commit_format(cfg)
        assert fmt2.main_tags == ("CACHED",)
        _clear_cache()

    def test_rereads_on_mtime_change(self, tmp_path):
        _clear_cache()
        cfg = tmp_path / "evolving.yaml"
        cfg.write_text("main_tags:\n  - OLD\n")
        fmt1 = load_commit_format(cfg)
        assert fmt1.main_tags == ("OLD",)

        # Overwrite with new content and force a distinct mtime so the
        # cache sees a change even on filesystems with 1-second granularity.
        cfg.write_text("main_tags:\n  - NEW\n")
        import os

        st = os.stat(cfg)
        os.utime(cfg, (st.st_atime, st.st_mtime + 2))

        fmt2 = load_commit_format(cfg)
        assert fmt2.main_tags == ("NEW",)
        _clear_cache()


# ---------------------------------------------------------------------------
# Tests for golem.committer.build_commit_message
# ---------------------------------------------------------------------------

_MOCK_FMT = CommitFormat(
    main_tags=("FEATURE", "BUG", "FIX", "DOCS", "CHORE", "REFACTOR", "PERF", "TEST"),
    sub_tags_hw=("ALM", "FTO", "HBR", "SMA", "I2C"),
    sub_tags_areas=("CORE", "INFRA", "CI", "IB", "ETH", "NVL", "DOCS"),
    sub_tags_chips=("AGUR", "NVL7", "QTM5"),
)


def _build(subject, task_type="code_change", issue_id=1, summary="done"):
    """Helper — call build_commit_message with the mock format."""
    with patch("golem.committer.load_commit_format", return_value=_MOCK_FMT):
        from golem.committer import build_commit_message

        return build_commit_message(issue_id, subject, task_type, summary)


class TestBuildCommitMessage:
    def test_basic_format(self):
        msg = _build("[AGENT] Fix ALM register init")
        first_line = msg.split("\n")[0]
        assert first_line.startswith("[FIX][ALM]")
        assert "[AGENT]" not in first_line

    def test_word_boundary_no_false_positive_ci(self):
        """'CI' must NOT match inside 'SPECIAL'."""
        msg = _build("Fix special handling of packets")
        first_line = msg.split("\n")[0]
        assert "[INFRA]" in first_line  # default fallback, not CI

    def test_word_boundary_no_false_positive_ib(self):
        """'IB' must NOT match inside 'PLIB'."""
        msg = _build("Update PLIB configuration")
        first_line = msg.split("\n")[0]
        # PLIB is an HW tag, so should match PLIB... but PLIB is not in our
        # mock. So it should fall back to INFRA, NOT pick IB.
        assert "[IB]" not in first_line

    def test_word_boundary_no_false_positive_eth(self):
        """'ETH' must NOT match inside 'SOMETHING'."""
        msg = _build("Fix something in the codebase")
        first_line = msg.split("\n")[0]
        assert "[ETH]" not in first_line

    def test_word_boundary_matches_exact_tag(self):
        """'IB' should match when it appears as a standalone word."""
        msg = _build("Fix IB packet flow issue")
        first_line = msg.split("\n")[0]
        assert "[IB]" in first_line

    def test_chip_tag_takes_priority_over_area(self):
        """'NVL7' (chip) should win over 'NVL' (area)."""
        msg = _build("Fix NVL7 reduction path")
        first_line = msg.split("\n")[0]
        assert "[NVL7]" in first_line

    def test_hw_tag_takes_priority_over_area(self):
        """HW tag 'ALM' should win over area 'CORE' when both present."""
        msg = _build("Fix CORE ALM register initialization")
        first_line = msg.split("\n")[0]
        # chips > hw > areas  — ALM is hw, CORE is area, so ALM wins.
        assert "[ALM]" in first_line

    @pytest.mark.parametrize(
        "task_type,expected_tag",
        [
            ("code_change", "FIX"),
            ("bug_fix", "BUG"),
            ("feature", "FEATURE"),
            ("refactor", "REFACTOR"),
            ("investigation", "DOCS"),
            ("documentation", "DOCS"),
            ("performance", "PERF"),
            ("test", "TEST"),
            ("configuration", "CHORE"),
            ("other", "CHORE"),
            ("unknown_type", "CHORE"),
        ],
    )
    def test_task_type_to_main_tag(self, task_type, expected_tag):
        msg = _build("Fix something", task_type=task_type)
        first_line = msg.split("\n")[0]
        assert first_line.startswith(f"[{expected_tag}]")

    def test_long_subject_preserved(self):
        long_subject = "A" * 100
        msg = _build(long_subject)
        first_line = msg.split("\n")[0]
        assert long_subject in first_line

    def test_body_contains_issue_id(self):
        msg = _build("Fix thing", issue_id=42)
        assert "Redmine issue #42" in msg

    def test_body_contains_automated_by(self):
        msg = _build("Fix thing")
        assert "Automated-By: Golem" in msg

    def test_agent_marker_stripped_case_insensitive(self):
        for marker in ("[AGENT]", "[agent]", "[Agent]"):
            msg = _build(f"{marker} Fix ALM stuff")
            first_line = msg.split("\n")[0]
            assert marker not in first_line
