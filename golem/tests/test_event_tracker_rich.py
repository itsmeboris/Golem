"""Tests for rich tool-call summaries in the golem event tracker."""

# pylint: disable=missing-class-docstring,missing-function-docstring

from golem.event_tracker import (
    TaskEventTracker,
    _short_path,
    _summarize_agent,
    _summarize_tool_input,
)


def _tool_use_event(name, tool_input=None):
    """Helper: build a minimal assistant event with a tool_use block."""
    block = {"type": "tool_use", "name": name}
    if tool_input is not None:
        block["input"] = tool_input
    return {"type": "assistant", "message": {"content": [block]}}


class TestShortPath:
    def test_short_path_long(self):
        assert _short_path("/a/b/c/d/e.py") == ".../c/d/e.py"

    def test_short_path_short(self):
        assert _short_path("a/b/c") == "a/b/c"

    def test_short_path_single(self):
        assert _short_path("file.py") == "file.py"


class TestSummarizeToolInput:
    def test_bash_with_description(self):
        assert (
            _summarize_tool_input(
                "Bash",
                {
                    "command": "pytest tests/test_run_log.py -v",
                    "description": "Run run_log tests",
                },
            )
            == "Bash: Run run_log tests"
        )

    def test_bash_no_description(self):
        assert (
            _summarize_tool_input(
                "Bash",
                {
                    "command": "git diff --stat",
                },
            )
            == "Bash: git diff --stat"
        )

    def test_bash_empty_input(self):
        assert _summarize_tool_input("Bash", {}) == "Called Bash"

    def test_read(self):
        s = _summarize_tool_input("Read", {"file_path": "/project/core/run_log.py"})
        assert "Read" in s
        assert "run_log.py" in s

    def test_edit(self):
        s = _summarize_tool_input("Edit", {"file_path": "/project/core/run_log.py"})
        assert "Edit" in s
        assert "run_log.py" in s

    def test_write(self):
        s = _summarize_tool_input("Write", {"file_path": "/tmp/out.txt"})
        assert "Write" in s
        assert "out.txt" in s

    def test_glob(self):
        assert _summarize_tool_input("Glob", {"pattern": "**/*.py"}) == "Glob: **/*.py"

    def test_grep_with_path(self):
        s = _summarize_tool_input(
            "Grep",
            {
                "pattern": "format_duration",
                "path": "/project/core/",
            },
        )
        assert "format_duration" in s
        assert "Grep" in s

    def test_grep_no_path(self):
        s = _summarize_tool_input("Grep", {"pattern": "format_duration"})
        assert s == "Grep 'format_duration'"

    def test_task(self):
        s = _summarize_tool_input("Task", {"description": "research approach"})
        assert s == "Task: research approach"

    def test_toolsearch(self):
        s = _summarize_tool_input("ToolSearch", {"query": "select:mcp__redmine"})
        assert "select:mcp__redmine" in s

    def test_mcp_tool(self):
        s = _summarize_tool_input("mcp__redmine__redmine_get_issue", {"issue_id": 1})
        assert s == "MCP: redmine_get_issue"

    def test_unknown_tool(self):
        assert _summarize_tool_input("FooBar", {}) == "Called FooBar"


class TestRichSummaryIntegration:
    """End-to-end: tool_use events through TaskEventTracker produce rich summaries."""

    def test_bash_event(self):
        tracker = TaskEventTracker(session_id=1)
        m = tracker.handle_event(
            _tool_use_event(
                "Bash",
                {
                    "command": "pytest tests/ -v",
                    "description": "Run all tests",
                },
            )
        )
        assert m.summary == "Bash: Run all tests"

    def test_read_event(self):
        tracker = TaskEventTracker(session_id=1)
        m = tracker.handle_event(
            _tool_use_event(
                "Read",
                {
                    "file_path": "/project/core/run_log.py",
                },
            )
        )
        assert "run_log.py" in m.summary

    def test_mcp_event(self):
        tracker = TaskEventTracker(session_id=1)
        m = tracker.handle_event(
            _tool_use_event(
                "mcp__redmine__redmine_get_issue",
                {
                    "issue_id": 123,
                },
            )
        )
        assert m.summary == "MCP: redmine_get_issue"

    def test_fallback_no_input(self):
        tracker = TaskEventTracker(session_id=1)
        m = tracker.handle_event(_tool_use_event("Bash"))
        assert m.summary == "Called Bash"


class TestToolCallCompletedNoError:
    def test_completed_no_error_returns_none(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "tool_call",
            "subtype": "completed",
            "tool_call": {"mcpToolCall": {"result": {}}},
        }
        m = tracker.handle_event(event)
        assert m is None


class TestAssistantNonDictBlock:
    def test_non_dict_block_skipped(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "assistant",
            "message": {
                "content": ["just a string", {"type": "text", "text": "hello."}]
            },
        }
        m = tracker.handle_event(event)
        assert m is not None
        assert m.kind == "text"

    def test_tool_result_block_in_assistant(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_result", "is_error": True, "content": "bad stuff"},
                ]
            },
        }
        m = tracker.handle_event(event)
        assert m is not None
        assert m.is_error is True


class TestToolResultListContent:
    def test_list_content_joined(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "tool_result",
            "is_error": False,
            "content": [{"text": "part1"}, {"text": "part2"}],
        }
        m = tracker.handle_event(event)
        assert m is not None
        assert "part1" in m.summary
        assert "part2" in m.summary


class TestFindContentBlocksMessagePath:
    def test_message_content_path(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "via message."}]},
        }
        m = tracker.handle_event(event)
        assert m is not None
        assert "via message" in m.summary

    def test_dict_content_block(self):
        blocks = TaskEventTracker._find_content_blocks(
            {"content_block": {"type": "text", "text": "hi"}}
        )
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"

    def test_no_content_returns_empty(self):
        blocks = TaskEventTracker._find_content_blocks({"type": "unknown"})
        assert not blocks


class TestSessionIdCapture:
    def test_session_id_from_init_event(self):
        tracker = TaskEventTracker(session_id=1)
        event = {"type": "system", "subtype": "init", "session_id": "abc-123"}
        tracker.handle_event(event)
        assert tracker.state.session_id == "abc-123"

    def test_session_id_only_first_init(self):
        tracker = TaskEventTracker(session_id=1)
        tracker.handle_event(
            {"type": "system", "subtype": "init", "session_id": "first"}
        )
        tracker.handle_event(
            {"type": "system", "subtype": "init", "session_id": "second"}
        )
        assert tracker.state.session_id == "first"

    def test_no_session_id_in_event(self):
        tracker = TaskEventTracker(session_id=1)
        tracker.handle_event({"type": "system", "subtype": "init"})
        assert tracker.state.session_id == ""


class TestSummarizeAgent:
    def test_with_type_and_description(self):
        result = _summarize_agent(
            "Agent", {"subagent_type": "Explore", "description": "Find config files"}
        )
        assert result == "Agent: [Explore] Find config files"

    def test_with_type_and_prompt_no_desc(self):
        result = _summarize_agent(
            "Agent", {"subagent_type": "general-purpose", "prompt": "Search for bugs"}
        )
        assert result == "Agent: [general-purpose] Search for bugs"

    def test_with_only_description(self):
        result = _summarize_agent("Agent", {"description": "Analyze code"})
        assert result == "Agent: Analyze code"

    def test_with_only_prompt(self):
        result = _summarize_agent(
            "Agent", {"prompt": "Run the tests\nand check output"}
        )
        assert result == "Agent: Run the tests and check output"

    def test_empty_input(self):
        result = _summarize_agent("Agent", {})
        assert result == ""

    def test_long_description_truncated(self):
        long_desc = "x" * 100
        result = _summarize_agent("Agent", {"description": long_desc})
        assert len(result.split("Agent: ")[1]) == 80

    def test_desc_preferred_over_prompt(self):
        result = _summarize_agent("Agent", {"description": "desc", "prompt": "prompt"})
        assert "desc" in result
        assert "prompt" not in result
