# golem/handoff.py
"""Phase handoff helpers for passing structured context between orchestration phases."""

from datetime import datetime, timezone

from .types import FileRoleDict, PhaseHandoffDict


def create_handoff(
    from_phase: str,
    to_phase: str,
    context: list[str],
    files: list[FileRoleDict],
    open_questions: list[str],
    warnings: list[str],
) -> PhaseHandoffDict:
    """Build a PhaseHandoffDict with an auto-populated UTC timestamp.

    Args:
        from_phase: Name of the phase completing the handoff.
        to_phase: Name of the phase receiving the handoff.
        context: Key facts and decisions to carry forward.
        files: Files identified as relevant, each with path, role, and relevance.
        open_questions: Unresolved questions for the receiving phase.
        warnings: Caveats or risks the receiving phase should know about.

    Returns:
        A fully-populated PhaseHandoffDict.
    """
    return PhaseHandoffDict(
        from_phase=from_phase,
        to_phase=to_phase,
        context=context,
        files=files,
        open_questions=open_questions,
        warnings=warnings,
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
    )


def validate_handoff(handoff: PhaseHandoffDict) -> tuple[bool, list[str]]:
    """Check that a PhaseHandoffDict has all required non-empty fields.

    Required fields that must be non-empty:
        - from_phase (non-empty string)
        - to_phase (non-empty string)
        - context (non-empty list)

    Args:
        handoff: The handoff dict to validate.

    Returns:
        A (valid, reasons) tuple where valid is True iff no issues found,
        and reasons is a list of human-readable problem descriptions.
    """
    reasons: list[str] = []

    from_phase = handoff.get("from_phase")
    if not from_phase:
        reasons.append("from_phase is missing or empty")

    to_phase = handoff.get("to_phase")
    if not to_phase:
        reasons.append("to_phase is missing or empty")

    context = handoff.get("context")
    if not context:
        reasons.append("context is missing or empty")

    return (len(reasons) == 0, reasons)


def format_handoff_markdown(handoff: PhaseHandoffDict) -> str:
    """Render a PhaseHandoffDict as a markdown-formatted string.

    Output structure:
        ## Handoff: [from_phase] → [to_phase]

        ### Context carried forward
        - item1

        ### Files identified
        - path (role): relevance

        ### Open questions
        - question1

        ### Warnings
        - warning1

    Args:
        handoff: The handoff dict to render.

    Returns:
        A markdown string representation of the handoff.
    """
    lines: list[str] = []

    lines.append(
        "## Handoff: %s \u2192 %s" % (handoff["from_phase"], handoff["to_phase"])
    )
    lines.append("")

    lines.append("### Context carried forward")
    for item in handoff["context"]:
        lines.append("- %s" % item)
    lines.append("")

    lines.append("### Files identified")
    for f in handoff["files"]:
        lines.append("- %s (%s): %s" % (f["path"], f["role"], f["relevance"]))
    lines.append("")

    lines.append("### Open questions")
    for q in handoff["open_questions"]:
        lines.append("- %s" % q)
    lines.append("")

    lines.append("### Warnings")
    for w in handoff["warnings"]:
        lines.append("- %s" % w)

    return "\n".join(lines)
