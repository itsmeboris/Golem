# pylint: disable=too-few-public-methods
"""Extended tests for golem.trace_parser — fallback inference and edge cases."""

from __future__ import annotations

from golem.trace_parser import parse_trace

from golem.tests.test_trace_parser import (
    _agent_tool_use,
    _assistant_text,
    _build_fallback_trace,
    _result_event,
    _system_init,
    _task_notification,
    _task_progress,
    _task_started,
    _user_tool_result,
)


class TestOldTraceFallback:
    def test_infer_phases_from_descriptions(self):
        result = parse_trace(_build_fallback_trace())
        phase_names = [p["name"] for p in result["phases"]]
        assert "UNDERSTAND" in phase_names
        assert "BUILD" in phase_names
        assert "REVIEW" in phase_names

    def test_fallback_still_groups_subagents(self):
        """Single builder in fallback trace -> 1 subagent in BUILD phase."""
        events = [
            _system_init(),
            _agent_tool_use(
                "Build feature",
                tool_use_id="tu_b1",
                description="Build feature",
                subagent_type="builder",
                model="sonnet",
            ),
            _task_started("tu_b1", task_id="task_b1"),
            _task_notification("task_b1", "tu_b1", 50000, 12000, 5),
            _user_tool_result("tu_b1", "Done."),
        ]
        result = parse_trace(events)
        total = sum(len(p["subagents"]) for p in result["phases"])
        assert total == 1


# ---------------------------------------------------------------------------
# Task 2.5: Malformed/incomplete trace handling
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_events_list(self):
        result = parse_trace([])
        assert not result["phases"]
        assert result["totals"]["subagent_count"] == 0

    def test_only_system_init(self):
        result = parse_trace([_system_init()])
        assert not result["phases"]

    def test_agent_without_task_started(self):
        """Agent tool_use with no matching task_started — orphan agent still present."""
        events = [
            _assistant_text("## Phase: BUILD\nDispatching builder."),
            _agent_tool_use(
                "Build feature",
                tool_use_id="tu_orphan",
                description="Build feature",
                subagent_type="builder",
                model="sonnet",
            ),
            # No task_started, no task_notification
            _user_tool_result("tu_orphan", "Done."),
        ]
        result = parse_trace(events)
        build = next(p for p in result["phases"] if p["name"] == "BUILD")
        assert len(build["subagents"]) == 1
        orphan = build["subagents"][0]
        assert orphan["duration_ms"] == 0
        assert orphan["tokens"] == 0

    def test_task_progress_without_matching_agent(self):
        """Orphan task_progress events (no matching agent) do not crash."""
        events = [
            _system_init(),
            _assistant_text("## Phase: BUILD\nWorking..."),
            _task_progress("task_ghost", "Read", "file.py", 5000, 1, 1000),
        ]
        result = parse_trace(events)
        assert result["phases"]  # no crash

    def test_result_with_malformed_json(self):
        """```json block with invalid JSON -> final_report=None."""
        events = [
            _system_init(),
            _result_event(result_text="Done.\n```json\n{invalid json here\n```"),
        ]
        result = parse_trace(events)
        assert result["final_report"] is None

    def test_non_dict_content_blocks_skipped(self):
        """String/int/None in content blocks of assistant events do not crash."""
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        "bare string",
                        42,
                        None,
                        {"type": "text", "text": "## Phase: UNDERSTAND\nOK"},
                    ]
                },
            }
        ]
        result = parse_trace(events)
        assert result["phases"]  # no crash

    def test_fix_cycle_skips_non_builder_before_builder(self):
        """A non-builder subagent between reviewer and fix_builder is skipped."""
        events = [
            _system_init(),
            _assistant_text("## Phase: REVIEW\nReviewing."),
            # Reviewer that NEEDS_FIXES
            _agent_tool_use(
                "Quality review",
                tool_use_id="tu_rev_nf",
                description="Quality review",
                subagent_type="quality_reviewer",
                model="sonnet",
            ),
            _task_started("tu_rev_nf", task_id="task_rev_nf"),
            _task_notification("task_rev_nf", "tu_rev_nf", 30000, 10000, 3),
            _user_tool_result(
                "tu_rev_nf",
                "NEEDS_FIXES\n[90%] golem/foo.py:1 — some issue",
            ),
            # An interstitial non-builder subagent before the fix builder
            _agent_tool_use(
                "Analyze root cause",
                tool_use_id="tu_analyzer",
                description="Analyze root cause",
                subagent_type="analyzer",
                model="sonnet",
            ),
            _task_started("tu_analyzer", task_id="task_analyzer"),
            _task_notification("task_analyzer", "tu_analyzer", 10000, 5000, 2),
            _user_tool_result("tu_analyzer", "Analyzed."),
            # Actual fix builder
            _agent_tool_use(
                "Fix the issue",
                tool_use_id="tu_fix",
                description="Fix the issue",
                subagent_type="builder",
                model="sonnet",
            ),
            _task_started("tu_fix", task_id="task_fix"),
            _task_notification("task_fix", "tu_fix", 20000, 8000, 5),
            _user_tool_result("tu_fix", "Fixed."),
        ]
        result = parse_trace(events)
        review = next(p for p in result["phases"] if p["name"] == "REVIEW")
        assert len(review["fix_cycles"]) == 1
        assert review["fix_cycles"][0]["fix_builder"]["role"] == "builder"

    def test_json_block_non_dict_returns_none(self):
        """JSON block that parses to a non-dict value -> final_report=None."""
        events = [
            _system_init(),
            _result_event(result_text='Done.\n```json\n["not", "a", "dict"]\n```'),
        ]
        result = parse_trace(events)
        assert result["final_report"] is None

    def test_fallback_consecutive_same_phase_grouped(self):
        """Two consecutive same-phase agents in fallback are grouped."""
        events = [
            _system_init(),
            _agent_tool_use(
                "Build feature part 1",
                tool_use_id="tu_b1",
                description="Build feature part 1",
                subagent_type="builder",
                model="sonnet",
            ),
            _task_started("tu_b1", task_id="task_b1"),
            _task_notification("task_b1", "tu_b1", 30000, 10000, 4),
            _user_tool_result("tu_b1", "Done part 1."),
            _agent_tool_use(
                "Build feature part 2",
                tool_use_id="tu_b2",
                description="Implement feature part 2",
                subagent_type="builder",
                model="sonnet",
            ),
            _task_started("tu_b2", task_id="task_b2"),
            _task_notification("task_b2", "tu_b2", 30000, 10000, 4),
            _user_tool_result("tu_b2", "Done part 2."),
        ]
        result = parse_trace(events)
        build_phases = [p for p in result["phases"] if p["name"] == "BUILD"]
        assert len(build_phases) == 1
        assert len(build_phases[0]["subagents"]) == 2

    def test_fallback_unknown_description_not_included(self):
        """Agent with no matching keywords is not included."""
        events = [
            _system_init(),
            _agent_tool_use(
                "Do something unrecognized",
                tool_use_id="tu_x",
                description="xyz unrecognized task",
                subagent_type="custom",
                model="sonnet",
            ),
            _task_started("tu_x", task_id="task_x"),
            _task_notification("task_x", "tu_x", 10000, 5000, 2),
            _user_tool_result("tu_x", "Done."),
        ]
        result = parse_trace(events)
        assert not result["phases"]
