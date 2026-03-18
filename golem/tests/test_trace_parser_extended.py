# pylint: disable=too-few-public-methods
"""Extended tests for golem.trace_parser — fallback inference and edge cases."""

from golem.trace_parser import (
    _extract_thinking_blocks,
    _parse_issues,
    _trim_text_to_phase,
    parse_trace,
)

from golem.tests.test_trace_parser import (
    _agent_tool_use,
    _assistant_text,
    _assistant_tool_use,
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


class TestFixCycleRecheckStatus:
    """Tests for recheck_status field in fix cycle detection."""

    def test_fix_cycle_recheck_needs_fixes(self):
        """Re-check reviewer also returns NEEDS_FIXES — recheck_status reflects it."""
        events = [
            _system_init(),
            _assistant_text("## Phase: REVIEW\nReview."),
            _agent_tool_use(
                "Quality review",
                tool_use_id="tu_r1",
                description="Quality review",
                subagent_type="quality_reviewer",
            ),
            _task_started("tu_r1", task_id="t_r1"),
            _task_notification("t_r1", "tu_r1", 60000, 15000, 5),
            _user_tool_result("tu_r1", "NEEDS_FIXES\n\n1. [90%] f.py:1 — bug"),
            _agent_tool_use(
                "Fix issues",
                tool_use_id="tu_fix",
                description="Fix issues",
                subagent_type="builder",
            ),
            _task_started("tu_fix", task_id="t_fix"),
            _task_notification("t_fix", "tu_fix", 30000, 10000, 3),
            _user_tool_result("tu_fix", "Fixed."),
            _agent_tool_use(
                "Re-check",
                tool_use_id="tu_r2",
                description="Re-check",
                subagent_type="quality_reviewer",
            ),
            _task_started("tu_r2", task_id="t_r2"),
            _task_notification("t_r2", "tu_r2", 50000, 12000, 4),
            _user_tool_result("tu_r2", "NEEDS_FIXES\n\n1. [85%] f.py:2 — new bug"),
            _result_event(),
        ]
        result = parse_trace(events)
        review = next(p for p in result["phases"] if p["name"] == "REVIEW")
        assert review["fix_cycles"][0]["recheck_status"] == "NEEDS_FIXES"
        assert len(review["fix_cycles"]) == 2
        assert review["fix_cycles"][1]["recheck_status"] == "pending"

    def test_recheck_skips_extra_builders(self):
        """Re-check lookup skips consecutive builder-role agents."""
        events = [
            _system_init(),
            _assistant_text("## Phase: REVIEW\nReview."),
            _agent_tool_use(
                "Review", tool_use_id="tu_r", subagent_type="quality_reviewer"
            ),
            _task_started("tu_r", task_id="t_r"),
            _task_notification("t_r", "tu_r", 60000, 15000, 5),
            _user_tool_result("tu_r", "NEEDS_FIXES\n\n1. [90%] f.py:1 — bug"),
            _agent_tool_use("Fix", tool_use_id="tu_b1", subagent_type="builder"),
            _task_started("tu_b1", task_id="t_b1"),
            _task_notification("t_b1", "tu_b1", 30000, 10000, 3),
            _user_tool_result("tu_b1", "Fixed."),
            _agent_tool_use("Lint fix", tool_use_id="tu_b2", subagent_type="builder"),
            _task_started("tu_b2", task_id="t_b2"),
            _task_notification("t_b2", "tu_b2", 15000, 5000, 2),
            _user_tool_result("tu_b2", "Linted."),
            _agent_tool_use(
                "Re-check", tool_use_id="tu_rc", subagent_type="quality_reviewer"
            ),
            _task_started("tu_rc", task_id="t_rc"),
            _task_notification("t_rc", "tu_rc", 40000, 12000, 4),
            _user_tool_result("tu_rc", "APPROVED\nAll good."),
            _result_event(),
        ]
        result = parse_trace(events)
        review = next(p for p in result["phases"] if p["name"] == "REVIEW")
        assert len(review["fix_cycles"]) == 1
        assert review["fix_cycles"][0]["recheck_status"] == "APPROVED"


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


# ---------------------------------------------------------------------------
# Issue 2: Tool result truncation — issues beyond 2000 chars captured
# ---------------------------------------------------------------------------


class TestToolResultTruncation:
    """Issues appearing beyond 2000 chars in reviewer output are now parsed."""

    def test_issues_beyond_2000_chars_captured(self):
        """Reviewer output with issues starting after 2000 chars is fully parsed."""
        padding = "x" * 2100  # Push issues past the old 2000-char limit
        reviewer_output = (
            f"NEEDS_FIXES\n{padding}\n"
            "[95%] golem/foo.py:10 — deep issue beyond truncation point"
        )
        events = [
            _system_init(),
            _assistant_text("## Phase: REVIEW\nReviewing."),
            _agent_tool_use(
                "Review",
                tool_use_id="tu_rev",
                description="Quality review",
                subagent_type="quality_reviewer",
                model="sonnet",
            ),
            _task_started("tu_rev", task_id="t_rev"),
            _task_notification("t_rev", "tu_rev", 60000, 15000, 5),
            _user_tool_result("tu_rev", reviewer_output),
            _result_event(),
        ]
        result = parse_trace(events)
        review = next(p for p in result["phases"] if p["name"] == "REVIEW")
        assert len(review["fix_cycles"]) == 1
        cycle = review["fix_cycles"][0]
        assert len(cycle["issues"]) == 1
        assert cycle["issues"][0]["file"] == "golem/foo.py:10"

    def test_output_field_contains_full_content(self):
        """The subagent output field contains the full (untruncated) output."""
        long_output = "APPROVED\n" + "detail " * 400  # ~2800 chars
        events = [
            _system_init(),
            _assistant_text("## Phase: REVIEW\nReviewing."),
            _agent_tool_use(
                "Review",
                tool_use_id="tu_rev2",
                description="Spec review",
                subagent_type="spec_reviewer",
                model="sonnet",
            ),
            _task_started("tu_rev2", task_id="t_rev2"),
            _task_notification("t_rev2", "tu_rev2", 50000, 12000, 4),
            _user_tool_result("tu_rev2", long_output),
            _result_event(),
        ]
        result = parse_trace(events)
        review = next(p for p in result["phases"] if p["name"] == "REVIEW")
        agent = review["subagents"][0]
        # Output should contain content well beyond 2000 chars
        assert len(agent["output"]) > 2000


# ---------------------------------------------------------------------------
# Issue 3: Thinking block extraction and population
# ---------------------------------------------------------------------------


def _assistant_thinking(thinking_text, text=None):
    """Build an assistant event with a thinking block (and optional text block)."""
    content = [{"type": "thinking", "thinking": thinking_text}]
    if text:
        content.append({"type": "text", "text": text})
    return {"type": "assistant", "message": {"content": content}}


class TestExtractThinkingBlocks:
    """Unit tests for _extract_thinking_blocks."""

    def test_extracts_thinking_from_assistant_event(self):
        event = _assistant_thinking("This is my reasoning.")
        result = _extract_thinking_blocks(event)
        assert result == ["This is my reasoning."]

    def test_returns_empty_for_non_assistant_event(self):
        event = {"type": "user", "message": {"content": []}}
        assert _extract_thinking_blocks(event) == []

    def test_returns_empty_when_no_thinking_blocks(self):
        event = _assistant_text("Just text, no thinking.")
        assert _extract_thinking_blocks(event) == []

    def test_skips_empty_thinking_blocks(self):
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": ""},
                    {"type": "thinking", "thinking": "valid thought"},
                ]
            },
        }
        result = _extract_thinking_blocks(event)
        assert result == ["valid thought"]

    def test_skips_non_dict_blocks(self):
        event = {
            "type": "assistant",
            "message": {
                "content": ["bare string", {"type": "thinking", "thinking": "ok"}]
            },
        }
        result = _extract_thinking_blocks(event)
        assert result == ["ok"]

    def test_multiple_thinking_blocks(self):
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "first thought"},
                    {"type": "text", "text": "some text"},
                    {"type": "thinking", "thinking": "second thought"},
                ]
            },
        }
        result = _extract_thinking_blocks(event)
        assert result == ["first thought", "second thought"]


class TestThinkingBlockPopulation:
    """Tests that orchestrator_thinking is populated in phases."""

    def test_thinking_populated_in_phase(self):
        """Thinking blocks from orchestrator events populate orchestrator_thinking."""
        events = [
            _system_init(),
            _assistant_thinking(
                "Let me think about this.",
                text="## Phase: UNDERSTAND\nAnalyzing.",
            ),
            _assistant_tool_use("Read", {"file_path": "foo.py"}, tool_use_id="tu_r1"),
            _user_tool_result("tu_r1", "content"),
        ]
        result = parse_trace(events)
        understand = next(p for p in result["phases"] if p["name"] == "UNDERSTAND")
        assert understand["orchestrator_thinking"] == ["Let me think about this."]

    def test_no_thinking_gives_empty_list(self):
        """Phase with no thinking events has an empty orchestrator_thinking list."""
        events = [
            _system_init(),
            _assistant_text("## Phase: BUILD\nBuilding."),
            _agent_tool_use(
                "Build",
                tool_use_id="tu_b",
                description="Build feature",
                subagent_type="builder",
                model="sonnet",
            ),
            _task_started("tu_b", task_id="t_b"),
            _task_notification("t_b", "tu_b", 30000, 10000, 3),
            _user_tool_result("tu_b", "Done."),
        ]
        result = parse_trace(events)
        build = next(p for p in result["phases"] if p["name"] == "BUILD")
        assert build["orchestrator_thinking"] == []

    def test_make_phase_has_orchestrator_thinking_key(self):
        """All phases created via _make_phase include the orchestrator_thinking key."""
        parse_trace([])  # empty trace — no phases, no crash
        # Verify the key is in make_phase output via an actual trace
        events = [_assistant_text("## Phase: PLAN\nPlanning.")]
        r = parse_trace(events)
        plan = next(p for p in r["phases"] if p["name"] == "PLAN")
        assert "orchestrator_thinking" in plan

    def test_thinking_across_multiple_events_in_phase(self):
        """Multiple thinking events in a phase are all collected."""
        events = [
            _system_init(),
            _assistant_thinking("First thought.", text="## Phase: REVIEW\nReviewing."),
            _assistant_thinking("Second thought."),
        ]
        result = parse_trace(events)
        review = next(p for p in result["phases"] if p["name"] == "REVIEW")
        assert "First thought." in review["orchestrator_thinking"]
        assert "Second thought." in review["orchestrator_thinking"]


# ---------------------------------------------------------------------------
# Phase duration estimation edge cases
# ---------------------------------------------------------------------------


class TestTrimTextToPhase:
    """Tests for _trim_text_to_phase helper."""

    def test_single_marker_returns_full_text(self):
        text = "## Phase: BUILD\nDispatching builder..."
        assert _trim_text_to_phase(text, "BUILD", None) == text

    def test_no_marker_returns_full_text(self):
        text = "Some random text without markers"
        assert _trim_text_to_phase(text, "BUILD", None) == text

    def test_phase_not_found_returns_full_text(self):
        """When the requested phase is not among the markers, return full text."""
        text = "## Phase: PLAN\nSpecs\n\n## Phase: BUILD\nDispatch"
        result = _trim_text_to_phase(text, "REVIEW", None)
        assert result == text

    def test_splits_plan_from_build(self):
        text = "## Phase: PLAN\nSPEC-1: do X\n\n## Phase: BUILD\nDispatching..."
        plan = _trim_text_to_phase(text, "PLAN", "BUILD")
        build = _trim_text_to_phase(text, "BUILD", None)
        assert "SPEC-1" in plan
        assert "Dispatching" not in plan
        assert "Dispatching" in build
        assert "SPEC-1" not in build


class TestParseIssues:
    """Tests for _parse_issues with primary and fallback regex."""

    def test_confidence_tagged_issues(self):
        output = (
            "NEEDS_FIXES\n\n"
            "[95%] golem/utils.py:16 — ms truncation bug\n"
            "[80%] golem/tests/test_utils.py:45 — missing boundary test\n"
        )
        issues = _parse_issues(output)
        assert len(issues) == 2
        assert issues[0]["confidence"] == 95
        assert issues[0]["file"] == "golem/utils.py:16"
        assert "truncation" in issues[0]["text"]

    def test_fallback_numbered_issues(self):
        output = (
            "NEEDS_FIXES\n\n"
            "1. golem/utils.py:16 — ms truncation bug\n"
            "2. golem/tests/test_utils.py:45 — missing boundary test\n"
            "3. golem/core/config.py — unused import\n"
        )
        issues = _parse_issues(output)
        assert len(issues) == 3
        assert issues[0]["confidence"] == 0
        assert issues[0]["file"] == "golem/utils.py:16"
        assert "truncation" in issues[0]["text"]

    def test_fallback_bulleted_issues(self):
        output = "NEEDS_FIXES\n\n- golem/flow.py:10 — error handling missing\n"
        issues = _parse_issues(output)
        assert len(issues) == 1
        assert issues[0]["file"] == "golem/flow.py:10"

    def test_empty_output(self):
        assert _parse_issues("") == []
        assert _parse_issues("APPROVED\nAll good.") == []


class TestPhaseDurationEstimation:
    """Tests for phase duration computation edge cases."""

    def test_no_timestamps_uses_subagent_durations(self):
        """Without event timestamps, phase duration = sum of subagent durations."""
        events = [
            _system_init(),
            _assistant_text("## Phase: BUILD\nDispatching..."),
            _agent_tool_use(
                "Build task",
                tool_use_id="tu_b",
                description="Build task",
                model="sonnet",
            ),
            _task_started("tu_b", task_id="t_b"),
            _task_notification("t_b", "tu_b", 10000, 5000, 3),
            _user_tool_result("tu_b", "Done."),
            _result_event(duration_ms=0),
        ]
        result = parse_trace(events)
        build = next(p for p in result["phases"] if p["name"] == "BUILD")
        # With duration_ms=0, no orchestrator time is added — raw subagent only
        assert build["duration_ms"] == 10000
