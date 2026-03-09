"""GitHub Issues backend — uses the ``gh`` CLI for issue operations."""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

logger = logging.getLogger("golem.backends.github")

_STATUS_LABELS = {
    "in_progress": "in-progress",
    "fixed": "fixed",
    "closed": "closed",
}


def _gh(
    *args: str,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a ``gh`` CLI command and return the result."""
    cmd = ["gh", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
    )


# ---------------------------------------------------------------------------
# GitHubTaskSource
# ---------------------------------------------------------------------------


class GitHubTaskSource:
    """Discover and read tasks from GitHub Issues via the ``gh`` CLI."""

    def poll_tasks(
        self,
        projects: list[str],
        detection_tag: str,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        """List open issues with the given label."""
        del timeout
        all_tasks: list[dict[str, Any]] = []
        for repo in projects:
            try:
                result = _gh(
                    "issue",
                    "list",
                    "--label",
                    detection_tag,
                    "--json",
                    "number,title",
                    "--state",
                    "open",
                    "--repo",
                    repo,
                )
                if result.returncode != 0:
                    logger.warning(
                        "gh issue list failed for %s: %s", repo, result.stderr
                    )
                    continue
                issues = json.loads(result.stdout) if result.stdout.strip() else []
                for issue in issues:
                    all_tasks.append({"id": issue["number"], "subject": issue["title"]})
            except (json.JSONDecodeError, KeyError, OSError) as exc:
                logger.warning("Failed to poll GitHub issues for %s: %s", repo, exc)
        return all_tasks

    def get_task_subject(self, task_id: int | str) -> str:
        """Fetch issue title."""
        try:
            result = _gh("issue", "view", str(task_id), "--json", "title")
            if result.returncode != 0:
                logger.warning("gh issue view failed: %s", result.stderr)
                return ""
            data = json.loads(result.stdout)
            return data.get("title", "")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to get issue subject %s: %s", task_id, exc)
            return ""

    def get_task_description(self, task_id: int | str) -> str:
        """Fetch issue body."""
        try:
            result = _gh("issue", "view", str(task_id), "--json", "body")
            if result.returncode != 0:
                logger.warning("gh issue view failed: %s", result.stderr)
                return ""
            data = json.loads(result.stdout)
            return data.get("body", "") or ""
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to get issue description %s: %s", task_id, exc)
            return ""

    def get_child_tasks(self, parent_id: int | str) -> list[dict[str, Any]]:
        """GitHub Issues has no native sub-issue support."""
        del parent_id
        return []

    def create_child_task(
        self,
        parent_id: int | str,
        subject: str,
        description: str,
    ) -> int | str | None:
        """Not supported for GitHub Issues."""
        del parent_id, subject, description


# ---------------------------------------------------------------------------
# GitHubStateBackend
# ---------------------------------------------------------------------------


class GitHubStateBackend:
    """Update issue state via the ``gh`` CLI."""

    def update_status(self, task_id: int | str, status: str) -> bool:
        """Add a status label and remove other status labels."""
        label = _STATUS_LABELS.get(status)
        if label is None:
            logger.warning("Unknown status %r for issue %s", status, task_id)
            return False
        # Remove other status labels first
        for other_status, other_label in _STATUS_LABELS.items():
            if other_status != status:
                try:
                    _gh(
                        "issue",
                        "edit",
                        str(task_id),
                        "--remove-label",
                        other_label,
                    )
                except OSError:
                    pass
        # Add the new status label
        try:
            result = _gh("issue", "edit", str(task_id), "--add-label", label)
            if result.returncode != 0:
                logger.warning(
                    "Failed to set label %s on issue %s: %s",
                    label,
                    task_id,
                    result.stderr,
                )
                return False
        except OSError as exc:
            logger.warning("Failed to update status for issue %s: %s", task_id, exc)
            return False
        return True

    def post_comment(self, task_id: int | str, text: str) -> bool:
        """Post a comment on the issue."""
        try:
            result = _gh("issue", "comment", str(task_id), "--body", text)
            if result.returncode != 0:
                logger.warning(
                    "Failed to comment on issue %s: %s", task_id, result.stderr
                )
                return False
        except OSError as exc:
            logger.warning("Failed to comment on issue %s: %s", task_id, exc)
            return False
        return True

    def update_progress(self, task_id: int | str, percent: int) -> bool:
        """Post a progress comment on the issue."""
        return self.post_comment(task_id, f"Progress: {percent}%")
