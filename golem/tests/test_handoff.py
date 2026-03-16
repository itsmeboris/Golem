# golem/tests/test_handoff.py
"""Tests for golem.handoff — create_handoff, validate_handoff, format_handoff_markdown."""

from datetime import datetime, timezone

import pytest

from golem.handoff import create_handoff, format_handoff_markdown, validate_handoff
from golem.types import FileRoleDict, PhaseHandoffDict


class TestCreateHandoff:
    def test_returns_all_required_keys(self):
        """create_handoff() returns a dict with all PhaseHandoffDict keys."""
        result = create_handoff(
            from_phase="UNDERSTAND",
            to_phase="PLAN",
            context=["context item"],
            files=[],
            open_questions=[],
            warnings=[],
        )
        for key in PhaseHandoffDict.__required_keys__:  # pylint: disable=no-member
            assert key in result, "Missing required key: %s" % key

    def test_fields_populated_correctly(self):
        """create_handoff() populates from_phase, to_phase, context, files, open_questions, warnings."""
        files: list[FileRoleDict] = [
            {"path": "golem/foo.py", "role": "modify", "relevance": "main target"}
        ]
        result = create_handoff(
            from_phase="BUILD",
            to_phase="REVIEW",
            context=["implemented feature X"],
            files=files,
            open_questions=["Is error handling sufficient?"],
            warnings=["Be careful with concurrency"],
        )
        assert result["from_phase"] == "BUILD"
        assert result["to_phase"] == "REVIEW"
        assert result["context"] == ["implemented feature X"]
        assert result["files"] == files
        assert result["open_questions"] == ["Is error handling sufficient?"]
        assert result["warnings"] == ["Be careful with concurrency"]

    def test_timestamp_auto_populated(self):
        """create_handoff() auto-populates a valid UTC ISO timestamp."""
        result = create_handoff(
            from_phase="PLAN",
            to_phase="BUILD",
            context=["some context"],
            files=[],
            open_questions=[],
            warnings=[],
        )
        ts = datetime.fromisoformat(result["timestamp"])
        assert ts.tzinfo == timezone.utc

    def test_empty_lists_accepted(self):
        """create_handoff() accepts all-empty lists without error."""
        result = create_handoff(
            from_phase="REVIEW",
            to_phase="VERIFY",
            context=["at least one context"],
            files=[],
            open_questions=[],
            warnings=[],
        )
        assert result["files"] == []
        assert result["open_questions"] == []
        assert result["warnings"] == []

    def test_multiple_files(self):
        """create_handoff() preserves multiple FileRoleDict entries."""
        files: list[FileRoleDict] = [
            {"path": "golem/a.py", "role": "modify", "relevance": "primary"},
            {"path": "golem/b.py", "role": "read", "relevance": "context"},
            {"path": "golem/c.py", "role": "create", "relevance": "new module"},
        ]
        result = create_handoff(
            from_phase="UNDERSTAND",
            to_phase="PLAN",
            context=["analyzed codebase"],
            files=files,
            open_questions=[],
            warnings=[],
        )
        assert len(result["files"]) == 3
        assert result["files"][0]["role"] == "modify"
        assert result["files"][1]["role"] == "read"
        assert result["files"][2]["role"] == "create"


class TestValidateHandoff:
    def test_valid_handoff_returns_true_empty_reasons(self):
        """validate_handoff() returns (True, []) for a complete handoff."""
        handoff = create_handoff(
            from_phase="BUILD",
            to_phase="REVIEW",
            context=["some context"],
            files=[],
            open_questions=[],
            warnings=[],
        )
        valid, reasons = validate_handoff(handoff)
        assert valid is True
        assert reasons == []

    @pytest.mark.parametrize(
        "missing_field,override,expected_reason_fragment",
        [
            ("from_phase", "", "from_phase"),
            ("to_phase", "", "to_phase"),
            ("context", [], "context"),
        ],
    )
    def test_missing_or_empty_required_field(
        self, missing_field, override, expected_reason_fragment
    ):
        """validate_handoff() returns (False, [reasons]) when required field is missing or empty."""
        handoff = create_handoff(
            from_phase="BUILD",
            to_phase="REVIEW",
            context=["some context"],
            files=[],
            open_questions=[],
            warnings=[],
        )
        handoff[missing_field] = override
        valid, reasons = validate_handoff(handoff)
        assert valid is False
        assert len(reasons) >= 1
        assert any(expected_reason_fragment in r for r in reasons)

    def test_multiple_missing_fields_all_reported(self):
        """validate_handoff() reports all missing fields, not just the first."""
        handoff = create_handoff(
            from_phase="BUILD",
            to_phase="REVIEW",
            context=["some context"],
            files=[],
            open_questions=[],
            warnings=[],
        )
        handoff["from_phase"] = ""
        handoff["to_phase"] = ""
        handoff["context"] = []
        valid, reasons = validate_handoff(handoff)
        assert valid is False
        assert len(reasons) == 3

    def test_none_from_phase_invalid(self):
        """validate_handoff() treats None from_phase as invalid."""
        handoff = create_handoff(
            from_phase="BUILD",
            to_phase="REVIEW",
            context=["ctx"],
            files=[],
            open_questions=[],
            warnings=[],
        )
        handoff["from_phase"] = None  # type: ignore[typeddict-item]
        valid, reasons = validate_handoff(handoff)
        assert valid is False
        assert any("from_phase" in r for r in reasons)

    def test_none_to_phase_invalid(self):
        """validate_handoff() treats None to_phase as invalid."""
        handoff = create_handoff(
            from_phase="BUILD",
            to_phase="REVIEW",
            context=["ctx"],
            files=[],
            open_questions=[],
            warnings=[],
        )
        handoff["to_phase"] = None  # type: ignore[typeddict-item]
        valid, reasons = validate_handoff(handoff)
        assert valid is False
        assert any("to_phase" in r for r in reasons)

    def test_missing_key_from_phase(self):
        """validate_handoff() handles dict missing from_phase key entirely."""
        handoff: PhaseHandoffDict = {
            "to_phase": "REVIEW",
            "context": ["ctx"],
            "files": [],
            "open_questions": [],
            "warnings": [],
            "timestamp": "2026-03-17T00:00:00",
        }  # type: ignore[typeddict-item]
        valid, reasons = validate_handoff(handoff)
        assert valid is False
        assert any("from_phase" in r for r in reasons)

    def test_valid_with_all_optional_lists_populated(self):
        """validate_handoff() passes when all fields including optional lists are populated."""
        files: list[FileRoleDict] = [
            {"path": "golem/foo.py", "role": "modify", "relevance": "primary"}
        ]
        handoff = create_handoff(
            from_phase="PLAN",
            to_phase="BUILD",
            context=["ctx1", "ctx2"],
            files=files,
            open_questions=["Q1?"],
            warnings=["W1"],
        )
        valid, reasons = validate_handoff(handoff)
        assert valid is True
        assert reasons == []


class TestFormatHandoffMarkdown:
    def test_header_contains_phases(self):
        """format_handoff_markdown() includes both phases in the header."""
        handoff = create_handoff(
            from_phase="BUILD",
            to_phase="REVIEW",
            context=["ctx"],
            files=[],
            open_questions=[],
            warnings=[],
        )
        output = format_handoff_markdown(handoff)
        assert "## Handoff: BUILD → REVIEW" in output

    def test_context_items_listed(self):
        """format_handoff_markdown() lists all context items."""
        handoff = create_handoff(
            from_phase="PLAN",
            to_phase="BUILD",
            context=["implemented feature A", "refactored module B"],
            files=[],
            open_questions=[],
            warnings=[],
        )
        output = format_handoff_markdown(handoff)
        assert "### Context carried forward" in output
        assert "- implemented feature A" in output
        assert "- refactored module B" in output

    def test_files_listed_with_role_and_relevance(self):
        """format_handoff_markdown() formats files as 'path (role): relevance'."""
        files: list[FileRoleDict] = [
            {"path": "golem/foo.py", "role": "modify", "relevance": "primary change"},
        ]
        handoff = create_handoff(
            from_phase="BUILD",
            to_phase="REVIEW",
            context=["ctx"],
            files=files,
            open_questions=[],
            warnings=[],
        )
        output = format_handoff_markdown(handoff)
        assert "### Files identified" in output
        assert "- golem/foo.py (modify): primary change" in output

    def test_open_questions_listed(self):
        """format_handoff_markdown() lists open questions."""
        handoff = create_handoff(
            from_phase="REVIEW",
            to_phase="VERIFY",
            context=["ctx"],
            files=[],
            open_questions=["Is test coverage sufficient?"],
            warnings=[],
        )
        output = format_handoff_markdown(handoff)
        assert "### Open questions" in output
        assert "- Is test coverage sufficient?" in output

    def test_warnings_listed(self):
        """format_handoff_markdown() lists warnings."""
        handoff = create_handoff(
            from_phase="BUILD",
            to_phase="REVIEW",
            context=["ctx"],
            files=[],
            open_questions=[],
            warnings=["Watch for race conditions"],
        )
        output = format_handoff_markdown(handoff)
        assert "### Warnings" in output
        assert "- Watch for race conditions" in output

    def test_empty_optional_sections_still_present(self):
        """format_handoff_markdown() includes all section headers even when lists are empty."""
        handoff = create_handoff(
            from_phase="UNDERSTAND",
            to_phase="PLAN",
            context=["initial understanding"],
            files=[],
            open_questions=[],
            warnings=[],
        )
        output = format_handoff_markdown(handoff)
        assert "### Files identified" in output
        assert "### Open questions" in output
        assert "### Warnings" in output

    def test_full_output_structure(self):
        """format_handoff_markdown() full output matches expected structure."""
        files: list[FileRoleDict] = [
            {"path": "golem/types.py", "role": "modify", "relevance": "add TypedDicts"}
        ]
        handoff = create_handoff(
            from_phase="UNDERSTAND",
            to_phase="PLAN",
            context=["reviewed codebase structure"],
            files=files,
            open_questions=["Which approach is faster?"],
            warnings=["Large file, be careful"],
        )
        output = format_handoff_markdown(handoff)
        lines = output.splitlines()
        # First non-empty line should be the header
        non_empty = [l for l in lines if l.strip()]
        assert non_empty[0] == "## Handoff: UNDERSTAND → PLAN"
        assert "### Context carried forward" in output
        assert "- reviewed codebase structure" in output
        assert "### Files identified" in output
        assert "- golem/types.py (modify): add TypedDicts" in output
        assert "### Open questions" in output
        assert "- Which approach is faster?" in output
        assert "### Warnings" in output
        assert "- Large file, be careful" in output


class TestTaskSessionHandoffs:
    """Tests that TaskSession serializes/deserializes phase_handoffs."""

    def test_phase_handoffs_default_empty(self):
        """TaskSession.phase_handoffs defaults to empty list."""
        from golem.orchestrator import TaskSession

        session = TaskSession(parent_issue_id=1)
        assert session.phase_handoffs == []

    def test_phase_handoffs_in_to_dict(self):
        """TaskSession.to_dict() includes phase_handoffs."""
        from golem.orchestrator import TaskSession

        handoff = create_handoff(
            from_phase="PLAN",
            to_phase="BUILD",
            context=["ctx"],
            files=[],
            open_questions=[],
            warnings=[],
        )
        session = TaskSession(parent_issue_id=1)
        session.phase_handoffs.append(handoff)
        d = session.to_dict()
        assert "phase_handoffs" in d
        assert len(d["phase_handoffs"]) == 1
        assert d["phase_handoffs"][0]["from_phase"] == "PLAN"
        assert d["phase_handoffs"][0]["to_phase"] == "BUILD"

    def test_phase_handoffs_round_trip(self):
        """TaskSession.from_dict() restores phase_handoffs list correctly."""
        from golem.orchestrator import TaskSession

        handoff = create_handoff(
            from_phase="BUILD",
            to_phase="REVIEW",
            context=["built feature"],
            files=[{"path": "golem/foo.py", "role": "modify", "relevance": "main"}],
            open_questions=["Q?"],
            warnings=["W!"],
        )
        session = TaskSession(parent_issue_id=99)
        session.phase_handoffs.append(handoff)
        d = session.to_dict()
        restored = TaskSession.from_dict(d)
        assert len(restored.phase_handoffs) == 1
        ph = restored.phase_handoffs[0]
        assert ph["from_phase"] == "BUILD"
        assert ph["to_phase"] == "REVIEW"
        assert ph["context"] == ["built feature"]
        assert ph["files"][0]["path"] == "golem/foo.py"

    def test_from_dict_defaults_phase_handoffs_to_empty(self):
        """TaskSession.from_dict() defaults phase_handoffs to [] when key absent."""
        from golem.orchestrator import TaskSession

        session = TaskSession.from_dict({"parent_issue_id": 1, "state": "detected"})
        assert session.phase_handoffs == []
