"""Tests for plugins/golem/scripts/lib/delegation.py."""

import pytest

import delegation  # pylint: disable=import-error


class TestStructureTaskMetadata:
    def test_simple_prompt_returns_correct_word_count(self):
        result = delegation.structure_task_metadata("fix the login bug")
        assert result["word_count"] == 4

    def test_detects_file_references_with_extension(self):
        result = delegation.structure_task_metadata(
            "update golem/flow.py to fix timeout"
        )
        assert "golem/flow.py" in result["file_refs"]
        assert result["file_ref_count"] >= 1

    def test_detects_file_references_with_slash(self):
        result = delegation.structure_task_metadata("check golem/tests/ directory")
        assert result["file_ref_count"] >= 1

    def test_detects_complexity_keywords(self):
        result = delegation.structure_task_metadata(
            "refactor all modules across the codebase"
        )
        assert "refactor" in result["complexity_keywords"]
        assert "all" in result["complexity_keywords"]
        assert "across" in result["complexity_keywords"]
        assert "modules" in result["complexity_keywords"]

    def test_detects_simplicity_keywords(self):
        result = delegation.structure_task_metadata("fix the typo in config comment")
        assert "fix" in result["simplicity_keywords"]
        assert "typo" in result["simplicity_keywords"]
        assert "config" in result["simplicity_keywords"]
        assert "comment" in result["simplicity_keywords"]

    def test_empty_prompt_returns_zeroed_result(self):
        result = delegation.structure_task_metadata("")
        assert result["word_count"] == 0
        assert result["file_ref_count"] == 0
        assert result["file_refs"] == []
        assert result["complexity_keywords"] == []
        assert result["simplicity_keywords"] == []

    def test_file_refs_capped_at_ten(self):
        # Create prompt with more than 10 file references
        refs = " ".join(f"file{i}.py" for i in range(20))
        result = delegation.structure_task_metadata(refs)
        assert len(result["file_refs"]) <= 10
        assert result["file_ref_count"] == 20

    def test_no_false_complexity_for_simple_task(self):
        result = delegation.structure_task_metadata("fix the typo")
        assert result["complexity_keywords"] == []

    def test_no_false_simplicity_for_complex_task(self):
        result = delegation.structure_task_metadata(
            "migrate the entire database schema"
        )
        assert result["simplicity_keywords"] == []

    @pytest.mark.parametrize(
        "prompt,expected_file_ref_count",
        [
            ("update README.md", 1),
            ("look at src/main.go and tests/integration.go", 2),
            ("no file references here", 0),
            ("golem/flow.py golem/orchestrator.py golem/verifier.py", 3),
        ],
        ids=["single_ref", "two_refs", "no_refs", "three_refs"],
    )
    def test_file_ref_count(self, prompt, expected_file_ref_count):
        result = delegation.structure_task_metadata(prompt)
        assert result["file_ref_count"] == expected_file_ref_count

    @pytest.mark.parametrize(
        "prompt,expected_complexity_kws",
        [
            ("refactor the auth module", ["refactor"]),
            (
                "migrate everything across components",
                ["migrate", "across", "components"],
            ),
            ("rewrite the scheduler", ["rewrite"]),
            ("simple update to config", []),
        ],
        ids=["refactor", "migrate_across_components", "rewrite", "no_complexity"],
    )
    def test_complexity_keyword_extraction(self, prompt, expected_complexity_kws):
        result = delegation.structure_task_metadata(prompt)
        for kw in expected_complexity_kws:
            assert kw in result["complexity_keywords"]
