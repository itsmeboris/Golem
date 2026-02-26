"""Single source of truth for commit message tag definitions.

Loads ``commit_format.yaml`` once and exposes the tags as plain data
so both prompt templates and validation code reference the same list.

The config is re-read from disk when the file's mtime changes, so edits
are picked up by long-running daemons without a restart.
"""

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "commit_format.yaml"

_EMPTY = None  # sentinel, assigned below after class definition
_cache: dict[str, Any] = {
    "lock": threading.Lock(),
    "format": None,  # CommitFormat | None
    "mtime": 0.0,
    "size": -1,
    "path": None,  # Path | None
}


@dataclass(frozen=True)
class CommitFormat:
    """Immutable container for commit-message tag definitions."""

    main_tags: tuple[str, ...]
    sub_tags_hw: tuple[str, ...]
    sub_tags_areas: tuple[str, ...]
    sub_tags_chips: tuple[str, ...]

    @property
    def main_tags_str(self) -> str:  # pylint: disable=missing-function-docstring
        return ", ".join(self.main_tags)

    @property
    def sub_tags_hw_str(self) -> str:  # pylint: disable=missing-function-docstring
        return ", ".join(self.sub_tags_hw)

    @property
    def sub_tags_areas_str(self) -> str:  # pylint: disable=missing-function-docstring
        return ", ".join(self.sub_tags_areas)

    @property
    def sub_tags_chips_str(self) -> str:  # pylint: disable=missing-function-docstring
        return ", ".join(self.sub_tags_chips)

    def prompt_vars(self) -> dict[str, str]:
        """Return a dict ready to inject into prompt templates."""
        return {
            "main_tags": self.main_tags_str,
            "sub_tags_hw": self.sub_tags_hw_str,
            "sub_tags_areas": self.sub_tags_areas_str,
            "sub_tags_chips": self.sub_tags_chips_str,
        }


_EMPTY = CommitFormat(
    main_tags=(), sub_tags_hw=(), sub_tags_areas=(), sub_tags_chips=()
)


def _parse(raw: dict[str, Any]) -> CommitFormat:
    sub = raw.get("sub_tags") or {}
    return CommitFormat(
        main_tags=tuple(raw.get("main_tags") or []),
        sub_tags_hw=tuple(sub.get("hardware") or []),
        sub_tags_areas=tuple(sub.get("areas") or []),
        sub_tags_chips=tuple(sub.get("chips") or []),
    )


def load_commit_format(path: Path | None = None) -> CommitFormat:
    """Load the commit format definition, re-reading on file changes.

    The result is cached and only re-read when the file's mtime changes,
    so this is safe to call frequently from hot paths while still picking
    up edits in a long-running daemon.

    Parameters
    ----------
    path:
        Override for testing; defaults to ``commit_format.yaml`` at
        project root.
    """
    target = path or _CONFIG_PATH

    with _cache["lock"]:
        # Fast path: same file, mtime unchanged.
        if _cache["format"] is not None and _cache["path"] == target:
            try:
                current_mtime = os.path.getmtime(target)
            except OSError:
                return _cache["format"]
            if (
                current_mtime == _cache["mtime"]
                and os.path.getsize(target) == _cache["size"]
            ):
                return _cache["format"]

        # (Re-)load from disk.
        if not target.exists():
            _cache["format"] = _EMPTY
            _cache["mtime"] = 0.0
            _cache["path"] = target
            return _EMPTY

        with open(target, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        result = _parse(raw)

        _cache["mtime"] = os.path.getmtime(target)
        _cache["size"] = os.path.getsize(target)
        _cache["path"] = target
        _cache["format"] = result
        return result


def _clear_cache() -> None:
    """Reset the module-level cache (for tests)."""
    with _cache["lock"]:
        _cache["format"] = None
        _cache["mtime"] = 0.0
        _cache["size"] = -1
        _cache["path"] = None
