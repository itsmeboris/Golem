"""Tests for golem.pitfall_extractor — full coverage."""

import pytest

from golem.pitfall_extractor import (
    _is_duplicate,
    _token_overlap,
    extract_pitfalls,
    normalize_pitfall,
)


def test_normalize_pitfall_basic():
    assert normalize_pitfall("  Hello World  ") == "hello world"


def test_normalize_pitfall_truncate():
    long_text = "a" * 150
    result = normalize_pitfall(long_text)
    assert len(result) == 120


def test_normalize_pitfall_empty():
    assert normalize_pitfall("") == ""


def test_token_overlap_identical():
    assert _token_overlap("foo bar baz", "foo bar baz") == 1.0


def test_token_overlap_disjoint():
    assert _token_overlap("foo bar", "baz qux") == 0.0


def test_token_overlap_partial():
    # intersection = {foo}, union = {foo, bar, baz}
    result = _token_overlap("foo bar", "foo baz")
    assert abs(result - 1 / 3) < 1e-9


def test_token_overlap_empty():
    assert _token_overlap("", "") == 0.0


def test_is_duplicate_true():
    existing = ["the test failed due to missing dependency"]
    assert _is_duplicate("test failed due to missing dependency", existing)


def test_is_duplicate_false():
    existing = ["completely unrelated sentence"]
    assert not _is_duplicate("totally different content here", existing)


def test_is_duplicate_empty_list():
    assert not _is_duplicate("any candidate", [])


def test_extract_from_concerns():
    sessions = [
        {
            "validation_concerns": ["Missing test coverage"],
            "validation_test_failures": [],
            "errors": [],
            "retry_count": 0,
            "validation_summary": "",
        }
    ]
    result = extract_pitfalls(sessions)
    assert "missing test coverage" in result


def test_extract_from_test_failures():
    sessions = [
        {
            "validation_concerns": [],
            "validation_test_failures": ["test_foo FAILED: assertion error"],
            "errors": [],
            "retry_count": 0,
            "validation_summary": "",
        }
    ]
    result = extract_pitfalls(sessions)
    assert "test_foo failed: assertion error" in result


def test_extract_from_errors():
    sessions = [
        {
            "validation_concerns": [],
            "validation_test_failures": [],
            "errors": ["ImportError: cannot import name X"],
            "retry_count": 0,
            "validation_summary": "",
        }
    ]
    result = extract_pitfalls(sessions)
    assert "importerror: cannot import name x" in result


def test_extract_from_retries():
    sessions = [
        {
            "validation_concerns": [],
            "validation_test_failures": [],
            "errors": [],
            "retry_count": 2,
            "validation_summary": "Tests failed after retry",
        }
    ]
    result = extract_pitfalls(sessions)
    assert "tests failed after retry" in result


def test_extract_from_summary():
    sessions = [
        {
            "validation_concerns": [],
            "validation_test_failures": [],
            "errors": [],
            "retry_count": 0,
            "validation_summary": "First issue found. Second issue found",
        }
    ]
    result = extract_pitfalls(sessions)
    assert "first issue found" in result
    assert "second issue found" in result


def test_extract_deduplication():
    sessions = [
        {
            "validation_concerns": ["tests failed due to missing mock"],
            "validation_test_failures": ["test failed due to missing mock setup"],
            "errors": [],
            "retry_count": 0,
            "validation_summary": "",
        }
    ]
    result = extract_pitfalls(sessions)
    # Both are similar enough to deduplicate
    assert len(result) == 1


def test_extract_empty_sessions():
    result = extract_pitfalls([])
    assert result == []


def test_extract_skips_empty_strings():
    sessions = [
        {
            "validation_concerns": ["", "   ", "real concern"],
            "validation_test_failures": [],
            "errors": [],
            "retry_count": 0,
            "validation_summary": "",
        }
    ]
    result = extract_pitfalls(sessions)
    assert "" not in result
    assert "real concern" in result


def test_extract_retry_with_multi_sentence_summary():
    """Sentences from validation_summary are not suppressed by retry_count full-summary entry."""
    sessions = [
        {
            "validation_concerns": [],
            "validation_test_failures": [],
            "errors": [],
            "retry_count": 2,
            "validation_summary": "Missing imports. Broken test assertion",
        }
    ]
    result = extract_pitfalls(sessions)
    assert "missing imports" in result
    assert "broken test assertion" in result


def test_extract_cap_120_chars():
    long_concern = "x" * 200
    sessions = [
        {
            "validation_concerns": [long_concern],
            "validation_test_failures": [],
            "errors": [],
            "retry_count": 0,
            "validation_summary": "",
        }
    ]
    result = extract_pitfalls(sessions)
    assert all(len(p) <= 120 for p in result)
