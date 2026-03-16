"""Instinct store: persistent confidence-weighted pitfall memory for Golem."""

import json
import logging
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .pitfall_extractor import _is_duplicate
from .pitfall_writer import (
    _CATEGORIES,
    _parse_metadata,
    _strip_metadata,
    parse_agents_md,
)

logger = logging.getLogger("golem.instinct_store")

# Header/comment constants (re-exported for use in generate_agents_md)
_HEADER = "# AGENTS.md — Golem Learning\n"
_AUTO_COMMENT = (
    "<!-- Auto-maintained by Golem's post-task learning loop."
    " Do not edit manually. -->\n"
)

_CONFIDENCE_MIN = 0.1
_CONFIDENCE_MAX = 0.9
_CONFIDENCE_DEFAULT = 0.5
_CONFIDENCE_CONFIRM_DELTA = 0.1
_CONFIDENCE_CONTRADICT_DELTA = 0.1
_CONFIDENCE_CAP = 0.95
_CONFIDENCE_FLOOR = 0.0
_CONFIDENCE_ARCHIVE_THRESHOLD = 0.2
_CONFIDENCE_STRONG_THRESHOLD = 0.8


@dataclass
class Instinct:
    """A single learned instinct with a confidence score."""

    id: str
    text: str
    category: str
    confidence: float
    created_at: str
    last_confirmed: str
    confirmation_count: int = 0
    contradiction_count: int = 0
    archived: bool = False


class InstinctStore:
    """Persistent store for instincts, backed by a JSON file."""

    def __init__(self, storage_path: Path) -> None:
        self._path = Path(storage_path)

    # -- Persistence ----------------------------------------------------------

    def _load(self) -> list[Instinct]:
        """Load instincts from JSON file, returning empty list if missing."""
        if not self._path.exists():
            return []
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return [Instinct(**item) for item in data]

    def _save(self, instincts: list[Instinct]) -> None:
        """Atomically write instincts to JSON file (temp + os.replace)."""
        dir_path = self._path.parent
        dir_path.mkdir(parents=True, exist_ok=True)
        data = [asdict(inst) for inst in instincts]
        fd, tmp_path = tempfile.mkstemp(
            dir=dir_path, prefix=".instinct_store_", suffix=".json"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError as exc:
                logger.debug("Failed to unlink instinct store temp file: %s", exc)
            raise

    # -- Mutation -------------------------------------------------------------

    def add(
        self,
        text: str,
        category: str,
        initial_confidence: float = _CONFIDENCE_DEFAULT,
    ) -> Instinct:
        """Add a new instinct, or confirm an existing duplicate.

        Clamps initial_confidence to [0.1, 0.9].  If a near-duplicate already
        exists (active or archived), confirms it instead of creating a new one.

        Returns the instinct (new or confirmed existing).
        """
        confidence = max(_CONFIDENCE_MIN, min(_CONFIDENCE_MAX, initial_confidence))
        instincts = self._load()

        # Check for duplicates against all existing instincts
        existing_texts = [inst.text for inst in instincts]
        match_idx = _is_duplicate(text, existing_texts)
        if match_idx is not None:
            existing = instincts[match_idx]
            self.confirm(existing.id)
            # Reload to get updated version after confirm saved
            for inst in self._load():
                if inst.id == existing.id:
                    return inst
            return existing  # defensive fallback

        today = date.today().isoformat()
        new_instinct = Instinct(
            id=str(uuid.uuid4()),
            text=text,
            category=category,
            confidence=confidence,
            created_at=today,
            last_confirmed=today,
        )
        instincts.append(new_instinct)
        self._save(instincts)
        return new_instinct

    def confirm(self, instinct_id: str) -> None:
        """Increase confidence by 0.1 (cap 0.95), increment confirmation_count."""
        instincts = self._load()
        today = date.today().isoformat()
        for inst in instincts:
            if inst.id == instinct_id:
                inst.confidence = min(
                    _CONFIDENCE_CAP,
                    round(inst.confidence + _CONFIDENCE_CONFIRM_DELTA, 10),
                )
                inst.confirmation_count += 1
                inst.last_confirmed = today
                if inst.archived and inst.confidence >= _CONFIDENCE_ARCHIVE_THRESHOLD:
                    inst.archived = False
                break
        self._save(instincts)

    def contradict(self, instinct_id: str) -> None:
        """Decrease confidence by 0.1 (floor 0.0), archive if below 0.2."""
        instincts = self._load()
        for inst in instincts:
            if inst.id == instinct_id:
                inst.confidence = max(
                    _CONFIDENCE_FLOOR,
                    round(inst.confidence - _CONFIDENCE_CONTRADICT_DELTA, 10),
                )
                inst.contradiction_count += 1
                if inst.confidence < _CONFIDENCE_ARCHIVE_THRESHOLD:
                    inst.archived = True
                break
        self._save(instincts)

    def prune(self) -> list[Instinct]:
        """Archive instincts below 0.2 confidence. Returns newly archived list."""
        instincts = self._load()
        newly_archived: list[Instinct] = []
        for inst in instincts:
            if not inst.archived and inst.confidence < _CONFIDENCE_ARCHIVE_THRESHOLD:
                inst.archived = True
                newly_archived.append(inst)
        if newly_archived:
            self._save(instincts)
        return newly_archived

    # -- Queries --------------------------------------------------------------

    def get_active(self) -> list[Instinct]:
        """Return non-archived instincts."""
        return [inst for inst in self._load() if not inst.archived]

    def get_all(self) -> list[Instinct]:
        """Return all instincts including archived."""
        return self._load()

    # -- AGENTS.md generation -------------------------------------------------

    def generate_agents_md(self, preamble: str = "") -> str:
        """Generate AGENTS.md content from active instincts.

        Groups by category using _CATEGORIES order, sorts by confidence
        descending within each group.  Instincts with confidence > 0.8
        get ' [strong]' appended.

        If preamble is empty, uses _HEADER + _AUTO_COMMENT.
        """
        active = self.get_active()

        if preamble.strip():
            parts = [preamble.rstrip("\n") + "\n\n"]
        else:
            parts = [_HEADER + _AUTO_COMMENT + "\n"]

        for cat, header in _CATEGORIES:
            cat_instincts = [inst for inst in active if inst.category == cat]
            if not cat_instincts:
                continue
            # Sort by confidence descending
            cat_instincts.sort(key=lambda inst: inst.confidence, reverse=True)
            parts.append(header)
            for inst in cat_instincts:
                text = inst.text
                if inst.confidence > _CONFIDENCE_STRONG_THRESHOLD:
                    text = text + " [strong]"
                parts.append(f"- {text}\n")
            parts.append("\n")

        return "".join(parts).rstrip("\n") + "\n"

    # -- Migration ------------------------------------------------------------

    def migrate_from_agents_md(self, agents_md_path: Path) -> int:
        """Import entries from an existing AGENTS.md into the instinct store.

        Maps seen count to initial confidence:
          seen=1 → 0.3
          seen=2 → 0.4
          seen≥3 → 0.5 + 0.05 * min(seen-3, 6)

        Returns the count of imported instincts.
        """
        content = Path(agents_md_path).read_text(encoding="utf-8")
        categorized = parse_agents_md(content)

        count = 0
        instincts = self._load()
        today = date.today().isoformat()

        for cat, entries in categorized.items():
            for entry in entries:
                seen, _ = _parse_metadata(entry)
                text = _strip_metadata(entry).strip()
                if not text:
                    continue

                confidence = _seen_to_confidence(seen)

                new_instinct = Instinct(
                    id=str(uuid.uuid4()),
                    text=text,
                    category=cat,
                    confidence=confidence,
                    created_at=today,
                    last_confirmed=today,
                )
                instincts.append(new_instinct)
                count += 1

        if count > 0:
            self._save(instincts)

        return count


def _seen_to_confidence(seen: int) -> float:
    """Map a seen count to an initial confidence value.

    seen=1 → 0.3
    seen=2 → 0.4
    seen≥3 → 0.5 + 0.05 * min(seen-3, 6)
    """
    if seen == 1:
        return 0.3
    if seen == 2:
        return 0.4
    return 0.5 + 0.05 * min(seen - 3, 6)
