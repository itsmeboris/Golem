"""Tests for rich tool-call summaries in the golem event tracker."""

# pylint: disable=missing-class-docstring,missing-function-docstring

from golem.event_tracker import (
    TaskEventTracker,
    _short_path,
    _summarize_agent,
    _summarize_skill,
    _summarize_task_create,
    _summarize_task_update,
    _summarize_todo_write,
    _summarize_tool_input,
    _truncate_summary,
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

    def test_long_description_preserved(self):
        long_desc = "x" * 100
        result = _summarize_agent("Agent", {"description": long_desc})
        assert len(result.split("Agent: ")[1]) == 100

    def test_desc_preferred_over_prompt(self):
        result = _summarize_agent("Agent", {"description": "desc", "prompt": "prompt"})
        assert "desc" in result
        assert "prompt" not in result


class TestSummarizeSkill:
    def test_skill_with_name(self):
        assert _summarize_skill("Skill", {"skill": "commit"}) == "Skill: commit"

    def test_skill_with_name_and_args(self):
        result = _summarize_skill("Skill", {"skill": "commit", "args": "-m 'Fix'"})
        assert result == "Skill: commit -m 'Fix'"

    def test_skill_empty(self):
        assert _summarize_skill("Skill", {}) == ""

    def test_skill_via_summarize_tool_input(self):
        assert (
            _summarize_tool_input("Skill", {"skill": "review-pr"}) == "Skill: review-pr"
        )


class TestSummarizeTodoWrite:
    def test_with_items(self):
        todos = [{"id": "1", "text": "a"}, {"id": "2", "text": "b"}]
        assert (
            _summarize_todo_write("TodoWrite", {"todos": todos}) == "TodoWrite: 2 items"
        )

    def test_empty_list(self):
        assert _summarize_todo_write("TodoWrite", {"todos": []}) == "TodoWrite"

    def test_no_todos_key(self):
        assert _summarize_todo_write("TodoWrite", {}) == "TodoWrite"

    def test_via_summarize_tool_input(self):
        todos = [{"id": "1", "text": "item"}]
        result = _summarize_tool_input("TodoWrite", {"todos": todos})
        assert result == "TodoWrite: 1 items"


class TestSummarizeTaskCreate:
    def test_with_description(self):
        result = _summarize_task_create(
            "TaskCreate", {"description": "Fix the login bug"}
        )
        assert result == "TaskCreate: Fix the login bug"

    def test_long_description_truncated(self):
        desc = "x" * 120
        result = _summarize_task_create("TaskCreate", {"description": desc})
        assert len(result) <= len("TaskCreate: ") + 80

    def test_description_with_newlines(self):
        result = _summarize_task_create("TaskCreate", {"description": "line1\nline2"})
        assert "\n" not in result

    def test_empty(self):
        assert _summarize_task_create("TaskCreate", {}) == ""


class TestSummarizeTaskUpdate:
    def test_with_id_and_status(self):
        result = _summarize_task_update(
            "TaskUpdate", {"task_id": "42", "status": "completed"}
        )
        assert result == "TaskUpdate: #42 → completed"

    def test_with_id_only(self):
        result = _summarize_task_update("TaskUpdate", {"task_id": "42"})
        assert result == "TaskUpdate: #42"

    def test_empty(self):
        assert _summarize_task_update("TaskUpdate", {}) == ""


class TestTruncateSummary:
    def test_short_text_unchanged(self):
        assert _truncate_summary("Hello world.") == "Hello world."

    def test_first_line_only(self):
        text = "First line.\nSecond line.\nThird line."
        assert _truncate_summary(text) == "First line."

    def test_long_text_truncated_at_sentence(self):
        text = "This is a sentence. " + "x" * 200
        result = _truncate_summary(text, max_len=60)
        assert result == "This is a sentence."

    def test_long_text_truncated_with_ellipsis(self):
        text = "a" * 200
        result = _truncate_summary(text, max_len=120)
        assert len(result) == 121  # 120 + ellipsis char
        assert result.endswith("\u2026")

    def test_empty_first_line_uses_next(self):
        text = "\n\nActual content here."
        assert _truncate_summary(text) == "Actual content here."

    def test_all_empty(self):
        assert _truncate_summary("") == ""
        assert _truncate_summary("\n\n\n") == ""

    def test_sentence_boundary_too_early_ignored(self):
        # Period at position 2 is too early (< 10), so just truncate
        text = "Hi. " + "x" * 200
        result = _truncate_summary(text, max_len=50)
        assert result == "Hi. " + "x" * 46 + "\u2026"

    def test_whitespace_stripped(self):
        text = "  Hello world.  \n  More text.  "
        assert _truncate_summary(text) == "Hello world."


class TestTextMilestoneTruncation:
    def test_multiline_text_produces_truncated_summary(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "First line of analysis.\nSecond line with details.\nThird line.",
                    }
                ]
            },
        }
        m = tracker.handle_event(event)
        assert m is not None
        assert m.kind == "text"
        assert m.summary == "First line of analysis."
        assert "\n" not in m.summary
        assert m.full_text == "First line of analysis.\nSecond line with details.\nThird line."

    def test_text_milestone_full_text_untruncated(self):
        tracker = TaskEventTracker(session_id=1)
        long_text = "a" * 200
        event = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": long_text}]
            },
        }
        m = tracker.handle_event(event)
        assert m is not None
        assert len(m.summary) == 121  # 120 + ellipsis
        assert m.full_text == long_text  # full text preserved

    def test_to_dict_includes_full_text_for_text_events(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Line one.\nLine two.\nLine three."}
                ]
            },
        }
        tracker.handle_event(event)
        data = tracker.to_dict()
        text_events = [e for e in data["event_log"] if e["kind"] == "text"]
        assert len(text_events) == 1
        assert text_events[0]["full_text"] == "Line one.\nLine two.\nLine three."

    def test_to_dict_omits_full_text_when_empty(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "id": "x",
                        "input": {"file_path": "/tmp/foo.py"},
                    }
                ]
            },
        }
        tracker.handle_event(event)
        data = tracker.to_dict()
        tool_events = [e for e in data["event_log"] if e["kind"] == "tool_call"]
        assert len(tool_events) == 1
        assert "full_text" not in tool_events[0]
