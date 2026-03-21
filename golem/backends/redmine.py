"""Redmine backend adapters for the golem profile system.

Wraps the existing Redmine REST helpers in ``poller.py``, ``orchestrator.py``,
and ``supervisor.py`` behind the ``TaskSource`` and ``StateBackend`` protocols.
"""

import logging
from typing import Any

import requests as _requests

from ..core.defaults import REDMINE_ISSUES_URL
from ..core.service_clients import _request_with_retry, get_redmine_headers

from ..interfaces import TaskStatus

logger = logging.getLogger("golem.backends.redmine")

_DEFAULT_STATUS_MAP: dict[str, int] = {
    TaskStatus.IN_PROGRESS: 2,
    TaskStatus.FIXED: 3,
    TaskStatus.CLOSED: 5,
}

_status_map: dict[str, int] = dict(_DEFAULT_STATUS_MAP)


def configure_status_ids(mapping: dict[str, int]) -> None:
    """Override Redmine status IDs for non-standard instances.

    Example::

        configure_status_ids({TaskStatus.FIXED: 16})
    """
    _status_map.update(mapping)


# ---------------------------------------------------------------------------
# RedmineTaskSource
# ---------------------------------------------------------------------------


class RedmineTaskSource:
    """Discovers and reads tasks from Redmine issues."""

    def poll_tasks(
        self,
        projects: list[str],
        detection_tag: str,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        """Poll Redmine for open issues matching *detection_tag*."""
        from ..poller import get_agent_tasks

        return get_agent_tasks(projects, detection_tag=detection_tag, timeout=timeout)

    def get_task_subject(self, task_id: int | str) -> str:
        """Fetch the issue subject from Redmine."""
        from ..poller import get_issue_subject

        return get_issue_subject(int(task_id))

    def get_task_description(self, task_id: int | str) -> str:
        """Fetch the issue description from Redmine."""
        url = f"{REDMINE_ISSUES_URL}/{int(task_id)}.json"
        try:
            resp = _request_with_retry(
                _requests.get, url, headers=get_redmine_headers(), timeout=10
            )
            resp.raise_for_status()
            return resp.json().get("issue", {}).get("description", "") or ""
        except _requests.RequestException as exc:
            logger.warning("Could not fetch description for #%s: %s", task_id, exc)
            return ""

    def get_child_tasks(self, parent_id: int | str) -> list[dict[str, Any]]:
        """Fetch child issues from Redmine."""
        from ..poller import get_child_issues

        return get_child_issues(int(parent_id))

    def create_child_task(
        self,
        parent_id: int | str,
        subject: str,
        description: str,
    ) -> int | str | None:
        """Create a child issue under *parent_id* in Redmine."""
        parent_id = int(parent_id)
        project_id, tracker_id = _get_parent_issue_info(parent_id)
        if not project_id:
            logger.warning(
                "Cannot create child under #%d: unknown project_id", parent_id
            )
            return None

        issue_data: dict[str, Any] = {
            "project_id": project_id,
            "parent_issue_id": parent_id,
            "subject": subject,
            "description": description,
        }
        if tracker_id is not None:
            issue_data["tracker_id"] = tracker_id

        url = f"{REDMINE_ISSUES_URL}.json"
        try:
            resp = _request_with_retry(
                _requests.post,
                url,
                json={"issue": issue_data},
                headers=get_redmine_headers(),
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("issue", {}).get("id")
        except _requests.RequestException as exc:
            logger.warning("Failed to create child issue under #%d: %s", parent_id, exc)
            return None

    def poll_untagged_tasks(
        self,
        _projects: list[str],
        _exclude_tag: str,
        limit: int = 20,
        _timeout: int = 30,
    ) -> list[dict[str, Any]]:
        """Redmine backend does not support untagged issue discovery."""
        del limit  # keyword-passed by heartbeat.py; cannot rename
        return []

    def get_task_comments(
        self, task_id: int | str, *, since: str = ""
    ) -> list[dict[str, Any]]:
        """Fetch journal notes (comments) from a Redmine issue."""
        url = f"{REDMINE_ISSUES_URL}/{task_id}.json?include=journals"
        try:
            resp = _request_with_retry(
                _requests.get,
                url,
                headers=get_redmine_headers(),
                timeout=15,
            )
            resp.raise_for_status()
            journals = resp.json().get("issue", {}).get("journals", [])
            comments: list[dict[str, Any]] = []
            for j in journals:
                notes = j.get("notes", "").strip()
                if not notes:
                    continue
                created = j.get("created_on", "")
                if since and created <= since:
                    continue
                comments.append(
                    {
                        "author": j.get("user", {}).get("name", ""),
                        "body": notes,
                        "created_at": created,
                    }
                )
            return comments
        except _requests.RequestException as exc:
            logger.warning("Failed to fetch comments for #%s: %s", task_id, exc)
            return []


# ---------------------------------------------------------------------------
# RedmineStateBackend
# ---------------------------------------------------------------------------


class RedmineStateBackend:
    """Updates task state via Redmine REST API.

    Maps canonical ``TaskStatus`` strings to Redmine status IDs and verifies
    that transitions actually take effect.
    """

    def update_status(self, task_id: int | str, status: str) -> bool:
        """Map canonical status to Redmine status ID and update."""
        redmine_status = _status_map.get(status)
        if redmine_status is None:
            logger.warning("Unknown canonical status %r for task #%s", status, task_id)
            return False
        return _update_redmine_issue(int(task_id), status_id=redmine_status)

    def post_comment(self, task_id: int | str, text: str) -> bool:
        """Post a comment (notes) on the Redmine issue."""
        return _update_redmine_issue(int(task_id), notes=text)

    def update_progress(self, task_id: int | str, percent: int) -> bool:
        """Update the done_ratio on the Redmine issue."""
        return _update_redmine_issue(int(task_id), done_ratio=percent)


# ---------------------------------------------------------------------------
# Helpers (moved from orchestrator.py / supervisor.py)
# ---------------------------------------------------------------------------


def _update_redmine_issue(issue_id: int, **fields: Any) -> bool:
    """Update a Redmine issue via REST API.

    When *status_id* is among the fields, a follow-up GET verifies that the
    transition actually took effect (Redmine silently ignores invalid
    transitions while still returning 200).
    """
    url = f"{REDMINE_ISSUES_URL}/{issue_id}.json"
    try:
        resp = _request_with_retry(
            _requests.put,
            url,
            json={"issue": fields},
            headers=get_redmine_headers(),
            timeout=15,
        )
        resp.raise_for_status()
    except _requests.RequestException as exc:
        logger.warning("Failed to update Redmine #%d: %s", issue_id, exc)
        return False

    # Verify status transition actually took effect
    expected_status = fields.get("status_id")
    if expected_status is not None:
        try:
            get_resp = _request_with_retry(
                _requests.get,
                url,
                headers=get_redmine_headers(),
                timeout=10,
            )
            get_resp.raise_for_status()
            actual_status = get_resp.json().get("issue", {}).get("status", {}).get("id")
            if actual_status is not None and actual_status != expected_status:
                logger.warning(
                    "Redmine #%d: status transition silently failed — "
                    "expected status_id=%d but got %d",
                    issue_id,
                    expected_status,
                    actual_status,
                )
                return False
        except _requests.RequestException as exc:
            logger.debug(
                "Could not verify Redmine #%d status after update: %s",
                issue_id,
                exc,
            )
    return True


def _get_parent_issue_info(parent_id: int) -> tuple[str | None, int | None]:
    """Return ``(project_id, tracker_id)`` for *parent_id*."""
    url = f"{REDMINE_ISSUES_URL}/{parent_id}.json"
    try:
        resp = _request_with_retry(
            _requests.get, url, headers=get_redmine_headers(), timeout=10
        )
        resp.raise_for_status()
        issue = resp.json().get("issue", {})
        project = issue.get("project", {})
        return (
            project.get("identifier") or str(project.get("id", "")),
            issue.get("tracker", {}).get("id"),
        )
    except _requests.RequestException as exc:
        logger.warning("Failed to fetch info for #%d: %s", parent_id, exc)
        return None, None
