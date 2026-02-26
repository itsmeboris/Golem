"""Pretty-printer for Claude/Agent CLI stream-json events."""

import json
import logging

logger = logging.getLogger("golem.core.stream_printer")


class StreamPrinter:
    """Buffers text fragments and prints clean, readable verbose output.

    Handles multiple event formats from both ``agent`` and ``claude`` CLIs,
    deduplicates repeated text, and surfaces tool calls prominently.
    """

    TOOL = "\033[36m⚙\033[0m"
    TOOL_OK = "\033[32m↩\033[0m"
    ERR = "\033[31m✗\033[0m"
    TXT = "\033[33m…\033[0m"
    DONE = "\033[32m━\033[0m"

    def __init__(self, out):
        self.out = out
        self._text_buf: list[str] = []
        self._seen: set[str] = set()
        self._tool_names_seen: set[str] = set()

    def handle(self, event: dict) -> None:
        """Process a single stream-json event and print readable output."""
        etype = event.get("type", "")

        if etype == "assistant":
            self._on_assistant(event)
        elif etype == "result":
            self._flush_text()
            cost = event.get("cost_usd", 0)
            dur = event.get("duration_ms", 0) / 1000
            self._line(f"{self.DONE} done  ${cost:.2f}  {dur:.0f}s")
        elif etype == "tool_result":
            self._on_tool_result(event)
        elif etype == "tool_call":
            self._on_tool_call(event)

        self._log_tool_events(event)

    def _on_assistant(self, event: dict) -> None:
        blocks = self._find_content_blocks(event)
        for block in blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "tool_use":
                self._flush_text()
                name = block.get("name", "?")
                self._emit_tool(name)
            elif btype == "tool_result":
                self._on_tool_result(block)
            elif btype == "text":
                text = block.get("text", "")
                if text:
                    self._text_buf.append(text)
                    if self._ends_sentence(text):
                        self._flush_text()

        self._scan_for_tools(event)

    @staticmethod
    def _find_content_blocks(event: dict) -> list:
        for path in [
            lambda e: e.get("message", {}).get("content", []),
            lambda e: e.get("content", []),
            lambda e: e.get("content_block", []),
        ]:
            blocks = path(event)
            if isinstance(blocks, list) and blocks:
                return blocks
            if isinstance(blocks, dict):
                return [blocks]
        return []

    def _scan_for_tools(self, event: dict) -> None:
        raw = json.dumps(event, default=str)
        if '"tool_use"' not in raw and '"tool_call"' not in raw:
            return
        for key in ("name", "tool_name", "function"):
            self._find_tool_name(event, key)

    def _find_tool_name(self, obj, key: str) -> None:
        if isinstance(obj, dict):
            if key in obj and isinstance(obj[key], str):
                self._emit_tool(obj[key])
            for val in obj.values():
                self._find_tool_name(val, key)
        elif isinstance(obj, list):
            for item in obj:
                self._find_tool_name(item, key)

    def _emit_tool(self, name: str) -> None:
        if name in self._tool_names_seen:
            return
        self._tool_names_seen.add(name)
        self._line(f"  {self.TOOL}  {name}")

    def _on_tool_call(self, event: dict) -> None:
        subtype = event.get("subtype", "")
        call = event.get("tool_call", {})
        mcp = call.get("mcpToolCall", {})

        if subtype == "started":
            tool_name = mcp.get("args", {}).get("toolName", "")
            if tool_name:
                self._flush_text()
                self._emit_tool(tool_name)
        elif subtype == "completed":
            result = mcp.get("result", {})
            rejected = result.get("rejected", {})
            if rejected:
                reason = rejected.get("reason", "unknown")
                self._line(f"  {self.ERR}  MCP rejected: {reason}")

    def _on_tool_result(self, block: dict) -> None:
        is_error = block.get("is_error", False)
        content = block.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        text = str(content).replace("\n", " ").strip()
        if is_error:
            self._line(f"  {self.ERR}  {text or '(empty error)'}")
        elif text:
            snippet = text[:120] + "…" if len(text) > 120 else text
            self._line(f"  {self.TOOL_OK}  {snippet}")

    def _log_tool_events(self, event: dict) -> None:
        raw = json.dumps(event, default=str)
        if '"tool_use"' not in raw and '"tool_result"' not in raw:
            return
        for keyword in ("error", "reject", "denied", "refused", "unavailable"):
            if keyword in raw.lower():
                snippet = raw[:300].replace("\n", " ")
                logger.debug("Tool event with '%s': %s", keyword, snippet)
                break

    def _flush_text(self) -> None:
        if not self._text_buf:
            return
        full = "".join(self._text_buf).strip()
        self._text_buf.clear()
        if not full:
            return
        clean = " ".join(full.split())
        if self._is_duplicate(clean):
            return
        if self._looks_like_json(clean):
            return
        self._seen.add(clean[:100])
        self._line(f"  {self.TXT}  {clean}")

    def _is_duplicate(self, text: str) -> bool:
        prefix = text[:100]
        if prefix in self._seen:
            return True
        for prev in self._seen:
            if prefix.startswith(prev[:60]) or prev.startswith(prefix[:60]):
                return True
        return False

    @staticmethod
    def _looks_like_json(text: str) -> bool:
        stripped = text.lstrip()
        if stripped.startswith(("```json", '{"action"', '{ "action"')):
            return True
        if '"action":' in text and '"code_review_label":' in text:
            return True
        if '"root_cause":' in text and '"category":' in text:
            return True
        return False

    def _line(self, text: str) -> None:
        self.out.write(text + "\n")
        self.out.flush()

    @staticmethod
    def _ends_sentence(text: str) -> bool:
        for ch in reversed(text):
            if ch in ".!?\n":
                return True
            if not ch.isspace():
                return False
        return False
