"""Tests for rich tool-call summaries in the golem event tracker."""

# pylint: disable=missing-class-docstring,missing-function-docstring

from golem.event_tracker import (
    TaskEventTracker,
    _short_path,
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
