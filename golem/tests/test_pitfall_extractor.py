"""Tests for golem.pitfall_extractor — full coverage."""

import pytest

from golem.pitfall_extractor import (
    CATEGORY_ANTIPATTERNS,
    CATEGORY_ARCHITECTURE,
    CATEGORY_COVERAGE,
    _is_duplicate,
    _is_noise,
    _token_overlap,
    classify_pitfall,
    extract_pitfalls,
    normalize_pitfall,
)

# -- normalize_pitfall -------------------------------------------------------


def test_normalize_pitfall_basic():
    assert normalize_pitfall("  Hello World  ") == "hello world"


def test_normalize_pitfall_truncate():
    long_text = "a" * 250
    result = normalize_pitfall(long_text)
    assert len(result) == 200


def test_normalize_pitfall_empty():
    assert normalize_pitfall("") == ""


# -- _token_overlap ----------------------------------------------------------


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


# -- _is_duplicate -----------------------------------------------------------


def test_is_duplicate_returns_index_on_match():
    existing = ["the test failed due to missing dependency"]
    result = _is_duplicate("test failed due to missing dependency", existing)
    assert result is not None
    assert result == 0  # index of matched entry


def test_is_duplicate_returns_index_not_first():
    existing = ["unrelated entry", "the test failed due to missing dependency"]
    result = _is_duplicate("test failed due to missing dependency", existing)
    assert result == 1


def test_is_duplicate_returns_none_on_no_match():
    existing = ["completely unrelated sentence"]
    result = _is_duplicate("totally different content here", existing)
    assert result is None


def test_is_duplicate_returns_none_for_empty():
    assert _is_duplicate("any candidate", []) is None


# -- _is_noise ---------------------------------------------------------------


def test_is_noise_short_text():
    assert _is_noise("ok")
    assert _is_noise("always run tests")


def test_is_noise_positive_outcomes():
    assert _is_noise("all spec requirements implemented correctly")
    assert _is_noise("code is clean and well-structured")
    assert _is_noise("no regressions to existing endpoints")
    assert _is_noise("follows project conventions nicely")


def test_is_noise_actionable_content():
    assert not _is_noise("antipattern: dead code after return in dashboard.py")
    assert not _is_noise("no independent verification was run")
    assert not _is_noise("cross-module private access in pitfall_writer.py")


# -- classify_pitfall --------------------------------------------------------


def test_classify_antipattern():
    assert classify_pitfall("antipattern: dead code") == CATEGORY_ANTIPATTERNS
    assert classify_pitfall("empty exception handler") == CATEGORY_ANTIPATTERNS
    assert classify_pitfall("silently swallows errors") == CATEGORY_ANTIPATTERNS
    assert (
        classify_pitfall("tightly coupling to implementation") == CATEGORY_ANTIPATTERNS
    )
    assert classify_pitfall("unused import detected") == CATEGORY_ANTIPATTERNS


def test_classify_coverage():
    assert classify_pitfall("no independent verification was run") == CATEGORY_COVERAGE
    assert classify_pitfall("missing test coverage for module") == CATEGORY_COVERAGE
    assert classify_pitfall("no end-to-end test for path") == CATEGORY_COVERAGE
    assert classify_pitfall("test pass claims unverified") == CATEGORY_COVERAGE


def test_classify_architecture_default():
    assert classify_pitfall("cross-module private access") == CATEGORY_ARCHITECTURE
    assert classify_pitfall("file locking is ineffective") == CATEGORY_ARCHITECTURE
    assert classify_pitfall("untyped dict access in config") == CATEGORY_ARCHITECTURE


# -- extract_pitfalls --------------------------------------------------------


def test_extract_from_concerns():
    sessions = [
        {
            "validation_concerns": ["Missing test coverage for module X"],
            "validation_test_failures": [],
            "errors": [],
            "retry_count": 0,
            "validation_summary": "",
        }
    ]
    result = extract_pitfalls(sessions)
    assert "missing test coverage for module x" in result


def test_extract_from_test_failures():
    sessions = [
        {
            "validation_concerns": [],
            "validation_test_failures": ["test_foo FAILED: assertion error in module"],
            "errors": [],
            "retry_count": 0,
            "validation_summary": "",
        }
    ]
    result = extract_pitfalls(sessions)
    assert "test_foo failed: assertion error in module" in result


def test_extract_from_errors():
    sessions = [
        {
            "validation_concerns": [],
            "validation_test_failures": [],
            "errors": ["ImportError: cannot import name X from module Y"],
            "retry_count": 0,
            "validation_summary": "",
        }
    ]
    result = extract_pitfalls(sessions)
    assert "importerror: cannot import name x from module y" in result


def test_extract_from_retries():
    sessions = [
        {
            "validation_concerns": [],
            "validation_test_failures": [],
            "errors": [],
            "retry_count": 2,
            "validation_summary": "Tests failed after retry due to missing mock",
        }
    ]
    result = extract_pitfalls(sessions)
    assert "tests failed after retry due to missing mock" in result


def test_extract_from_summary():
    sessions = [
        {
            "validation_concerns": [],
            "validation_test_failures": [],
            "errors": [],
            "retry_count": 0,
            "validation_summary": "First issue found in module. Second issue found in tests",
        }
    ]
    result = extract_pitfalls(sessions)
    assert "first issue found in module" in result
    assert "second issue found in tests" in result


def test_extract_deduplication():
    sessions = [
        {
            "validation_concerns": ["tests failed due to missing mock in module"],
            "validation_test_failures": [
                "test failed due to missing mock setup in module"
            ],
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
            "validation_concerns": ["", "   ", "real concern worth keeping here"],
            "validation_test_failures": [],
            "errors": [],
            "retry_count": 0,
            "validation_summary": "",
        }
    ]
    result = extract_pitfalls(sessions)
    assert "" not in result
    assert "real concern worth keeping here" in result


def test_extract_retry_with_multi_sentence_summary():
    """Sentences from validation_summary are not suppressed by retry full-summary entry."""
    sessions = [
        {
            "validation_concerns": [],
            "validation_test_failures": [],
            "errors": [],
            "retry_count": 2,
            "validation_summary": "Missing imports in the module. Broken test assertion in suite",
        }
    ]
    result = extract_pitfalls(sessions)
    assert "missing imports in the module" in result
    assert "broken test assertion in suite" in result


def test_extract_cap_200_chars():
    long_concern = "x" * 250
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
    assert all(len(p) <= 200 for p in result)


def test_extract_filters_noise():
    """Positive outcomes and short noise are filtered out."""
    sessions = [
        {
            "validation_concerns": [
                "all spec requirements implemented correctly",
                "antipattern: dead code after return in module",
            ],
            "validation_test_failures": [],
            "errors": [],
            "retry_count": 0,
            "validation_summary": "code is clean and follows project conventions",
        }
    ]
    result = extract_pitfalls(sessions)
    assert len(result) == 1
    assert "antipattern" in result[0]
