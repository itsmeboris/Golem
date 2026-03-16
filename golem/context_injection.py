"""Workspace context injection for agent sessions.

Loads AGENTS.md and CLAUDE.md from the workspace and builds a system prompt
appendix.  Also provides a write-back mechanism for agents to persist
discoveries into AGENTS.md.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("golem.context_injection")

_CONTEXT_FILES = ["AGENTS.md", "CLAUDE.md"]
_MAX_CONTEXT_BYTES = 64 * 1024  # 64 KB


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


def build_system_prompt(work_dir: str) -> str:
    """Build a system prompt appendix from workspace context files.

    Returns the formatted prompt to pass via --append-system-prompt,
    or empty string if no context files are found.
    """
    context = load_workspace_context(work_dir)
    if not context:
        return ""

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
