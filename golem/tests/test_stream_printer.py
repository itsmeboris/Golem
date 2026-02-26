# pylint: disable=too-few-public-methods
"""Tests for golem.core.stream_printer — event pretty-printing."""

import io

from golem.core.stream_printer import StreamPrinter


def _printer():
    buf = io.StringIO()
    return StreamPrinter(buf), buf


class TestStreamPrinterHandleResult:
    def test_result_event(self):
        sp, buf = _printer()
        sp.handle({"type": "result", "cost_usd": 1.50, "duration_ms": 45000})
        output = buf.getvalue()
        assert "$1.50" in output
        assert "45s" in output

    def test_result_zero_cost(self):
        sp, buf = _printer()
        sp.handle({"type": "result", "cost_usd": 0, "duration_ms": 0})
        assert "$0.00" in buf.getvalue()


class TestStreamPrinterAssistant:
    def test_text_block(self):
        sp, buf = _printer()
        sp.handle(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello world."}]},
            }
        )
        assert "Hello world" in buf.getvalue()

    def test_tool_use_block(self):
        sp, buf = _printer()
        sp.handle(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Read"}]},
            }
        )
        assert "Read" in buf.getvalue()

    def test_deduplicates_tools(self):
        sp, buf = _printer()
        for _ in range(3):
            sp.handle(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "Write"}]},
                }
            )
        assert buf.getvalue().count("Write") == 1

    def test_text_not_flushed_mid_sentence(self):
        sp, buf = _printer()
        sp.handle(
            {
                "type": "assistant",
                "content": [{"type": "text", "text": "partial text with no ending"}],
            }
        )
        assert buf.getvalue() == ""

    def test_text_flushed_at_sentence_end(self):
        sp, buf = _printer()
        sp.handle(
            {
                "type": "assistant",
                "content": [{"type": "text", "text": "Sentence one."}],
            }
        )
        assert "Sentence one" in buf.getvalue()


class TestStreamPrinterToolResult:
    def test_error_result(self):
        sp, buf = _printer()
        sp.handle(
            {
                "type": "tool_result",
                "is_error": True,
                "content": "something failed",
            }
        )
        assert "something failed" in buf.getvalue()

    def test_success_result(self):
        sp, buf = _printer()
        sp.handle(
            {
                "type": "tool_result",
                "is_error": False,
                "content": "ok done",
            }
        )
        assert "ok done" in buf.getvalue()

    def test_list_content(self):
        sp, buf = _printer()
        sp.handle(
            {
                "type": "tool_result",
                "is_error": False,
                "content": [{"text": "part1"}, {"text": "part2"}],
            }
        )
        assert "part1" in buf.getvalue()
        assert "part2" in buf.getvalue()

    def test_long_result_truncated(self):
        sp, buf = _printer()
        sp.handle(
            {
                "type": "tool_result",
                "is_error": False,
                "content": "x" * 200,
            }
        )
        output = buf.getvalue()
        assert len(output) < 200


class TestStreamPrinterToolCall:
    def test_started(self):
        sp, buf = _printer()
        sp.handle(
            {
                "type": "tool_call",
                "subtype": "started",
                "tool_call": {"mcpToolCall": {"args": {"toolName": "redmine_search"}}},
            }
        )
        assert "redmine_search" in buf.getvalue()

    def test_completed_rejected(self):
        sp, buf = _printer()
        sp.handle(
            {
                "type": "tool_call",
                "subtype": "completed",
                "tool_call": {
                    "mcpToolCall": {"result": {"rejected": {"reason": "not allowed"}}}
                },
            }
        )
        assert "not allowed" in buf.getvalue()


class TestStreamPrinterDeduplication:
    def test_duplicate_text_suppressed(self):
        sp, buf = _printer()
        for _ in range(3):
            sp.handle(
                {
                    "type": "assistant",
                    "content": [{"type": "text", "text": "Same sentence repeated."}],
                }
            )
        lines = [l for l in buf.getvalue().strip().split("\n") if l.strip()]
        assert len(lines) == 1

    def test_json_looking_text_suppressed(self):
        sp, buf = _printer()
        sp.handle(
            {
                "type": "assistant",
                "content": [{"type": "text", "text": '```json\n{"action": "do"}.'}],
            }
        )
        output = buf.getvalue()
        assert "action" not in output


class TestStreamPrinterHelpers:
    def test_ends_sentence(self):
        assert StreamPrinter._ends_sentence("done.") is True
        assert StreamPrinter._ends_sentence("done!") is True
        assert StreamPrinter._ends_sentence("done?") is True
        assert StreamPrinter._ends_sentence("line\n") is True
        assert StreamPrinter._ends_sentence("partial") is False
        assert StreamPrinter._ends_sentence("") is False

    def test_looks_like_json(self):
        assert StreamPrinter._looks_like_json('```json\n{"x": 1}') is True
        assert (
            StreamPrinter._looks_like_json('{"action": "x", "code_review_label": "y"}')
            is True
        )
        assert StreamPrinter._looks_like_json("hello world") is False

    def test_find_content_blocks_dict(self):
        event = {"content_block": {"type": "text", "text": "hi"}}
        blocks = StreamPrinter._find_content_blocks(event)
        assert len(blocks) == 1

    def test_find_content_blocks_empty(self):
        blocks = StreamPrinter._find_content_blocks({})
        assert not blocks

    def test_looks_like_json_action_code_review(self):
        assert (
            StreamPrinter._looks_like_json(
                'something "action": "fix" and "code_review_label": "bug".'
            )
            is True
        )

    def test_looks_like_json_root_cause_category(self):
        assert (
            StreamPrinter._looks_like_json(
                '"root_cause": "race" and "category": "concurrency".'
            )
            is True
        )


class TestStreamPrinterNonDictBlock:
    def test_non_dict_block_skipped(self):
        sp, buf = _printer()
        sp.handle(
            {
                "type": "assistant",
                "message": {
                    "content": ["raw string", {"type": "text", "text": "real text."}]
                },
            }
        )
        assert "real text" in buf.getvalue()

    def test_tool_result_block_in_assistant(self):
        sp, buf = _printer()
        sp.handle(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_result", "is_error": True, "content": "err msg"},
                    ]
                },
            }
        )
        assert "err msg" in buf.getvalue()


class TestStreamPrinterEmptyFlush:
    def test_empty_text_after_join_not_printed(self):
        sp, buf = _printer()
        sp._text_buf = ["   ", "  "]
        sp._flush_text()
        assert buf.getvalue() == ""


class TestStreamPrinterFuzzyDuplicate:
    def test_fuzzy_prefix_match(self):
        sp, buf = _printer()
        base = "A" * 65
        sp.handle(
            {
                "type": "assistant",
                "content": [{"type": "text", "text": base + " first version."}],
            }
        )
        sp.handle(
            {
                "type": "assistant",
                "content": [{"type": "text", "text": base + " second version."}],
            }
        )
        lines = [l for l in buf.getvalue().strip().split("\n") if l.strip()]
        assert len(lines) == 1
