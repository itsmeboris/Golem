"""Tests for PLAN phase upgrade: TypedDicts, prompt rendering, templates."""

import pathlib
from unittest.mock import MagicMock

import pytest

from golem.core.config import GolemFlowConfig
from golem.orchestrator import TaskSession
from golem.supervisor_v2_subagent import SubagentSupervisor
from golem.types import PlanStepDict, PlanHandoffDict

PROMPTS_DIR = pathlib.Path(__file__).parent.parent / "prompts"


# -- TypedDict contract tests -----------------------------------------------


class TestPlanStepDict:
    def test_required_keys(self):
        assert PlanStepDict.__required_keys__ == {  # pylint: disable=no-member
            "task_name",
            "files_created",
            "files_modified",
            "files_tested",
            "step_descriptions",
        }

    def test_optional_keys_empty(self):
        assert PlanStepDict.__optional_keys__ == set()  # pylint: disable=no-member


class TestPlanHandoffDict:
    def test_required_keys(self):
        assert PlanHandoffDict.__required_keys__ == {  # pylint: disable=no-member
            "from_phase",
            "to_phase",
            "complexity",
            "file_map",
            "steps",
            "test_strategy",
            "open_questions",
            "warnings",
            "plan_reviewer_status",
            "timestamp",
        }

    @pytest.mark.parametrize("complexity", ["trivial", "standard", "complex"])
    def test_valid_complexity_values_accepted(self, complexity):
        """Complexity is a plain str — any value is accepted at runtime."""
        handoff: PlanHandoffDict = {
            "from_phase": "PLAN",
            "to_phase": "BUILD",
            "complexity": complexity,
            "file_map": [],
            "steps": [],
            "test_strategy": "Unit tests",
            "open_questions": [],
            "warnings": [],
            "plan_reviewer_status": "approved",
            "timestamp": "2026-04-05T10:00:00+00:00",
        }
        assert handoff["complexity"] == complexity

    @pytest.mark.parametrize("reviewer_status", ["skipped", "approved", "issues_fixed"])
    def test_valid_plan_reviewer_statuses(self, reviewer_status):
        handoff: PlanHandoffDict = {
            "from_phase": "PLAN",
            "to_phase": "BUILD",
            "complexity": "standard",
            "file_map": [],
            "steps": [],
            "test_strategy": "Unit tests",
            "open_questions": [],
            "warnings": [],
            "plan_reviewer_status": reviewer_status,
            "timestamp": "2026-04-05T10:00:00+00:00",
        }
        assert handoff["plan_reviewer_status"] == reviewer_status


# -- Prompt rendering tests -------------------------------------------------


def _make_profile():
    profile = MagicMock()
    profile.task_source.get_task_description.return_value = "description"
    profile.prompt_provider.format.return_value = "prompt text"
    profile.tool_provider.servers_for_subject.return_value = []
    profile.state_backend = MagicMock()
    profile.notifier = MagicMock()
    return profile


def _make_config(**overrides):
    defaults = {
        "enabled": True,
        "task_model": "sonnet",
        "supervisor_mode": True,
        "use_worktrees": False,
        "auto_commit": True,
        "max_retries": 1,
        "default_work_dir": "/tmp/test",
    }
    defaults.update(overrides)
    return GolemFlowConfig(**defaults)


def _make_supervisor(session=None, config=None, profile=None):
    if session is None:
        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
    if config is None:
        config = _make_config()
    if profile is None:
        profile = _make_profile()
    return SubagentSupervisor(
        session=session,
        config=MagicMock(),
        task_config=config,
        profile=profile,
    )


class TestBuildPromptStructuredPlanning:
    def test_contains_planner_dispatch(self):
        """Prompt includes Planner subagent dispatch instructions."""
        profile = _make_profile()
        sup = _make_supervisor(profile=profile)
        sup._build_prompt(42, "desc", "/work")
        call_kwargs = profile.prompt_provider.format.call_args[1]
        section = call_kwargs["structured_planning_section"]
        assert "orchestrate_planner_template.txt" in section
        assert "Planner" in section
        assert "File Map" in section

    def test_mentions_plan_review(self):
        """Planner dispatch section includes plan review instructions."""
        profile = _make_profile()
        sup = _make_supervisor(profile=profile)
        sup._build_prompt(42, "desc", "/work")
        call_kwargs = profile.prompt_provider.format.call_args[1]
        section = call_kwargs["structured_planning_section"]
        assert "Plan Reviewer" in section
        assert "Trivial" in section
        assert "Standard" in section


# -- Template file tests ----------------------------------------------------


class TestPlannerTemplate:
    """Verify that the planner prompt template exists and contains required sections."""

    def test_template_file_exists(self):
        assert (PROMPTS_DIR / "orchestrate_planner_template.txt").exists()

    def test_template_has_file_map_section(self):
        content = (PROMPTS_DIR / "orchestrate_planner_template.txt").read_text()
        assert "File Map" in content

    @pytest.mark.parametrize(
        "keyword",
        [
            "placeholder",
            "No Placeholder",
        ],
        ids=["lowercase_placeholder", "titled_no_placeholder"],
    )
    def test_template_has_placeholder_rule(self, keyword):
        content = (PROMPTS_DIR / "orchestrate_planner_template.txt").read_text()
        assert keyword.lower() in content.lower()

    def test_template_has_test_strategy_section(self):
        content = (PROMPTS_DIR / "orchestrate_planner_template.txt").read_text()
        assert "Test Strategy" in content

    def test_template_has_plan_reviewer_section(self):
        content = (PROMPTS_DIR / "orchestrate_planner_template.txt").read_text()
        assert "Plan Reviewer" in content

    def test_template_has_read_only_scope(self):
        content = (PROMPTS_DIR / "orchestrate_planner_template.txt").read_text()
        assert "Do NOT modify" in content

    def test_template_has_checkbox_syntax(self):
        content = (PROMPTS_DIR / "orchestrate_planner_template.txt").read_text()
        assert "- [ ]" in content


class TestOrchestratePlanPhaseTemplate:
    def test_placeholder_present(self):
        text = (PROMPTS_DIR / "orchestrate_task.txt").read_text()
        assert "{structured_planning_section}" in text

    def test_hardcoded_specs_removed(self):
        text = (PROMPTS_DIR / "orchestrate_task.txt").read_text()
        start = text.find("### Phase 2: Plan")
        end = text.find("### Phase 3:", start)
        section = text[start:end]
        assert "specification statements" not in section


class TestBuilderTemplateImplementationPlan:
    def test_implementation_plan_section_present(self):
        text = (PROMPTS_DIR / "orchestrate_builder_template.txt").read_text()
        assert "## Implementation Plan" in text

    def test_implementation_plan_before_context(self):
        text = (PROMPTS_DIR / "orchestrate_builder_template.txt").read_text()
        plan_pos = text.find("## Implementation Plan")
        context_pos = text.find("## Context from exploration")
        assert plan_pos < context_pos
