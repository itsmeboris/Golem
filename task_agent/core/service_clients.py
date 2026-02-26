"""Shared auth/URL helpers and data-fetching clients for Redmine.

Centralised here so both core modules and flow modules can import from
a single location without circular imports.
"""

import logging
import os
import time

import requests

from .defaults import REDMINE_URL

logger = logging.getLogger("golem.core.service_clients")

# Transient HTTP status codes that warrant a retry.
_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


def _request_with_retry(
    method, *args, retries: int = 2, backoff: float = 0.5, **kwargs
):
    """Call *method* (e.g. ``requests.get``) with retry on transient failures.

    Retries on ``ConnectionError`` and HTTP 429/502/503/504 with exponential
    backoff.  Keeps test compatibility since the underlying ``requests.get``
    / ``requests.post`` calls are still mockable.
    """
    for attempt in range(retries + 1):
        try:
            resp = method(*args, **kwargs)
            if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < retries:
                delay = backoff * (2**attempt)
                logger.debug(
                    "HTTP %d from %s, retrying in %.1fs (attempt %d/%d)",
                    resp.status_code,
                    args[0] if args else "?",
                    delay,
                    attempt + 1,
                    retries,
                )
                time.sleep(delay)
                continue
            return resp
        except requests.ConnectionError:
            if attempt < retries:
                delay = backoff * (2**attempt)
                logger.debug(
                    "Connection error for %s, retrying in %.1fs (attempt %d/%d)",
                    args[0] if args else "?",
                    delay,
                    attempt + 1,
                    retries,
                )
                time.sleep(delay)
                continue
            raise
    return resp  # type: ignore[possibly-undefined]


def get_redmine_url() -> str:
    """Return the base Redmine URL from env or the default."""
    return os.getenv("REDMINE_URL", REDMINE_URL)


def get_redmine_headers() -> dict[str, str]:
    """Return HTTP headers with the Redmine API key for authentication."""
    api_key = os.getenv("REDMINE_API_KEY", "")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Redmine-API-Key"] = api_key
    return headers
