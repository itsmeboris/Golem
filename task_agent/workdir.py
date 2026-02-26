"""Resolve per-task working directory from issue subject tags and description directives.

Resolution priority chain:
    1. ``work_dir:`` directive in issue description (most specific)
    2. ``[TAG]`` in subject → ``work_dirs`` mapping from config
    3. ``default_work_dir`` from config
    4. ``PROJECT_ROOT`` fallback

Key exports:
    resolve_work_dir — single entry-point that runs the full chain.
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger("Tools.AgentAutomation.Flows.TaskAgent.WorkDir")

# Match `work_dir: /some/path` on its own line in the description.
_WORKDIR_DIRECTIVE_RE = re.compile(r"(?:^|\n)\s*work_dir:\s*(\S+)", re.IGNORECASE)

# Match [TAG] patterns in the subject (e.g. [CHIPSIM], [CHIPSIM_IB]).
_SUBJECT_TAG_RE = re.compile(r"\[([A-Za-z0-9_-]+)\]")


def _parse_description_workdir(
    description: str, allowed_bases: list[str] | None = None
) -> str:
    """Extract ``work_dir:`` directive from issue description.  Returns path or ``""``."""
    if not description:
        return ""
    match = _WORKDIR_DIRECTIVE_RE.search(description)
    if match:
        path = match.group(1).strip()
        resolved = str(Path(path).resolve())
        # Validate against allowlist if provided
        if allowed_bases:
            if not any(
                resolved.startswith(str(Path(b).resolve())) for b in allowed_bases
            ):
                logger.warning(
                    "work_dir directive path %s is outside allowed bases %s — ignoring",
                    path,
                    allowed_bases,
                )
                return ""
        if Path(path).is_dir():
            logger.info("work_dir from description directive: %s", path)
            return path
        logger.warning("work_dir directive path does not exist: %s", path)
    return ""


def _parse_subject_workdir(subject: str, work_dirs: dict[str, str]) -> str:
    """Match subject ``[TAG]`` patterns against *work_dirs* mapping.  Returns path or ``""``."""
    if not subject or not work_dirs:
        return ""
    # Normalize mapping keys to uppercase for case-insensitive matching.
    normalized = {k.upper(): v for k, v in work_dirs.items()}
    for match in _SUBJECT_TAG_RE.finditer(subject):
        tag = match.group(1).upper()
        if tag == "AGENT":
            continue
        if tag in normalized:
            logger.info("work_dir from subject tag [%s]: %s", tag, normalized[tag])
            return normalized[tag]
    return ""


def resolve_work_dir(
    subject: str,
    description: str,
    work_dirs: dict[str, str],
    default_work_dir: str,
    project_root: str,
) -> str:
    """Resolve the working directory for a task.

    Walks the 4-level priority chain and returns the first match.
    """
    # Build allowlist from config-known directories for description directive validation
    allowed_bases = list(work_dirs.values())
    if default_work_dir:
        allowed_bases.append(default_work_dir)
    allowed_bases.append(project_root)

    # Priority 1: Description directive (validated against allowlist)
    path = _parse_description_workdir(description, allowed_bases=allowed_bases or None)
    if path:
        return path

    # Priority 2: Subject tag → config mapping
    path = _parse_subject_workdir(subject, work_dirs)
    if path:
        return path

    # Priority 3: Config default
    if default_work_dir:
        return default_work_dir

    # Priority 4: Project root
    return project_root
