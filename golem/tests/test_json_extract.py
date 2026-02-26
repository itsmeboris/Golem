# pylint: disable=too-few-public-methods,implicit-str-concat
"""Tests for golem.core.json_extract — pure JSON extraction logic."""

from golem.core.json_extract import extract_json


class TestExtractJsonFullParse:
    def test_plain_json_object(self):
        text = '{"key": "value", "num": 42}'
        assert extract_json(text) == {"key": "value", "num": 42}

    def test_plain_json_with_whitespace(self):
        text = '  \n  {"key": "value"}  \n  '
        assert extract_json(text) == {"key": "value"}

    def test_plain_json_array_rejected(self):
        assert extract_json("[1, 2, 3]") is None

    def test_plain_json_string_rejected(self):
        assert extract_json('"hello"') is None

    def test_empty_string(self):
        assert extract_json("") is None

    def test_none_like_blanks(self):
        assert extract_json("   ") is None
        assert extract_json("\n\t") is None


class TestExtractJsonRequireKey:
    def test_matching_key(self):
        text = '{"verdict": "PASS", "confidence": 0.9}'
        result = extract_json(text, require_key="verdict")
        assert result is not None
        assert result["verdict"] == "PASS"

    def test_missing_key(self):
        text = '{"other": "value"}'
        assert extract_json(text, require_key="verdict") is None

    def test_no_require_key(self):
        text = '{"any": "thing"}'
        assert extract_json(text) == {"any": "thing"}


class TestExtractJsonFencedBlocks:
    def test_json_code_fence(self):
        text = 'Here is the result:\n```json\n{"verdict": "PASS"}\n```\nDone.'
        result = extract_json(text, require_key="verdict")
        assert result == {"verdict": "PASS"}

    def test_bare_code_fence(self):
        text = 'Output:\n```\n{"key": 123}\n```'
        assert extract_json(text) == {"key": 123}

    def test_multiple_fences_prefers_last(self):
        text = (
            '```json\n{"verdict": "FAIL"}\n```\n'
            "More text\n"
            '```json\n{"verdict": "PASS"}\n```'
        )
        result = extract_json(text, require_key="verdict")
        assert result["verdict"] == "PASS"

    def test_fence_with_require_key_skips_non_matching(self):
        text = '```json\n{"other": 1}\n```\n' '```json\n{"verdict": "PASS"}\n```'
        result = extract_json(text, require_key="verdict")
        assert result["verdict"] == "PASS"

    def test_fence_with_invalid_json(self):
        text = "```json\nnot valid json\n```"
        assert extract_json(text) is None


class TestExtractJsonBraceMatching:
    def test_embedded_json(self):
        text = 'The answer is {"verdict": "PASS", "confidence": 0.95} and done.'
        result = extract_json(text, require_key="verdict")
        assert result is not None
        assert result["verdict"] == "PASS"

    def test_nested_braces(self):
        text = 'Result: {"outer": {"inner": "value"}, "ok": true}'
        result = extract_json(text)
        assert result == {"outer": {"inner": "value"}, "ok": True}

    def test_braces_in_strings(self):
        text = 'data: {"msg": "use {curly} braces", "n": 1}'
        result = extract_json(text)
        assert result == {"msg": "use {curly} braces", "n": 1}

    def test_escaped_quotes(self):
        text = r'result: {"msg": "say \"hello\"", "n": 1}'
        result = extract_json(text)
        assert result is not None
        assert result["n"] == 1

    def test_tiny_objects_ignored(self):
        text = 'x {a} y {"verdict": "PASS", "confidence": 0.9}'
        result = extract_json(text, require_key="verdict")
        assert result is not None
        assert result["verdict"] == "PASS"

    def test_multiple_objects_prefers_last(self):
        text = (
            'first: {"verdict": "FAIL", "extra": true} '
            'second: {"verdict": "PASS", "extra": false}'
        )
        result = extract_json(text, require_key="verdict")
        assert result["verdict"] == "PASS"

    def test_unbalanced_braces(self):
        text = '{"key": "value"'
        assert extract_json(text) is None

    def test_no_json_at_all(self):
        text = "There is no JSON here, just plain text about things."
        assert extract_json(text) is None


class TestExtractJsonPriority:
    def test_full_parse_wins_over_fenced(self):
        text = '{"direct": true}'
        assert extract_json(text) == {"direct": True}

    def test_fenced_wins_over_brace_match(self):
        text = 'stray {"brace": "match"} text\n' '```json\n{"fenced": true}\n```'
        result = extract_json(text, require_key="fenced")
        assert result == {"fenced": True}
