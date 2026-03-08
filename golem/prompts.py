"""Load and format golem prompt templates.

Reads ``.txt`` template files from the ``prompts/`` subdirectory and fills in
placeholders using Python's ``str.format_map``.  Missing placeholders are left
as-is so callers can supply only the fields they need.

Key exports:
- ``load_prompt`` — reads a raw template file by name.
- ``format_prompt`` — loads a template and substitutes keyword arguments.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    """Read a prompt template file by name."""
    prompt_file = PROMPTS_DIR / name
    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")
    return prompt_file.read_text()


def _apply_description_guard(name: str, kwargs: dict) -> None:
    """Replace empty *task_description* with a subject-based fallback."""
    if "task_description" in kwargs and not kwargs["task_description"].strip():
        subject = kwargs.get("parent_subject", kwargs.get("issue_id", "unknown"))
        logger.warning(
            "Empty task_description for template %s, using subject fallback",
            name,
        )
        kwargs[
            "task_description"
        ] = f"Implement the following based on the subject: {subject}"


def format_prompt(name: str, **kwargs) -> str:
    """Load a prompt template and fill in *kwargs* placeholders.

    Unrecognised placeholders are left as-is so templates can contain
    optional fields that callers don't always supply.
    """
    _apply_description_guard(name, kwargs)
    template = load_prompt(name)
    return template.format_map(_SafeDict(kwargs))


class _SafeDict(dict):
    """dict subclass that returns the placeholder for missing keys."""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


class FilePromptProvider:
    """PromptProvider backed by ``.txt`` template files in a directory.

    Satisfies the ``PromptProvider`` protocol from ``interfaces.py``.
    Defaults to the built-in ``prompts/`` directory when no *prompts_dir*
    is given.
    """

    def __init__(self, prompts_dir: str | Path | None = None):
        self._dir = Path(prompts_dir) if prompts_dir else PROMPTS_DIR

    def format(self, template_name: str, **kwargs) -> str:
        """Load a template from the configured directory and fill placeholders."""
        _apply_description_guard(template_name, kwargs)
        prompt_file = self._dir / template_name
        if not prompt_file.exists():
            raise FileNotFoundError(f"Prompt file not found: {prompt_file}")
        template = prompt_file.read_text()
        return template.format_map(_SafeDict(kwargs))
