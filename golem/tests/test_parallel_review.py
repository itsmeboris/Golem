# pylint: disable=too-few-public-methods
"""Tests for golem.parallel_review — multi-perspective parallel review."""

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from golem.core.config import GolemFlowConfig
from golem.parallel_review import (
    ReviewerRole,
    ReviewFinding,
    ReviewResult,
    _deduplicate_findings,
    aggregate_reviews,
    default_reviewers,
    enhanced_reviewers,
    roles_from_config,
)


class TestReviewerRole:
    @pytest.mark.parametrize(
        "role,expected_template",
        [
            (ReviewerRole.SPEC, "orchestrate_review_template.txt"),
            (ReviewerRole.QUALITY, "orchestrate_review_template.txt"),
            (ReviewerRole.SECURITY, "review_security.txt"),
            (ReviewerRole.CONSISTENCY, "review_consistency.txt"),
            (ReviewerRole.TEST_QUALITY, "review_test_quality.txt"),
        ],
    )
    def test_prompt_template(self, role, expected_template):
        assert role.prompt_template == expected_template

    @pytest.mark.parametrize(
        "role,expected_keyword",
        [
            (ReviewerRole.SPEC, "Spec"),
            (ReviewerRole.QUALITY, "quality"),
            (ReviewerRole.SECURITY, "Security"),
            (ReviewerRole.CONSISTENCY, "Consistency"),
            (ReviewerRole.TEST_QUALITY, "Test"),
        ],
    )
    def test_description_contains_keyword(self, role, expected_keyword):
        desc = role.description
        assert len(desc) > 0
        assert expected_keyword in desc

    def test_enum_from_value(self):
        assert ReviewerRole("security") == ReviewerRole.SECURITY

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("spec", ReviewerRole.SPEC),
            ("quality", ReviewerRole.QUALITY),
            ("security", ReviewerRole.SECURITY),
            ("consistency", ReviewerRole.CONSISTENCY),
            ("test_quality", ReviewerRole.TEST_QUALITY),
        ],
    )
    def test_all_roles_from_value(self, value, expected):
        assert ReviewerRole(value) == expected


class TestReviewResult:
    def test_default_empty_findings(self):
        result = ReviewResult(role=ReviewerRole.SPEC, verdict="APPROVED")
        assert result.findings == []


class TestDefaultReviewers:
    def test_returns_spec_and_quality(self):
        roles = default_reviewers()
        assert roles == [ReviewerRole.SPEC, ReviewerRole.QUALITY]

    def test_length_is_two(self):
        assert len(default_reviewers()) == 2


class TestEnhancedReviewers:
    def test_returns_all_five(self):
        roles = enhanced_reviewers()
        assert len(roles) == 5

    def test_contains_all_roles(self):
        roles = enhanced_reviewers()
        assert set(roles) == set(ReviewerRole)


class TestRolesFromConfig:
    def test_valid_names(self):
        roles = roles_from_config(["security", "consistency"])
        assert roles == [ReviewerRole.SECURITY, ReviewerRole.CONSISTENCY]

    def test_unknown_name_logged_and_skipped(self, caplog):
        with caplog.at_level(logging.WARNING, logger="golem.parallel_review"):
            roles = roles_from_config(["security", "bogus_role"])
        assert roles == [ReviewerRole.SECURITY]
        assert "bogus_role" in caplog.text

    def test_empty_list_returns_empty(self):
        assert roles_from_config([]) == []

    def test_mixed_valid_and_invalid(self, caplog):
        with caplog.at_level(logging.WARNING, logger="golem.parallel_review"):
            roles = roles_from_config(["spec", "not_a_role", "test_quality"])
        assert roles == [ReviewerRole.SPEC, ReviewerRole.TEST_QUALITY]
        assert "not_a_role" in caplog.text


class TestAggregateReviews:
    def test_all_approved_returns_approved(self):
        results = [
            ReviewResult(role=ReviewerRole.SPEC, verdict="APPROVED"),
            ReviewResult(role=ReviewerRole.QUALITY, verdict="APPROVED"),
        ]
        agg = aggregate_reviews(results)
        assert agg.overall_verdict == "APPROVED"
        assert agg.findings == []

    def test_one_needs_fixes_returns_needs_fixes(self):
        results = [
            ReviewResult(role=ReviewerRole.SPEC, verdict="APPROVED"),
            ReviewResult(role=ReviewerRole.SECURITY, verdict="NEEDS_FIXES"),
        ]
        agg = aggregate_reviews(results)
        assert agg.overall_verdict == "NEEDS_FIXES"

    def test_findings_below_threshold_filtered(self):
        finding_low = ReviewFinding(
            confidence=79,
            file_line="foo.py:1",
            description="Low confidence finding",
            reviewer="security",
        )
        results = [
            ReviewResult(
                role=ReviewerRole.SECURITY,
                verdict="NEEDS_FIXES",
                findings=[finding_low],
            )
        ]
        agg = aggregate_reviews(results, confidence_threshold=80)
        assert agg.findings == []

    def test_findings_at_threshold_included(self):
        finding_at = ReviewFinding(
            confidence=80,
            file_line="foo.py:2",
            description="Exactly at threshold",
            reviewer="security",
        )
        results = [
            ReviewResult(
                role=ReviewerRole.SECURITY,
                verdict="NEEDS_FIXES",
                findings=[finding_at],
            )
        ]
        agg = aggregate_reviews(results, confidence_threshold=80)
        assert len(agg.findings) == 1
        assert agg.findings[0].file_line == "foo.py:2"

    def test_different_reviewers_same_line_both_kept(self):
        f1 = ReviewFinding(
            confidence=85,
            file_line="dup.py:5",
            description="First finding",
            reviewer="security",
        )
        f2 = ReviewFinding(
            confidence=92,
            file_line="dup.py:5",
            description="Second finding same line",
            reviewer="consistency",
        )
        results = [
            ReviewResult(
                role=ReviewerRole.SECURITY, verdict="NEEDS_FIXES", findings=[f1]
            ),
            ReviewResult(
                role=ReviewerRole.CONSISTENCY, verdict="NEEDS_FIXES", findings=[f2]
            ),
        ]
        agg = aggregate_reviews(results)
        assert len(agg.findings) == 2
        confidences = {f.confidence for f in agg.findings}
        assert confidences == {85, 92}

    def test_same_reviewer_same_line_deduplicates_keeps_highest_confidence(self):
        f1 = ReviewFinding(
            confidence=85,
            file_line="dup.py:5",
            description="First finding",
            reviewer="security",
        )
        f2 = ReviewFinding(
            confidence=92,
            file_line="dup.py:5",
            description="Duplicate from same reviewer",
            reviewer="security",
        )
        results = [
            ReviewResult(
                role=ReviewerRole.SECURITY, verdict="NEEDS_FIXES", findings=[f1, f2]
            ),
        ]
        agg = aggregate_reviews(results)
        assert len(agg.findings) == 1
        assert agg.findings[0].confidence == 92

    def test_empty_results_returns_approved(self):
        agg = aggregate_reviews([])
        assert agg.overall_verdict == "APPROVED"
        assert agg.findings == []

    def test_multiple_reviewers_all_high_confidence_findings_included(self):
        f1 = ReviewFinding(
            confidence=90,
            file_line="a.py:1",
            description="Issue A",
            reviewer="security",
        )
        f2 = ReviewFinding(
            confidence=85,
            file_line="b.py:2",
            description="Issue B",
            reviewer="test_quality",
        )
        results = [
            ReviewResult(
                role=ReviewerRole.SECURITY, verdict="NEEDS_FIXES", findings=[f1]
            ),
            ReviewResult(
                role=ReviewerRole.TEST_QUALITY, verdict="NEEDS_FIXES", findings=[f2]
            ),
        ]
        agg = aggregate_reviews(results)
        file_lines = {f.file_line for f in agg.findings}
        assert file_lines == {"a.py:1", "b.py:2"}

    def test_reviewer_summaries_populated(self):
        results = [
            ReviewResult(role=ReviewerRole.SPEC, verdict="APPROVED"),
            ReviewResult(role=ReviewerRole.SECURITY, verdict="NEEDS_FIXES"),
        ]
        agg = aggregate_reviews(results)
        assert agg.reviewer_summaries["spec"] == "APPROVED"
        assert agg.reviewer_summaries["security"] == "NEEDS_FIXES"


class TestDeduplicateFindings:
    def test_same_reviewer_same_line_keeps_highest_confidence(self):
        f1 = ReviewFinding(
            confidence=70, file_line="x.py:10", description="A", reviewer="r1"
        )
        f2 = ReviewFinding(
            confidence=95, file_line="x.py:10", description="B", reviewer="r1"
        )
        result = _deduplicate_findings([f1, f2])
        assert len(result) == 1
        assert result[0].confidence == 95

    def test_different_reviewer_same_line_keeps_both(self):
        f1 = ReviewFinding(
            confidence=70, file_line="x.py:10", description="A", reviewer="r1"
        )
        f2 = ReviewFinding(
            confidence=95, file_line="x.py:10", description="B", reviewer="r2"
        )
        result = _deduplicate_findings([f1, f2])
        assert len(result) == 2
        confidences = {f.confidence for f in result}
        assert confidences == {70, 95}

    def test_different_file_lines_keeps_all(self):
        f1 = ReviewFinding(
            confidence=80, file_line="x.py:1", description="A", reviewer="r1"
        )
        f2 = ReviewFinding(
            confidence=80, file_line="x.py:2", description="B", reviewer="r2"
        )
        result = _deduplicate_findings([f1, f2])
        assert len(result) == 2

    def test_empty_list_returns_empty(self):
        assert _deduplicate_findings([]) == []


class TestConfigDefaults:
    def test_enhanced_review_defaults_false(self):
        config = GolemFlowConfig()
        assert config.enhanced_review is False

    def test_review_roles_defaults_empty(self):
        config = GolemFlowConfig()
        assert config.review_roles == []

    def test_review_roles_is_independent_per_instance(self):
        c1 = GolemFlowConfig()
        c2 = GolemFlowConfig()
        c1.review_roles.append("security")
        assert c2.review_roles == []


class TestPromptTemplatesExist:
    @pytest.mark.parametrize(
        "template_name",
        [
            "review_security.txt",
            "review_consistency.txt",
            "review_test_quality.txt",
        ],
    )
    def test_template_exists(self, template_name):
        prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
        template_path = prompts_dir / template_name
        assert template_path.exists(), f"Missing template: {template_path}"

    @pytest.mark.parametrize(
        "template_name",
        [
            "review_security.txt",
            "review_consistency.txt",
            "review_test_quality.txt",
        ],
    )
    def test_template_contains_work_dir_placeholder(self, template_name):
        prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
        content = (prompts_dir / template_name).read_text(encoding="utf-8")
        assert (
            "{work_dir}" in content
        ), f"{template_name} missing {{work_dir}} placeholder"


class TestBuildPromptEnhancedReview:
    def _make_profile(self, captured_kwargs=None):
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "description"

        def _format(template, **kwargs):
            if captured_kwargs is not None:
                captured_kwargs.update(kwargs)
            return kwargs.get("enhanced_review_section", "") + " prompt text"

        profile.prompt_provider.format.side_effect = _format
        profile.tool_provider.servers_for_subject.return_value = []
        profile.state_backend = MagicMock()
        profile.notifier = MagicMock()
        return profile

    def _make_supervisor(self, config, profile):
        from golem.orchestrator import TaskSession
        from golem.supervisor_v2_subagent import SubagentSupervisor

        session = TaskSession(parent_issue_id=1, parent_subject="Test")
        return SubagentSupervisor(
            session=session,
            config=MagicMock(),
            task_config=config,
            profile=profile,
        )

    def test_enhanced_review_true_includes_section(self):
        captured = {}
        profile = self._make_profile(captured_kwargs=captured)
        config = GolemFlowConfig(
            enhanced_review=True,
            context_injection=False,
            enable_simplify_pass=False,
        )
        sup = self._make_supervisor(config, profile)
        sup._build_prompt(1, "desc", "/work")
        section = captured.get("enhanced_review_section", "")
        assert "Enhanced Parallel Review" in section

    def test_enhanced_review_false_no_section(self):
        captured = {}
        profile = self._make_profile(captured_kwargs=captured)
        config = GolemFlowConfig(
            enhanced_review=False,
            context_injection=False,
            enable_simplify_pass=False,
        )
        sup = self._make_supervisor(config, profile)
        sup._build_prompt(1, "desc", "/work")
        section = captured.get("enhanced_review_section", "")
        assert section == ""

    def test_enhanced_review_with_custom_roles(self):
        captured = {}
        profile = self._make_profile(captured_kwargs=captured)
        config = GolemFlowConfig(
            enhanced_review=True,
            review_roles=["security", "test_quality"],
            context_injection=False,
            enable_simplify_pass=False,
        )
        sup = self._make_supervisor(config, profile)
        sup._build_prompt(1, "desc", "/work")
        section = captured.get("enhanced_review_section", "")
        assert "security" in section
        assert "test_quality" in section
        # SPEC and QUALITY are filtered out (they are the 2-stage review)
        # Use "**spec**" / "**quality**" role markers to avoid false matches
        # from words like "specialized" or "quality reviewer"
        assert "**spec**" not in section
        assert "**quality**" not in section

    def test_enhanced_review_with_only_spec_quality_roles_produces_empty_section(self):
        """If only SPEC+QUALITY requested, extra_roles is empty → no section injected."""
        captured = {}
        profile = self._make_profile(captured_kwargs=captured)
        config = GolemFlowConfig(
            enhanced_review=True,
            review_roles=["spec", "quality"],
            context_injection=False,
            enable_simplify_pass=False,
        )
        sup = self._make_supervisor(config, profile)
        sup._build_prompt(1, "desc", "/work")
        section = captured.get("enhanced_review_section", "")
        assert section == ""
