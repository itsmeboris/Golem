"""Poll Redmine for [AGENT]-tagged issues.

Provides the detection layer for the golem flow.  Uses Redmine's
server-side ``subject=~TAG`` filter to fetch only matching issues, avoiding
expensive client-side pagination through large projects.

Key exports:
- ``get_agent_tasks`` — scans multiple projects and returns all open
  ``[AGENT]``-tagged issues.
- ``is_agent_task`` — predicate for checking if a subject matches the tag.
- ``get_issue_subject`` — fetches the subject of a single issue by ID.
- ``get_child_issues`` — fetches child issues of a given parent issue.
"""

import logging
from typing import Any

import requests

from .core.defaults import HTTP_TIMEOUT
from .core.service_clients import get_redmine_headers, get_redmine_url

logger = logging.getLogger("golem.poller")


def is_agent_task(subject: str, detection_tag: str = "[AGENT]") -> bool:
    """Return True if *subject* contains the detection tag."""
    return detection_tag.upper() in subject.upper()


def get_agent_tasks(
    projects: list[str],
    detection_tag: str = "[AGENT]",
    issues_per_page: int = 100,
    timeout: int = HTTP_TIMEOUT,
) -> list[dict[str, Any]]:
    """Fetch open issues containing *detection_tag* in their subject.

    Uses Redmine's server-side ``subject=~TAG`` filter so only matching
    issues are returned, regardless of how many total open issues exist
    in the project.
    """
    base = get_redmine_url()
    headers = get_redmine_headers()
    results: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    for project in projects:
        url = f"{base}/issues.json"
        params: dict[str, str] = {
            "project_id": project,
            "status_id": "open",
            "subject": f"~{detection_tag}",
            "sort": "created_on:desc",
            "limit": str(issues_per_page),
        }
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
            resp.raise_for_status()
            for issue in resp.json().get("issues", []):
                iid = issue.get("id")
                if iid and iid not in seen_ids:
                    results.append(issue)
                    seen_ids.add(iid)
        except requests.RequestException as exc:
            logger.error("Failed to fetch agent tasks for project %s: %s", project, exc)

    logger.info("Found %d %s issue(s)", len(results), detection_tag)
    return results


def get_issue_subject(
    issue_id: int,
    timeout: int = HTTP_TIMEOUT,
) -> str:
    """Fetch the subject of a single Redmine issue by ID.

    Returns the subject string or a fallback like ``"[AGENT] task #12345"``.
    """
    base = get_redmine_url()
    url = f"{base}/issues/{issue_id}.json"
    try:
        resp = requests.get(url, headers=get_redmine_headers(), timeout=timeout)
        resp.raise_for_status()
        issue = resp.json().get("issue", {})
        subject = issue.get("subject", "")
        if subject:
            return subject
    except requests.RequestException as exc:
        logger.warning("Failed to fetch subject for #%d: %s", issue_id, exc)
    return f"[AGENT] task #{issue_id}"


def get_child_issues(
    parent_id: int,
    timeout: int = HTTP_TIMEOUT,
) -> list[dict[str, Any]]:
    """Fetch child issues of *parent_id* from Redmine."""
    base = get_redmine_url()
    url = f"{base}/issues.json"
    params = {
        "parent_id": str(parent_id),
        "status_id": "*",
        "limit": "50",
    }
    try:
        resp = requests.get(
            url, headers=get_redmine_headers(), params=params, timeout=timeout
        )
        resp.raise_for_status()
        children = resp.json().get("issues", [])
        logger.info(
            "Fetched %d child issue(s) for parent #%d", len(children), parent_id
        )
        return children
    except requests.RequestException as exc:
        logger.error("Failed to fetch children for #%d: %s", parent_id, exc)
        return []
