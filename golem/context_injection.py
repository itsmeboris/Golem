"""Workspace context injection for agent sessions.

Loads AGENTS.md and CLAUDE.md from the workspace and builds a system prompt
appendix.  Also provides a write-back mechanism for agents to persist
discoveries into AGENTS.md.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("golem.context_injection")

_CONTEXT_FILES = ["AGENTS.md", "CLAUDE.md"]
_MAX_CONTEXT_BYTES = 64 * 1024  # 64 KB

# Calibrated chars-per-token ratios for heuristic estimation
_CODE_RATIO = 3.2  # code blocks (shorter tokens, symbols)
_PROSE_RATIO = 4.5  # English prose (longer words)
_UNICODE_RATIO = 2.5  # non-ASCII / CJK (multi-byte, shorter token coverage)
_TRUNCATION_RATIO = 3.5  # conservative ratio for tokens→chars in truncation

# Optional tiktoken encoder — used when available for exact counts
_tiktoken_encoder = None
try:
    import tiktoken  # pylint: disable=import-outside-toplevel

    _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")  # pragma: no cover
except ImportError:
    pass


def _estimate_chars_per_token(text: str) -> float:
    """Return a content-aware chars-per-token ratio for *text*.

    Detects fenced code blocks (triple-backtick delimiters) and non-ASCII
    characters to compute a weighted blend of the calibrated ratio constants.

    Returns a value >= 1.0.
    """
    if not text:
        return _PROSE_RATIO

    total_chars = len(text)
    code_chars = 0
    in_code_block = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            code_chars += len(line) + 1  # +1 for newline
        elif in_code_block:
            code_chars += len(line) + 1

    # Count non-ASCII characters (outside code blocks for a rough heuristic)
    non_ascii_chars = sum(1 for ch in text if ord(ch) > 127)

    code_frac = code_chars / total_chars
    non_ascii_frac = non_ascii_chars / total_chars
    prose_frac = max(0.0, 1.0 - code_frac - non_ascii_frac)

    ratio = (
        code_frac * _CODE_RATIO
        + non_ascii_frac * _UNICODE_RATIO
        + prose_frac * _PROSE_RATIO
    )
    return max(1.0, ratio)


@dataclass
class ContextBudget:
    """Token-aware context sizing for prompt injection."""

    max_tokens: int = 8000

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count using tiktoken when available, otherwise heuristic."""
        if not text:
            return 0
        if _tiktoken_encoder is not None:
            return len(_tiktoken_encoder.encode(text))
        return self._heuristic_estimate(text)

    def _heuristic_estimate(self, text: str) -> int:
        """Content-aware heuristic token estimate when tiktoken is unavailable."""
        ratio = _estimate_chars_per_token(text)
        return max(1, int(len(text) / ratio))

    def fit_sections(
        self,
        sections: list[tuple[int, str, str]],
    ) -> str:
        """Select sections by priority to fit within max_tokens.

        Args:
            sections: (priority, label, content) tuples. Lower = more important.

        Returns:
            Combined content that fits within budget.
        """
        if not sections:
            return ""

        # Sort by priority (lower number = more important)
        sorted_sections = sorted(sections, key=lambda s: s[0])

        result_parts: list[str] = []
        used_tokens = 0

        for _priority, label, content in sorted_sections:
            section_text = f"## {label}\n\n{content.strip()}"
            section_tokens = self.estimate_tokens(section_text)

            if used_tokens + section_tokens <= self.max_tokens:
                # Fits entirely
                result_parts.append(section_text)
                used_tokens += section_tokens
            else:
                # Try to include a truncated version
                remaining_tokens = self.max_tokens - used_tokens
                if remaining_tokens > 100:
                    max_chars = int(remaining_tokens * _TRUNCATION_RATIO)
                    truncated = content.strip()[:max_chars]
                    # Find last newline to avoid mid-line truncation
                    last_nl = truncated.rfind("\n")
                    if last_nl > max_chars // 2:
                        truncated = truncated[:last_nl]
                    section_text = (
                        f"## {label} (truncated)\n\n{truncated}\n\n"
                        f"...(truncated to fit context budget)"
                    )
                    result_parts.append(section_text)
                    used_tokens += self.estimate_tokens(section_text)
                break  # No more room

        return "\n\n---\n\n".join(result_parts)


def load_workspace_context(work_dir: str) -> str:
    """Load AGENTS.md and CLAUDE.md from *work_dir*, return combined content.

    Each file's content is wrapped in a labeled section. Returns empty string
    if neither file exists.
    """
    work_path = Path(work_dir).resolve()
    sections = []

    for filename in _CONTEXT_FILES:
        content = _find_and_read(work_path, filename)
        if content:
            sections.append(f"## {filename}\n\n{content.strip()}")

    return "\n\n---\n\n".join(sections)


def _find_and_read(base: Path, filename: str) -> str:
    """Read *filename* from *base* directory. Returns content or empty string."""
    path = base / filename
    if not path.is_file():
        return ""
    try:
        size = path.stat().st_size
        if size > _MAX_CONTEXT_BYTES:
            logger.warning(
                "Skipping %s: too large (%d bytes, limit %d)",
                path,
                size,
                _MAX_CONTEXT_BYTES,
            )
            return ""
        content = path.read_text(encoding="utf-8")
        logger.debug("Loaded %s from %s (%d chars)", filename, base, len(content))
        return content
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return ""


def build_system_prompt(
    work_dir: str,
    max_tokens: int = 8000,
    subject: str = "",
    files: list[str] | None = None,
) -> str:
    """Build a system prompt appendix from workspace context files.

    Args:
        work_dir: Workspace directory to search for context files.
        max_tokens: Token budget for the combined context content.
        subject: Optional task subject used to query the knowledge graph.
        files: Optional list of files involved in the task; used by the
            knowledge graph to score relevance.

    Returns:
        The formatted prompt to pass via --append-system-prompt,
        or empty string if no context files are found.
    """
    budget = ContextBudget(max_tokens=max_tokens)
    work_path = Path(work_dir).resolve()
    sections: list[tuple[int, str, str]] = []

    # Priority 1: CLAUDE.md (project rules — highest priority)
    claude_content = _find_and_read(work_path, "CLAUDE.md")
    if claude_content:
        sections.append((1, "CLAUDE.md", claude_content))

    # Priority 2: AGENTS.md (learned patterns)
    agents_content = _find_and_read(work_path, "AGENTS.md")
    if agents_content:
        sections.append((2, "AGENTS.md", agents_content))

    # Priority 3: Role contexts
    role_section = build_role_context_section()
    if role_section:
        sections.append((3, "Role Contexts", role_section))

    # Priority 4: Relevant knowledge from graph (if instinct store exists)
    if subject:
        kg_section = _query_knowledge_graph(work_dir, subject, files)
        if kg_section:
            sections.append((4, "Relevant Knowledge", kg_section))

    if not sections:
        return ""

    context = budget.fit_sections(sections)

    return (
        "# Workspace Context\n\n"
        "The following workspace conventions and agent guidelines were loaded "
        "from the project. Follow these guidelines during your work.\n\n"
        f"{context}\n\n"
        "---\n\n"
        "# Discovery Write-Back\n\n"
        "If you discover important patterns, conventions, gotchas, or "
        "architectural insights during this task that would help future "
        "agent sessions, append them to AGENTS.md in the workspace root. "
        "Use a clear heading and bullet points. Only add genuinely useful "
        "discoveries — do not repeat what is already documented."
    )


def _query_knowledge_graph(
    work_dir: str,
    subject: str,
    files: list[str] | None,
    max_results: int = 5,
) -> str:
    """Query the knowledge graph for task-relevant pitfalls.

    Returns a markdown-formatted section of relevant pitfalls, or an
    empty string if the instinct store does not exist, has no matching
    knowledge, or an error occurs.

    Args:
        work_dir: Workspace directory; the store is at ``<work_dir>/.golem/instincts.json``.
        subject: Task subject string to match against indexed keywords.
        files: Optional list of file paths for file-reference scoring.
        max_results: Maximum number of pitfall results to include.
    """
    store_path = Path(work_dir).resolve() / ".golem" / "instincts.json"
    if not store_path.exists():
        return ""
    try:
        from .instinct_store import (
            InstinctStore,
        )  # pylint: disable=import-outside-toplevel
        from .knowledge_graph import (
            KnowledgeGraph,
        )  # pylint: disable=import-outside-toplevel

        store = InstinctStore(store_path)
        graph = KnowledgeGraph(store)
        return graph.query_for_context(subject, files, max_results=max_results)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("Knowledge graph query failed: %s", exc)
        return ""


# Role-specific context for subagent dispatch
_ROLE_CONTEXT_DIR = Path(__file__).parent / "prompts" / "contexts"
_VALID_ROLES = frozenset({"builder", "reviewer", "verifier", "explorer"})


def load_role_context(role: str) -> str:
    """Load the context file for a sub-agent *role*.

    Returns the file content or empty string if the role is unknown
    or the file does not exist.
    """
    if role not in _VALID_ROLES:
        logger.warning("Unknown role %r, skipping context load", role)
        return ""
    context_file = _ROLE_CONTEXT_DIR / f"{role}.md"
    if not context_file.is_file():
        logger.warning("Role context file not found: %s", context_file)
        return ""
    try:
        content = context_file.read_text(encoding="utf-8")
        logger.debug("Loaded role context for %s (%d chars)", role, len(content))
        return content.strip()
    except OSError as exc:
        logger.warning("Could not read role context %s: %s", context_file, exc)
        return ""


def load_all_role_contexts() -> dict[str, str]:
    """Load context for all known roles, returning a dict of role → content.

    Roles whose context file is missing are omitted from the result.
    """
    contexts = {}
    for role in sorted(_VALID_ROLES):
        content = load_role_context(role)
        if content:
            contexts[role] = content
    return contexts


def build_role_context_section() -> str:
    """Format all role contexts into a section for the orchestration prompt.

    Returns a formatted string block that can be embedded in the orchestration
    template, or empty string if no context files are found.
    """
    contexts = load_all_role_contexts()
    if not contexts:
        return ""
    parts = []
    parts.append("## Role-Specific Contexts\n")
    parts.append(
        "When dispatching a subagent, prepend the matching context block below "
        "to the subagent's prompt. This sets behavioral priorities for each role.\n"
    )
    for role, content in contexts.items():
        parts.append(f"### {role.title()} Context\n")
        parts.append(f"```\n{content}\n```\n")
    return "\n".join(parts)


def write_back_discoveries(work_dir: str, discoveries: list[str]) -> bool:
    """Append *discoveries* to AGENTS.md in *work_dir*.

    Creates AGENTS.md if it does not exist. Each discovery is added as a
    bullet point under a dated section header.

    Returns True if the file was written successfully, False otherwise.
    """
    if not discoveries:
        return False

    cleaned = [d.strip() for d in discoveries if d.strip()]
    if not cleaned:
        return False

    agents_path = Path(work_dir).resolve() / "AGENTS.md"

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    section = f"\n\n## Discoveries ({timestamp})\n\n"
    section += "\n".join(f"- {d}" for d in cleaned)
    section += "\n"

    header = "# Agent Guidelines\n\nWorkspace conventions and discovered patterns.\n"

    try:
        if agents_path.is_file():
            with open(agents_path, "a", encoding="utf-8") as fh:
                fh.write(section)
        else:
            agents_path.write_text(header + section, encoding="utf-8")
        logger.info("Wrote %d discoveries to %s", len(cleaned), agents_path)
        return True
    except OSError as exc:
        logger.warning("Could not write discoveries to %s: %s", agents_path, exc)
        return False
