# pylint: disable=too-few-public-methods
"""Tests for golem.trace_parser — JSONL trace parsing."""

from __future__ import annotations

from golem.trace_parser import PHASE_NAMES, PHASE_MARKER_RE, parse_trace


# ---------------------------------------------------------------------------
# Fixture builder helpers
# ---------------------------------------------------------------------------


def _system_init(model="claude-sonnet-4-20250514", tools=None, cwd="/work/wt-12345"):
    """Build a system/init event."""
    return {
        "type": "system",
        "subtype": "init",
        "model": model,
        "tools": tools or ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
        "cwd": cwd,
    }


def _assistant_text(text):
    """Build an assistant event with a text block."""
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    }


def _assistant_tool_use(name, tool_input, tool_use_id="tu_001", extra_content=None):
    """Build an assistant event with a single tool_use block."""
    content = list(extra_content or [])
    content.append(
        {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}
    )
    return {"type": "assistant", "message": {"content": content}}


def _user_tool_result(tool_use_id, content_text, is_error=False):
    """Build a user event with a tool_result block."""
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content_text,
                    "is_error": is_error,
                }
            ]
        },
    }


def _agent_tool_use(
    prompt,
    tool_use_id="tu_agent_001",
    description="Build feature",
    subagent_type="builder",
    model=None,
):
    """Build an assistant event dispatching an Agent subagent.

    model is optional — real CLI traces sometimes omit it (e.g., when using
    the orchestrator's own model). Pass None to test the missing-model case.
    """
    agent_input = {
        "prompt": prompt,
        "description": description,
        "subagent_type": subagent_type,
    }
    if model is not None:
        agent_input["model"] = model
    return _assistant_tool_use("Agent", agent_input, tool_use_id=tool_use_id)


def _task_started(tool_use_id, task_id="task_001"):
    """Build a system/task_started event."""
    return {
        "type": "system",
        "subtype": "task_started",
        "tool_use_id": tool_use_id,
        "task_id": task_id,
    }


def _task_progress(
    task_id, last_tool_name, description, duration_ms, tool_uses, total_tokens=0
):
    """Build a system/task_progress event."""
    return {
        "type": "system",
        "subtype": "task_progress",
        "task_id": task_id,
        "description": description,
        "last_tool_name": last_tool_name,
        "usage": {
            "duration_ms": duration_ms,
            "tool_uses": tool_uses,
            "total_tokens": total_tokens,
        },
    }


def _task_notification(task_id, tool_use_id, duration_ms, total_tokens, tool_uses):
    """Build a system/task_notification event (final stats for a subagent)."""
    return {
        "type": "system",
        "subtype": "task_notification",
        "task_id": task_id,
        "tool_use_id": tool_use_id,
        "usage": {
            "duration_ms": duration_ms,
            "total_tokens": total_tokens,
            "tool_uses": tool_uses,
        },
    }


def _result_event(
    cost_usd=0.42,
    duration_ms=632000,
    num_turns=12,
    result_text="",
    model_usage=None,
):
    """Build a result event."""
    return {
        "type": "result",
        "total_cost_usd": cost_usd,
        "duration_ms": duration_ms,
        "num_turns": num_turns,
        "is_error": False,
        "result": result_text,
        "modelUsage": model_usage or {},
    }


def _build_simple_trace():
    """Build a minimal complete trace with all 5 phases, 1 builder, 2 reviewers, 1 verifier.

    Returns list of event dicts.
    """
    return [
        # System init
        _system_init(),
        # UNDERSTAND phase
        _assistant_text("## Phase: UNDERSTAND\nReading key files..."),
        _assistant_tool_use(
            "Read", {"file_path": "golem/utils.py"}, tool_use_id="tu_read1"
        ),
        _user_tool_result("tu_read1", "def format_duration(s): ..."),
        _assistant_tool_use(
            "Glob", {"pattern": "golem/tests/test_*.py"}, tool_use_id="tu_glob1"
        ),
        _user_tool_result("tu_glob1", "golem/tests/test_utils.py"),
        _assistant_text(
            "Found the module. Standard complexity.\n\n## Phase: PLAN\nSPEC-1..5 defined. One Builder."
        ),
        # BUILD phase
        _assistant_text("## Phase: BUILD\nDispatching Builder..."),
        _agent_tool_use(
            "Implement format_duration edge cases",
            tool_use_id="tu_builder1",
            description="Implement format_duration edge cases",
            model="sonnet",
        ),
        _task_started("tu_builder1", task_id="task_builder1"),
        # simulated progress
        _task_progress("task_builder1", "Read", "golem/utils.py", 5000, 1, 1200),
        _task_progress("task_builder1", "Write", "golem/utils.py", 15000, 3, 5400),
        _task_progress("task_builder1", "Bash", "pytest -x", 45000, 6, 12000),
        _task_notification("task_builder1", "tu_builder1", 222000, 34800, 27),
        _user_tool_result(
            "tu_builder1", "Files changed: golem/utils.py\nSelf-verification: all pass"
        ),
        # REVIEW phase
        _assistant_text("## Phase: REVIEW\nStarting two-stage review."),
        _agent_tool_use(
            "Spec compliance review",
            tool_use_id="tu_reviewer1",
            description="Spec compliance review",
            subagent_type="spec_reviewer",
            model="sonnet",
        ),
        _task_started("tu_reviewer1", task_id="task_reviewer1"),
        _task_notification("task_reviewer1", "tu_reviewer1", 72000, 20900, 7),
        _user_tool_result("tu_reviewer1", "APPROVED\nAll 5 specs verified."),
        _assistant_text("Spec review passed. Dispatching quality review."),
        _agent_tool_use(
            "Code quality review",
            tool_use_id="tu_reviewer2",
            description="Code quality review",
            subagent_type="quality_reviewer",
            model="sonnet",
        ),
        _task_started("tu_reviewer2", task_id="task_reviewer2"),
        _task_notification("task_reviewer2", "tu_reviewer2", 108000, 64100, 14),
        _user_tool_result(
            "tu_reviewer2",
            "NEEDS_FIXES\n\nIssues (confidence >= 80%):\n"
            "1. [95%] golem/utils.py:16 — ms truncation bug\n"
            "2. [85%] golem/tests/test_utils.py:45 — missing boundary test\n"
            "3. [80%] golem/utils.py:28 — day format omits minutes",
        ),
        # Fix cycle: dispatch fix builder
        _assistant_text("Quality review found 3 issues. Dispatching fix builder."),
        _agent_tool_use(
            "Fix quality review issues",
            tool_use_id="tu_fixer1",
            description="Fix quality review issues",
            subagent_type="builder",
            model="sonnet",
        ),
        _task_started("tu_fixer1", task_id="task_fixer1"),
        _task_notification("task_fixer1", "tu_fixer1", 48000, 19200, 13),
        _user_tool_result(
            "tu_fixer1", "Fixed all 3 issues.\nSelf-verification: all pass"
        ),
        # Re-check: dispatch quality reviewer again
        _assistant_text("Fix applied. Re-running quality review."),
        _agent_tool_use(
            "Code quality re-check",
            tool_use_id="tu_reviewer3",
            description="Code quality re-check",
            subagent_type="quality_reviewer",
            model="sonnet",
        ),
        _task_started("tu_reviewer3", task_id="task_reviewer3"),
        _task_notification("task_reviewer3", "tu_reviewer3", 60000, 18000, 8),
        _user_tool_result("tu_reviewer3", "APPROVED\nAll issues resolved."),
        # VERIFY phase
        _assistant_text("## Phase: VERIFY\nAll reviews passed. Running verification."),
        _agent_tool_use(
            "Full test suite verification",
            tool_use_id="tu_verifier1",
            description="Full test suite verification",
            subagent_type="verifier",
        ),
        _task_started("tu_verifier1", task_id="task_verifier1"),
        _task_notification("task_verifier1", "tu_verifier1", 66000, 14400, 3),
        _user_tool_result(
            "tu_verifier1", "black: PASS\npylint: PASS\npytest: 148 passed, 100% cov"
        ),
        _assistant_text("Verification passed. Task complete."),
        # Result
        _result_event(
            cost_usd=0.42,
            duration_ms=632000,
            num_turns=12,
            result_text=(
                "Task complete.\n\n```json\n"
                '{"status": "COMPLETE", "summary": "Fixed format_duration edge cases", '
                '"files_changed": ["golem/utils.py", "golem/tests/test_utils.py"], '
                '"test_results": {"black": "pass", "pylint": "pass", "pytest": "148 passed 100%"}, '
                '"specs_satisfied": {"SPEC-1": true, "SPEC-2": true, "SPEC-3": true}, '
                '"concerns": []}\n```'
            ),
            model_usage={
                "claude-sonnet-4-20250514": {
                    "input_tokens": 120000,
                    "output_tokens": 37000,
                    "cost_usd": 0.38,
                },
                "claude-opus-4-20250514": {
                    "input_tokens": 3000,
                    "output_tokens": 800,
                    "cost_usd": 0.04,
                },
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Task 1.1: Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_phase_names(self):
        assert PHASE_NAMES == ("UNDERSTAND", "PLAN", "BUILD", "REVIEW", "VERIFY")

    def test_phase_marker_regex_matches(self):
        assert PHASE_MARKER_RE.search("## Phase: UNDERSTAND")
        assert PHASE_MARKER_RE.search("## Phase: BUILD")

    def test_phase_marker_regex_no_match(self):
        assert not PHASE_MARKER_RE.search("## Phase: INVALID")
        assert not PHASE_MARKER_RE.search("Phase: BUILD")


# ---------------------------------------------------------------------------
# Task 1.2: Fixture
# ---------------------------------------------------------------------------


class TestFixture:
    def test_simple_trace_has_all_event_types(self):
        events = _build_simple_trace()
        types = {ev["type"] for ev in events}
        assert types == {"system", "assistant", "user", "result"}

    def test_simple_trace_has_all_phases(self):
        events = _build_simple_trace()
        text = " ".join(
            block["text"]
            for ev in events
            if ev["type"] == "assistant"
            for block in ev.get("message", {}).get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
        for phase in PHASE_NAMES:
            assert f"## Phase: {phase}" in text


# ---------------------------------------------------------------------------
# Task 1.3: Phase detection
# ---------------------------------------------------------------------------


class TestPhaseDetection:
    def test_simple_trace_detects_all_phases(self):
        result = parse_trace(_build_simple_trace())
        phase_names = [p["name"] for p in result["phases"]]
        assert phase_names == ["UNDERSTAND", "PLAN", "BUILD", "REVIEW", "VERIFY"]

    def test_two_markers_in_one_event(self):
        """PLAN + BUILD can share one assistant message."""
        events = [
            _system_init(),
            _assistant_text("## Phase: UNDERSTAND\nReading..."),
            # Two phase markers in one event (common pattern)
            _assistant_text(
                "Found the module.\n\n## Phase: PLAN\nOne Builder.\n\n## Phase: BUILD\nDispatching..."
            ),
            _result_event(),
        ]
        result = parse_trace(events)
        names = [p["name"] for p in result["phases"]]
        assert "UNDERSTAND" in names
        assert "PLAN" in names
        assert "BUILD" in names

    def test_empty_events(self):
        result = parse_trace([])
        assert result["phases"] == []

    def test_no_phase_markers_and_no_subagents(self):
        """Events without phase markers and no Agent dispatches -> empty phases.

        Note: after Task 2.4 adds fallback inference, this still returns empty
        because there are no subagents to infer phases from.
        """
        events = [_system_init(), _assistant_text("Hello world"), _result_event()]
        result = parse_trace(events)
        assert result["phases"] == []

    def test_phase_has_start_and_end_event(self):
        result = parse_trace(_build_simple_trace())
        for phase in result["phases"]:
            assert "start_event" in phase
            assert "end_event" in phase
            assert phase["start_event"] >= 0
            assert phase["end_event"] >= phase["start_event"]


# ---------------------------------------------------------------------------
# Task 1.4: Orchestrator content
# ---------------------------------------------------------------------------


class TestOrchestratorContent:
    def test_understand_has_orchestrator_text(self):
        result = parse_trace(_build_simple_trace())
        understand = result["phases"][0]
        assert "Reading key files" in understand["orchestrator_text"]

    def test_understand_has_orchestrator_tools(self):
        result = parse_trace(_build_simple_trace())
        understand = result["phases"][0]
        tool_names = [t["tool"] for t in understand["orchestrator_tools"]]
        assert "Read" in tool_names
        assert "Glob" in tool_names

    def test_orchestrator_tool_has_description(self):
        result = parse_trace(_build_simple_trace())
        understand = result["phases"][0]
        read_tool = next(
            t for t in understand["orchestrator_tools"] if t["tool"] == "Read"
        )
        assert "golem/utils.py" in read_tool["description"]

    def test_orchestrator_tool_has_result_preview(self):
        result = parse_trace(_build_simple_trace())
        understand = result["phases"][0]
        read_tool = next(
            t for t in understand["orchestrator_tools"] if t["tool"] == "Read"
        )
        assert "format_duration" in read_tool["result_preview"]

    def test_agent_tool_uses_not_in_orchestrator_tools(self):
        """Agent dispatches should not appear as orchestrator tools."""
        result = parse_trace(_build_simple_trace())
        for phase in result["phases"]:
            for tool in phase["orchestrator_tools"]:
                assert tool["tool"] != "Agent"


# ---------------------------------------------------------------------------
# Task 1.5: Subagent grouping
# ---------------------------------------------------------------------------


class TestSubagentGrouping:
    def test_build_phase_has_one_builder(self):
        result = parse_trace(_build_simple_trace())
        build = next(p for p in result["phases"] if p["name"] == "BUILD")
        assert len(build["subagents"]) == 1
        assert build["subagents"][0]["role"] == "builder"

    def test_builder_has_prompt_and_output(self):
        result = parse_trace(_build_simple_trace())
        build = next(p for p in result["phases"] if p["name"] == "BUILD")
        builder = build["subagents"][0]
        assert "format_duration" in builder["prompt"]
        assert "Files changed" in builder["output"]

    def test_builder_has_stats(self):
        result = parse_trace(_build_simple_trace())
        build = next(p for p in result["phases"] if p["name"] == "BUILD")
        builder = build["subagents"][0]
        assert builder["duration_ms"] == 222000
        assert builder["tokens"] == 34800
        assert builder["tool_count"] == 27

    def test_builder_has_description(self):
        result = parse_trace(_build_simple_trace())
        build = next(p for p in result["phases"] if p["name"] == "BUILD")
        assert (
            build["subagents"][0]["description"]
            == "Implement format_duration edge cases"
        )

    def test_review_phase_has_reviewers(self):
        result = parse_trace(_build_simple_trace())
        review = next(p for p in result["phases"] if p["name"] == "REVIEW")
        roles = [s["role"] for s in review["subagents"]]
        assert "spec_reviewer" in roles
        assert "quality_reviewer" in roles

    def test_reviewer_status_extracted_from_output(self):
        result = parse_trace(_build_simple_trace())
        review = next(p for p in result["phases"] if p["name"] == "REVIEW")
        spec_reviewer = next(
            s for s in review["subagents"] if s["role"] == "spec_reviewer"
        )
        assert spec_reviewer["status"] == "APPROVED"
        quality_reviewer = [
            s for s in review["subagents"] if s["role"] == "quality_reviewer"
        ]
        # First quality review has NEEDS_FIXES
        assert quality_reviewer[0]["status"] == "NEEDS_FIXES"

    def test_verify_phase_has_verifier(self):
        result = parse_trace(_build_simple_trace())
        verify = next(p for p in result["phases"] if p["name"] == "VERIFY")
        assert len(verify["subagents"]) >= 1
        assert verify["subagents"][0]["role"] == "verifier"

    def test_subagent_model_field(self):
        result = parse_trace(_build_simple_trace())
        build = next(p for p in result["phases"] if p["name"] == "BUILD")
        assert build["subagents"][0]["model"] == "sonnet"

    def test_subagent_model_defaults_when_missing(self):
        """Verifier in fixture omits model — should default to 'unknown'."""
        result = parse_trace(_build_simple_trace())
        verify = next(p for p in result["phases"] if p["name"] == "VERIFY")
        assert verify["subagents"][0]["model"] == "unknown"

    def test_subagent_has_task_and_tool_use_ids(self):
        result = parse_trace(_build_simple_trace())
        build = next(p for p in result["phases"] if p["name"] == "BUILD")
        builder = build["subagents"][0]
        assert builder["task_id"] == "task_builder1"
        assert builder["tool_use_id"] == "tu_builder1"

    def test_total_subagent_count(self):
        result = parse_trace(_build_simple_trace())
        total = sum(len(p["subagents"]) for p in result["phases"])
        # builder, spec_reviewer, quality_reviewer, fixer, re-checker, verifier = 6
        assert total == 6


# ---------------------------------------------------------------------------
# Task 1.6: Tool timeline
# ---------------------------------------------------------------------------


class TestToolTimeline:
    def test_builder_has_tool_timeline(self):
        result = parse_trace(_build_simple_trace())
        build = next(p for p in result["phases"] if p["name"] == "BUILD")
        builder = build["subagents"][0]
        assert len(builder["tool_timeline"]) == 3  # Read, Write, Bash

    def test_tool_timeline_has_correct_tools(self):
        result = parse_trace(_build_simple_trace())
        build = next(p for p in result["phases"] if p["name"] == "BUILD")
        tools = [t["tool"] for t in build["subagents"][0]["tool_timeline"]]
        assert tools == ["Read", "Write", "Bash"]

    def test_tool_timeline_has_descriptions(self):
        result = parse_trace(_build_simple_trace())
        build = next(p for p in result["phases"] if p["name"] == "BUILD")
        descs = [t["description"] for t in build["subagents"][0]["tool_timeline"]]
        assert "golem/utils.py" in descs[0]

    def test_tool_timeline_delta_ms(self):
        result = parse_trace(_build_simple_trace())
        build = next(p for p in result["phases"] if p["name"] == "BUILD")
        tl = build["subagents"][0]["tool_timeline"]
        # First tool: delta = cumulative (5000)
        assert tl[0]["cumulative_ms"] == 5000
        assert tl[0]["delta_ms"] == 5000
        # Second tool: delta = 15000 - 5000 = 10000
        assert tl[1]["cumulative_ms"] == 15000
        assert tl[1]["delta_ms"] == 10000
        # Third tool: delta = 45000 - 15000 = 30000
        assert tl[2]["cumulative_ms"] == 45000
        assert tl[2]["delta_ms"] == 30000

    def test_tool_timeline_cumulative_tools(self):
        result = parse_trace(_build_simple_trace())
        build = next(p for p in result["phases"] if p["name"] == "BUILD")
        tl = build["subagents"][0]["tool_timeline"]
        assert tl[0]["cumulative_tools"] == 1
        assert tl[1]["cumulative_tools"] == 3
        assert tl[2]["cumulative_tools"] == 6


# ---------------------------------------------------------------------------
# Task 1.7: Coverage gap tests
# ---------------------------------------------------------------------------


class TestToolResultMapCoverage:
    """Tests to cover _build_tool_result_map branches not exercised elsewhere."""

    def test_list_content_joined(self):
        """tool_result whose content is a list of text block dicts gets joined."""
        events = [
            _assistant_text("## Phase: UNDERSTAND\nReading..."),
            _assistant_tool_use("Read", {"file_path": "foo.py"}, tool_use_id="tu_r1"),
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_r1",
                            "content": [
                                {"type": "text", "text": "part1"},
                                {"type": "text", "text": "part2"},
                            ],
                        }
                    ]
                },
            },
        ]
        result = parse_trace(events)
        understand = result["phases"][0]
        read_tool = next(
            t for t in understand["orchestrator_tools"] if t["tool"] == "Read"
        )
        assert read_tool["result_preview"] == "part1\npart2"

    def test_list_content_skips_non_dict_elements(self):
        """Non-dict items in a list content are silently skipped."""
        events = [
            _assistant_text("## Phase: UNDERSTAND\nReading..."),
            _assistant_tool_use("Read", {"file_path": "bar.py"}, tool_use_id="tu_r2"),
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_r2",
                            "content": [
                                "bare-string",
                                {"type": "text", "text": "valid"},
                            ],
                        }
                    ]
                },
            },
        ]
        result = parse_trace(events)
        understand = result["phases"][0]
        read_tool = next(
            t for t in understand["orchestrator_tools"] if t["tool"] == "Read"
        )
        # bare string is skipped; only "valid" remains
        assert read_tool["result_preview"] == "valid"

    def test_non_dict_block_in_user_content_skipped(self):
        """A non-dict element directly in user message content is skipped."""
        events = [
            _assistant_text("## Phase: UNDERSTAND\nReading..."),
            _assistant_tool_use("Read", {"file_path": "baz.py"}, tool_use_id="tu_r3"),
            {
                "type": "user",
                "message": {
                    "content": [
                        "just a bare string",
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_r3",
                            "content": "actual result",
                        },
                    ]
                },
            },
        ]
        result = parse_trace(events)
        understand = result["phases"][0]
        read_tool = next(
            t for t in understand["orchestrator_tools"] if t["tool"] == "Read"
        )
        assert read_tool["result_preview"] == "actual result"


class TestSummarizeToolDescriptionCoverage:
    """Tests to cover _summarize_tool_description branches not yet exercised."""

    def _make_phase_with_tool(self, tool_name, tool_input, tool_use_id="tu_x1"):
        """Build a minimal trace with one orchestrator tool in a phase."""
        return [
            _assistant_text("## Phase: UNDERSTAND\nDoing stuff..."),
            _assistant_tool_use(tool_name, tool_input, tool_use_id=tool_use_id),
            _user_tool_result(tool_use_id, "ok"),
        ]

    def test_write_tool_description_uses_file_path(self):
        events = self._make_phase_with_tool("Write", {"file_path": "golem/new_file.py"})
        result = parse_trace(events)
        understand = result["phases"][0]
        tool = next(t for t in understand["orchestrator_tools"] if t["tool"] == "Write")
        assert tool["description"] == "golem/new_file.py"

    def test_edit_tool_description_uses_file_path(self):
        events = self._make_phase_with_tool("Edit", {"file_path": "golem/edit_me.py"})
        result = parse_trace(events)
        understand = result["phases"][0]
        tool = next(t for t in understand["orchestrator_tools"] if t["tool"] == "Edit")
        assert tool["description"] == "golem/edit_me.py"

    def test_bash_tool_description_uses_command(self):
        events = self._make_phase_with_tool("Bash", {"command": "pytest -x -v"})
        result = parse_trace(events)
        understand = result["phases"][0]
        tool = next(t for t in understand["orchestrator_tools"] if t["tool"] == "Bash")
        assert tool["description"] == "pytest -x -v"

    def test_bash_tool_description_truncated_at_120(self):
        long_cmd = "echo " + "x" * 200
        events = self._make_phase_with_tool("Bash", {"command": long_cmd})
        result = parse_trace(events)
        understand = result["phases"][0]
        tool = next(t for t in understand["orchestrator_tools"] if t["tool"] == "Bash")
        assert len(tool["description"]) == 120

    def test_unknown_tool_description_falls_back_to_str(self):
        events = self._make_phase_with_tool("UnknownTool", {"some_key": "some_value"})
        result = parse_trace(events)
        understand = result["phases"][0]
        tool = next(
            t for t in understand["orchestrator_tools"] if t["tool"] == "UnknownTool"
        )
        assert "some_key" in tool["description"] or "some_value" in tool["description"]


class TestSinceEventDocumented:
    """Verify since_event is stored in output (not used for incremental filtering)."""

    def test_since_event_stored_in_result(self):
        result = parse_trace(_build_simple_trace(), since_event=5)
        assert result["since_event"] == 5

    def test_since_event_default_zero(self):
        result = parse_trace(_build_simple_trace())
        assert result["since_event"] == 0
