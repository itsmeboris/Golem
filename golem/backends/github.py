"""GitHub Issues backend — uses the ``gh`` CLI for issue operations."""

from __future__ import annotations

import json
import logging
import re
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
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
    )
    if result.returncode != 0 and not check:
        logger.debug(
            "gh %s failed (rc=%d): %s",
            " ".join(args),
            result.returncode,
            result.stderr.strip(),
        )
    return result


_DEP_PATTERN = re.compile(
    r"(?:depends\s+on|blocked\s+by|after)\s+#(\d+)",
    re.IGNORECASE,
)


def parse_dependencies(body: str | None) -> set[int]:
    """Extract issue numbers from dependency patterns in an issue body.

    Recognises ``Depends on #N``, ``Blocked by #N``, and ``After #N``
    (case-insensitive, with the ``#`` prefix).
    """
    if not body:
        return set()
    return {int(m) for m in _DEP_PATTERN.findall(body)}


def _detect_circular_deps(deps: dict[int, set[int]]) -> set[int]:
    """Return the set of all issue numbers involved in any dependency cycle.

    Uses iterative DFS-based cycle detection (three-colour marking).
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    colour: dict[int, int] = {}
    in_cycle: set[int] = set()

    all_nodes: set[int] = set(deps)
    for targets in deps.values():
        all_nodes |= targets

    def dfs(start: int) -> None:
        stack = [(start, iter(deps.get(start, set())))]
        path: list[int] = [start]
        colour[start] = GRAY
        while stack:
            node, children = stack[-1]
            try:
                child = next(children)
                if colour.get(child) == GRAY:
                    # Found a back-edge — everything in the path from child onward is a cycle
                    cycle_start = path.index(child)
                    in_cycle.update(path[cycle_start:])
                elif colour.get(child, WHITE) == WHITE:
                    colour[child] = GRAY
                    path.append(child)
                    stack.append((child, iter(deps.get(child, set()))))
            except StopIteration:
                colour[node] = BLACK
                stack.pop()
                if path and path[-1] == node:
                    path.pop()

    for node in all_nodes:
        if colour.get(node, WHITE) == WHITE:
            dfs(node)

    return in_cycle


def _is_issue_closed(issue_number: int, repo: str) -> bool:
    """Return True if the given issue is closed, False otherwise or on error."""
    try:
        result = _gh(
            "issue", "view", str(issue_number), "--json", "state", "--repo", repo
        )
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout)
        return data.get("state", "").upper() == "CLOSED"
    except (json.JSONDecodeError, OSError):
        return False


# ---------------------------------------------------------------------------
# GitHubTaskSource
# ---------------------------------------------------------------------------


class GitHubTaskSource:
    """Discover and read tasks from GitHub Issues via the ``gh`` CLI."""

    def __init__(self, repo: str = "") -> None:
        self._repo = repo

    @property
    def _repo_args(self) -> list[str]:
        """Return ``--repo <repo>`` args when a repo is configured."""
        return ["--repo", self._repo] if self._repo else []

    def poll_tasks(
        self,
        projects: list[str],
        detection_tag: str,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        """List open issues with the given label."""
        all_tasks: list[dict[str, Any]] = []
        for repo in projects:
            try:
                result = _gh(
                    "issue",
                    "list",
                    "--label",
                    detection_tag,
                    "--json",
                    "number,title,body",
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

                # Build dependency map and detect cycles
                deps: dict[int, set[int]] = {
                    issue["number"]: parse_dependencies(issue.get("body", ""))
                    for issue in issues
                }
                circular = _detect_circular_deps(deps)
                for num in circular:
                    logger.warning(
                        "Circular dependency detected for #%d, skipping", num
                    )

                for issue in issues:
                    num = issue["number"]
                    if num in circular:
                        continue
                    issue_deps = deps.get(num, set())
                    blocked = False
                    for dep in issue_deps:
                        if not _is_issue_closed(dep, repo):
                            logger.info(
                                "Skipping #%d: blocked by open dependency #%d",
                                num,
                                dep,
                            )
                            blocked = True
                            break
                    if not blocked:
                        all_tasks.append({"id": num, "subject": issue["title"]})
            except (json.JSONDecodeError, KeyError, OSError) as exc:
                logger.warning("Failed to poll GitHub issues for %s: %s", repo, exc)
        return all_tasks

    def get_task_subject(self, task_id: int | str) -> str:
        """Fetch issue title."""
        try:
            result = _gh(
                "issue", "view", str(task_id), "--json", "title", *self._repo_args
            )
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
            result = _gh(
                "issue", "view", str(task_id), "--json", "body", *self._repo_args
            )
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
        return []

    def create_child_task(
        self,
        parent_id: int | str,
        subject: str,
        description: str,
    ) -> int | str | None:
        """Not supported for GitHub Issues."""

    def get_task_comments(
        self, task_id: int | str, *, since: str = ""
    ) -> list[dict[str, Any]]:
        """Fetch comments on a GitHub issue via ``gh`` CLI."""
        try:
            raw = _gh(
                "issue", "view", str(task_id), "--json", "comments", *self._repo_args
            )
            if raw.returncode != 0:
                logger.warning(
                    "gh issue view comments failed for #%s: %s", task_id, raw.stderr
                )
                return []
            data = json.loads(raw.stdout)
            comments: list[dict[str, Any]] = []
            for c in data.get("comments", []):
                created = c.get("createdAt", "")
                if since and created <= since:
                    continue
                comments.append(
                    {
                        "author": c.get("author", {}).get("login", ""),
                        "body": c.get("body", ""),
                        "created_at": created,
                    }
                )
            return comments
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to fetch comments for #%s: %s", task_id, exc)
            return []

    def poll_untagged_tasks(
        self,
        projects: list[str],
        exclude_tag: str,
        limit: int = 20,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        """Return open issues that do NOT have the exclude_tag label."""
        all_tasks: list[dict[str, Any]] = []
        for repo in projects:
            try:
                result = _gh(
                    "issue",
                    "list",
                    "--json",
                    "number,title,body,labels",
                    "--state",
                    "open",
                    "--limit",
                    str(limit),
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
                    label_names = [
                        lbl.get("name", "") for lbl in issue.get("labels", [])
                    ]
                    if exclude_tag in label_names:
                        continue
                    all_tasks.append(
                        {
                            "id": issue["number"],
                            "subject": issue["title"],
                            "body": issue.get("body", ""),
                        }
                    )
            except (json.JSONDecodeError, KeyError, OSError) as exc:
                logger.warning(
                    "Failed to poll untagged GitHub issues for %s: %s", repo, exc
                )
        return all_tasks[:limit]


# ---------------------------------------------------------------------------
# GitHubStateBackend
# ---------------------------------------------------------------------------


class GitHubStateBackend:
    """Update issue state via the ``gh`` CLI."""

    def __init__(self, repo: str = "") -> None:
        self._repo = repo

    @property
    def _repo_args(self) -> list[str]:
        """Return ``--repo <repo>`` args when a repo is configured."""
        return ["--repo", self._repo] if self._repo else []

    def update_status(self, task_id: int | str, status: str) -> bool:
        """Add a status label and remove other status labels."""
        label = _STATUS_LABELS.get(status)
        if label is None:
            logger.warning("Unknown status %r for issue %s", status, task_id)
            return False

        repo_args = self._repo_args

        # Close or reopen the issue based on the target status
        if status == "closed":
            try:
                result = _gh("issue", "close", str(task_id), *repo_args)
                if result.returncode != 0:
                    logger.debug(
                        "gh issue close %s failed (non-fatal): %s",
                        task_id,
                        result.stderr,
                    )
            except OSError as exc:
                logger.debug("gh issue close %s failed (non-fatal): %s", task_id, exc)

            # Verify close actually took effect
            try:
                verify = _gh(
                    "issue", "view", str(task_id), "--json", "state", *repo_args
                )
                if verify.returncode == 0:
                    state = json.loads(verify.stdout).get("state", "")
                    if state.upper() != "CLOSED":
                        logger.warning(
                            "gh issue close %s: expected CLOSED but got %s",
                            task_id,
                            state,
                        )
            except (OSError, ValueError) as exc:
                logger.debug(
                    "Could not verify issue %s state after close: %s",
                    task_id,
                    exc,
                )
        elif status == "in_progress":
            try:
                result = _gh("issue", "reopen", str(task_id), *repo_args)
                if result.returncode != 0:
                    logger.debug(
                        "gh issue reopen %s failed (non-fatal): %s",
                        task_id,
                        result.stderr,
                    )
            except OSError as exc:
                logger.debug("gh issue reopen %s failed (non-fatal): %s", task_id, exc)

        # Remove other status labels first
        for other_status, other_label in _STATUS_LABELS.items():
            if other_status != status:
                try:
                    result = _gh(
                        "issue",
                        "edit",
                        str(task_id),
                        "--remove-label",
                        other_label,
                        *repo_args,
                    )
                    if result.returncode != 0:
                        logger.debug(
                            "Failed to remove label %s (non-fatal): %s",
                            other_label,
                            result.stderr,
                        )
                except OSError as exc:
                    logger.debug("Failed to remove label %s: %s", other_label, exc)
        # Add the new status label
        try:
            result = _gh(
                "issue", "edit", str(task_id), "--add-label", label, *repo_args
            )
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
            result = _gh(
                "issue", "comment", str(task_id), "--body", text, *self._repo_args
            )
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

    def assign_issue(self, task_id: int | str, assignee: str = "@me") -> bool:
        """Assign the issue to a user (default: authenticated user)."""
        try:
            result = _gh(
                "issue",
                "edit",
                str(task_id),
                "--add-assignee",
                assignee,
                *self._repo_args,
            )
            if result.returncode != 0:
                logger.warning(
                    "Failed to assign issue %s to %s: %s",
                    task_id,
                    assignee,
                    result.stderr,
                )
                return False
        except OSError as exc:
            logger.warning("Failed to assign issue %s: %s", task_id, exc)
            return False
        return True

    def create_pull_request(self, head: str, base: str, title: str, body: str) -> str:
        """Create a PR and return its URL. Returns empty string on failure."""
        try:
            result = _gh(
                "pr",
                "create",
                "--head",
                head,
                "--base",
                base,
                "--title",
                title,
                "--body",
                body,
                *self._repo_args,
            )
            if result.returncode != 0:
                logger.warning("Failed to create PR: %s", result.stderr)
                return ""
            return result.stdout.strip()
        except OSError as exc:
            logger.warning("Failed to create pull request: %s", exc)
            return ""
