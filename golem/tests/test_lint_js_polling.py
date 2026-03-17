"""Tests for the JS polling pattern scanner."""

import pytest

from golem.lint.js_polling import scan_polling_patterns


class TestScanPollingPatterns:
    def test_empty_directory(self, tmp_path):
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_no_js_files(self, tmp_path):
        (tmp_path / "readme.txt").write_text("nothing here")
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_js_file_with_no_timer_calls(self, tmp_path):
        (tmp_path / "util.js").write_text("function greet() { return 'hello'; }\n")
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_setinterval_fetch_without_guard_flagged(self, tmp_path):
        (tmp_path / "poll.js").write_text(
            "setInterval(async () => {\n"
            "  const r = await fetch('/api/data');\n"
            "}, 1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["file"] == "poll.js"
        assert result[0]["line"] == 1
        assert result[0]["pattern"] == "setInterval"
        assert "concurrency guard" in result[0]["message"]

    def test_settimeout_fetch_without_guard_flagged(self, tmp_path):
        (tmp_path / "retry.js").write_text(
            "setTimeout(async () => {\n"
            "  const r = await fetch('/api/status');\n"
            "}, 2000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["file"] == "retry.js"
        assert result[0]["line"] == 1
        assert result[0]["pattern"] == "setTimeout"

    @pytest.mark.parametrize(
        "guard",
        [
            "isFetching",
            "_pollInFlight",
            "loading",
            "pending",
            "AbortController",
        ],
    )
    def test_setinterval_fetch_with_guard_not_flagged(self, tmp_path, guard):
        (tmp_path / "poll.js").write_text(
            f"setInterval(async () => {{\n"
            f"  if ({guard}) return;\n"
            f"  {guard} = true;\n"
            f"  const r = await fetch('/api/data');\n"
            f"  {guard} = false;\n"
            f"}}, 1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_setinterval_without_fetch_not_flagged(self, tmp_path):
        (tmp_path / "tick.js").write_text(
            "setInterval(() => {\n" "  updateCounter();\n" "}, 500);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_min_js_skipped(self, tmp_path):
        (tmp_path / "elk.min.js").write_text(
            "setInterval(function(){fetch('/api');},1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_non_min_js_not_skipped(self, tmp_path):
        (tmp_path / "app.js").write_text(
            "setInterval(async () => {\n" "  await fetch('/api');\n" "}, 1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1

    def test_multiple_files_only_problematic_reported(self, tmp_path):
        (tmp_path / "safe.js").write_text(
            "setInterval(async () => {\n"
            "  if (isFetching) return;\n"
            "  isFetching = true;\n"
            "  await fetch('/ok');\n"
            "  isFetching = false;\n"
            "}, 1000);\n"
        )
        (tmp_path / "unsafe.js").write_text(
            "setInterval(async () => {\n" "  await fetch('/bad');\n" "}, 500);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["file"] == "unsafe.js"

    def test_function_reference_with_fetch_flagged(self, tmp_path):
        """setTimeout(fetchConfig, 2000) where fetchConfig calls fetch() is flagged."""
        (tmp_path / "config.js").write_text(
            "function fetchConfig() {\n"
            "  return fetch('/config').then(r => r.json());\n"
            "}\n"
            "setTimeout(fetchConfig, 2000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["line"] == 4
        assert result[0]["pattern"] == "setTimeout"

    def test_function_reference_without_fetch_not_flagged(self, tmp_path):
        """setTimeout(cleanup, 4000) where cleanup doesn't call fetch() is not flagged."""
        (tmp_path / "ui.js").write_text(
            "function cleanup() {\n"
            "  el.remove();\n"
            "}\n"
            "setTimeout(cleanup, 4000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_settimeout_arrow_remove_not_flagged(self, tmp_path):
        """setTimeout(() => el.remove(), 4000) — no fetch, should not be flagged."""
        (tmp_path / "ui.js").write_text("setTimeout(() => el.remove(), 4000);\n")
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_multiple_patterns_in_file_mixed(self, tmp_path):
        """File with both guarded and unguarded patterns — only unguarded flagged."""
        (tmp_path / "mixed.js").write_text(
            "setInterval(async () => {\n"
            "  if (isFetching) return;\n"
            "  isFetching = true;\n"
            "  await fetch('/safe');\n"
            "  isFetching = false;\n"
            "}, 1000);\n"
            "\n"
            "setInterval(async () => {\n"
            "  await fetch('/unsafe');\n"
            "}, 500);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["line"] == 8
        assert result[0]["pattern"] == "setInterval"

    def test_empty_js_file(self, tmp_path):
        (tmp_path / "empty.js").write_text("")
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_whitespace_only_js_file(self, tmp_path):
        (tmp_path / "blank.js").write_text("   \n\n  \n")
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_xmlhttprequest_detected_as_async_io(self, tmp_path):
        """XMLHttpRequest usage inside setInterval counts as async I/O."""
        (tmp_path / "xhr.js").write_text(
            "setInterval(function() {\n"
            "  var xhr = new XMLHttpRequest();\n"
            "  xhr.open('GET', '/data');\n"
            "  xhr.send();\n"
            "}, 1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["pattern"] == "setInterval"

    def test_send_method_detected_as_async_io(self, tmp_path):
        """.send( usage inside setInterval counts as async I/O."""
        (tmp_path / "ws.js").write_text(
            "setInterval(function() {\n" "  socket.send('ping');\n" "}, 5000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1

    def test_nested_directory_js_file_found(self, tmp_path):
        subdir = tmp_path / "src" / "components"
        subdir.mkdir(parents=True)
        (subdir / "widget.js").write_text(
            "setInterval(async () => {\n" "  await fetch('/widget');\n" "}, 1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["file"] == "src/components/widget.js"

    def test_result_dict_keys(self, tmp_path):
        """Each result dict must have exactly: file, line, pattern, message."""
        (tmp_path / "poll.js").write_text(
            "setInterval(async () => {\n" "  await fetch('/api');\n" "}, 1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        entry = result[0]
        assert set(entry.keys()) == {"file", "line", "pattern", "message"}
        assert isinstance(entry["file"], str)
        assert isinstance(entry["line"], int)
        assert entry["pattern"] in {"setInterval", "setTimeout"}
        assert isinstance(entry["message"], str)

    def test_non_utf8_file_skipped_gracefully(self, tmp_path):
        """Files that cannot be decoded as UTF-8 are skipped without crashing."""
        bad_file = tmp_path / "binary.js"
        bad_file.write_bytes(b"\xff\xfe invalid utf-8 \x80\x81")
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_task_live_js_guarded_not_flagged(self, tmp_path):
        """Reproduction of task_live.js _startFallbackPolling — has _pollInFlight guard."""
        (tmp_path / "task_live.js").write_text(
            "function _startFallbackPolling() {\n"
            "  if (S.pollTimer) return;\n"
            "  S.pollTimer = setInterval(async () => {\n"
            "    if (_pollInFlight) return;\n"
            "    _pollInFlight = true;\n"
            "    try {\n"
            "      const data = await fetchSessions();\n"
            "    } finally {\n"
            "      _pollInFlight = false;\n"
            "    }\n"
            "  }, 2000);\n"
            "}\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_config_tab_js_function_ref_flagged(self, tmp_path):
        """Reproduction of config_tab.js setTimeout(fetchConfig, 2000) — fetch inside fetchConfig."""
        (tmp_path / "config_tab.js").write_text(
            "function fetchConfig() {\n"
            "  return fetch('/api/config').then(r => r.json());\n"
            "}\n"
            "function saveConfig() {\n"
            "  setTimeout(fetchConfig, 2000);\n"
            "}\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["line"] == 5
        assert result[0]["pattern"] == "setTimeout"

    def test_heartbeat_widget_countdown_not_flagged(self, tmp_path):
        """Reproduction of heartbeat_widget.js _hbCountdownTick — no fetch in callback."""
        (tmp_path / "heartbeat.js").write_text(
            "function _hbCountdownTick() {\n"
            "  counter -= 1;\n"
            "  updateDisplay(counter);\n"
            "}\n"
            "setInterval(_hbCountdownTick, 1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_inline_fetch_in_callback(self, tmp_path):
        """fetch() directly inside the callback body is detected."""
        (tmp_path / "inline.js").write_text(
            "setInterval(function() {\n"
            "  fetch('/status').then(r => r.json()).then(updateUI);\n"
            "}, 3000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1

    def test_inflight_guard_variant(self, tmp_path):
        """InFlight as part of a variable name is a valid guard."""
        (tmp_path / "poll.js").write_text(
            "setInterval(async () => {\n"
            "  if (requestInFlight) return;\n"
            "  requestInFlight = true;\n"
            "  await fetch('/data');\n"
            "  requestInFlight = false;\n"
            "}, 1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_closing_brace_in_string_does_not_terminate_scope(self, tmp_path):
        """A closing brace inside a string literal must not prematurely end scope extraction."""
        (tmp_path / "poll.js").write_text(
            "setInterval(async () => {\n"
            '  let x = "}";\n'
            "  const r = await fetch('/api/data');\n"
            "}, 1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["pattern"] == "setInterval"

    def test_js_keyword_not_captured_as_function_ref(self, tmp_path):
        """JS keywords like 'function' or 'async' must not be captured as function-ref names."""
        (tmp_path / "poll.js").write_text(
            "setInterval(async function() {\n"
            "  await fetch('/api/data');\n"
            "}, 1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        # Should be flagged (inline callback), not silently dropped due to keyword capture
        assert len(result) == 1
        assert result[0]["pattern"] == "setInterval"

    def test_setinterval_last_line_no_crash(self, tmp_path):
        """setInterval on last line of file (no closing brace) — handles gracefully."""
        (tmp_path / "trunc.js").write_text("setInterval(async () => {")
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_downloading_not_treated_as_loading_guard(self, tmp_path):
        """'downloading' must not be treated as a concurrency guard (substring of 'loading' is not matched)."""
        (tmp_path / "poll.js").write_text(
            "setInterval(async () => {\n"
            "  if (downloading) return;\n"
            "  const r = await fetch('/api/data');\n"
            "}, 1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["pattern"] == "setInterval"

    def test_appending_not_treated_as_pending_guard(self, tmp_path):
        """'appending' must not be treated as a concurrency guard (substring of 'pending' is not matched)."""
        (tmp_path / "poll.js").write_text(
            "setInterval(async () => {\n"
            "  if (appending) return;\n"
            "  const r = await fetch('/api/data');\n"
            "}, 1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["pattern"] == "setInterval"

    def test_block_comment_with_braces_not_counted(self, tmp_path):
        """Braces inside /* ... */ block comments must not affect depth tracking."""
        # Lines 75-81 and 95-97: block comment open/close handling
        (tmp_path / "poll.js").write_text(
            "setInterval(async () => {\n"
            "  /* closing brace } inside block comment */\n"
            "  const r = await fetch('/api/data');\n"
            "}, 1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["pattern"] == "setInterval"

    def test_escaped_quote_in_string_not_counted_as_brace(self, tmp_path):
        """An escaped quote inside a string must not terminate the string prematurely."""
        # Lines 86-87: escaped character skip in string parsing
        (tmp_path / "poll.js").write_text(
            "setInterval(async () => {\n"
            '  let x = "test\\"}";\n'
            "  const r = await fetch('/api/data');\n"
            "}, 1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["pattern"] == "setInterval"

    def test_line_comment_brace_not_counted(self, tmp_path):
        """A closing brace on a // line comment must not terminate scope extraction."""
        # Line 102: line comment break
        (tmp_path / "poll.js").write_text(
            "setInterval(async () => {\n"
            "  // this line has a brace } that should be ignored\n"
            "  const r = await fetch('/api/data');\n"
            "}, 1000);\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["pattern"] == "setInterval"

    def test_function_reference_not_in_file_returns_no_result(self, tmp_path):
        """setTimeout(nonExistent, 1000) where nonExistent is not defined — no result."""
        # Line 138: _find_function_body returns "" when function not found
        (tmp_path / "poll.js").write_text("setTimeout(nonExistent, 1000);\n")
        result = scan_polling_patterns(tmp_path)
        assert result == []

    def test_js_keyword_as_func_ref_uses_inline_scope(self, tmp_path):
        """setTimeout(function, 1000) — 'function' is a JS keyword; inline scope used."""
        # Line 187: keyword captured by _FUNC_REF_PATTERN falls through to inline scope
        # The line has a `{` so inline scope is extracted and fetch is detected.
        (tmp_path / "poll.js").write_text(
            "setTimeout(function, 1000, async () => {\n"
            "  await fetch('/api/data');\n"
            "});\n"
        )
        result = scan_polling_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["pattern"] == "setTimeout"
