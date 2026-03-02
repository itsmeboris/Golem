"""Teams card builders for golem events.

Constructs Microsoft Teams Adaptive Cards for the key lifecycle moments of a
golem session: session start, successful completion, mid-run activity
updates, failure (agent crash), and escalation (validation did not pass).

Key exports:
- ``build_task_started_card`` — card sent when a golem session begins.
- ``build_task_completed_card`` — card sent on successful completion, including
  validation verdict, cost, and commit SHA.
- ``build_task_activity_card`` — mid-run progress card (future Graph API use).
- ``build_task_failure_card`` — card sent on agent crash or unrecoverable error.
- ``build_task_escalation_card`` — card sent when validation fails and the task
  needs human review.
"""

from typing import Any

from .core.defaults import REDMINE_ISSUES_URL, _fmt_duration
from .core.teams import (
    _card_envelope,
    _fact_set,
    _header_block,
    _open_url_action,
    _text_block,
)


def build_task_started_card(
    parent_id: int,
    subject: str,
) -> dict[str, Any]:
    """Card sent when a golem session starts execution."""
    body: list[dict[str, Any]] = [
        _header_block(f"Golem Started: #{parent_id}", color="accent"),
        _text_block(subject[:120]),
    ]
    actions = [_open_url_action("View Issue", f"{REDMINE_ISSUES_URL}/{parent_id}")]
    return _card_envelope(body, actions)


def build_task_completed_card(
    parent_id: int,
    subject: str,
    total_cost_usd: float,
    duration_s: float = 0.0,
    steps: int = 0,
    *,
    verdict: str = "",
    confidence: float = 0.0,
    concerns: list[str] | None = None,
    commit_sha: str = "",
    retry_count: int = 0,
) -> dict[str, Any]:
    """Card sent when session completes successfully (with validation details)."""
    color = "good" if retry_count == 0 else "accent"
    body: list[dict[str, Any]] = [
        _header_block(f"Golem Completed: #{parent_id}", color=color),
        _text_block(subject[:120]),
    ]

    facts: list[tuple[str, str]] = [
        ("Cost", f"${total_cost_usd:.2f}"),
        ("Duration", _fmt_duration(duration_s)),
        ("Steps", str(steps)),
    ]
    if verdict:
        facts.append(("Verdict", f"{verdict} ({confidence:.0%})"))
    if commit_sha:
        facts.append(("Commit", commit_sha))
    if retry_count:
        facts.append(("Retries", str(retry_count)))
    body.append(_fact_set(facts))

    if concerns:
        items = "\n".join(f"- {c}" for c in concerns[:5])
        body.append(_text_block(f"**Concerns**:\n{items}"))

    actions = [_open_url_action("View Issue", f"{REDMINE_ISSUES_URL}/{parent_id}")]
    return _card_envelope(body, actions)


def build_task_activity_card(
    parent_id: int,
    subject: str,
    status_text: str,
    elapsed_s: float,
    milestone_count: int,
) -> dict[str, Any]:
    """Card for mid-run updates (future threaded replies via Graph API)."""
    body: list[dict[str, Any]] = [
        _header_block(f"Golem: #{parent_id} — In Progress", color="accent"),
        _text_block(f"**{subject[:80]}**"),
        _text_block(status_text[:200] if status_text else "Working..."),
        _fact_set(
            [
                ("Elapsed", _fmt_duration(elapsed_s)),
                ("Steps", str(milestone_count)),
            ]
        ),
    ]
    actions = [_open_url_action("View Issue", f"{REDMINE_ISSUES_URL}/{parent_id}")]
    return _card_envelope(body, actions)


def build_task_failure_card(
    parent_id: int,
    subject: str,
    reason: str,
    cost_usd: float = 0.0,
    duration_s: float = 0.0,
    *,
    verdict: str = "",
) -> dict[str, Any]:
    """Card sent when a session fails (agent crash or unrecoverable error)."""
    facts: list[tuple[str, str]] = [
        ("Error", reason[:200]),
        ("Cost", f"${cost_usd:.2f}"),
        ("Duration", _fmt_duration(duration_s)),
    ]
    if verdict:
        facts.append(("Verdict", verdict))

    body: list[dict[str, Any]] = [
        _header_block(f"Golem Failed: #{parent_id}", color="attention"),
        _text_block(subject[:120]),
        _fact_set(facts),
    ]
    actions = [_open_url_action("View Issue", f"{REDMINE_ISSUES_URL}/{parent_id}")]
    return _card_envelope(body, actions)


def build_task_escalation_card(
    parent_id: int,
    subject: str,
    verdict: str,
    summary: str,
    concerns: list[str] | None = None,
    cost_usd: float = 0.0,
    duration_s: float = 0.0,
    retry_count: int = 0,
) -> dict[str, Any]:
    """Card sent when validation fails and the task is escalated for human review."""
    body: list[dict[str, Any]] = [
        _header_block(f"Golem Needs Review: #{parent_id}", color="warning"),
        _text_block(subject[:120]),
        _fact_set(
            [
                ("Verdict", verdict),
                ("Cost", f"${cost_usd:.2f}"),
                ("Duration", _fmt_duration(duration_s)),
                ("Retried", "Yes" if retry_count else "No"),
            ]
        ),
    ]

    if summary:
        body.append(_text_block(f"**Summary**: {summary[:300]}"))

    if concerns:
        items = "\n".join(f"- {c}" for c in concerns[:5])
        body.append(_text_block(f"**Concerns**:\n{items}"))

    actions = [_open_url_action("View Issue", f"{REDMINE_ISSUES_URL}/{parent_id}")]
    return _card_envelope(body, actions)
