# pylint: disable=too-many-lines
"""Tests for golem.instinct_store — Instinct dataclass and InstinctStore."""

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from golem.pitfall_extractor import (
    CATEGORY_ANTIPATTERNS,
    CATEGORY_ARCHITECTURE,
    CATEGORY_COVERAGE,
)
from golem.pitfall_writer import _AUTO_COMMENT, _HEADER

# ---------------------------------------------------------------------------
# Instinct dataclass tests
# ---------------------------------------------------------------------------


class TestInstinctDataclass:
    def test_required_fields(self):
        """Instinct can be constructed with all required fields."""
        from golem.instinct_store import Instinct

        inst = Instinct(
            id="abc-123",
            text="always run tests before shipping",
            category=CATEGORY_ANTIPATTERNS,
            confidence=0.5,
            created_at="2026-03-17",
            last_confirmed="2026-03-17",
        )
        assert inst.id == "abc-123"
        assert inst.text == "always run tests before shipping"
        assert inst.category == CATEGORY_ANTIPATTERNS
        assert inst.confidence == 0.5
        assert inst.created_at == "2026-03-17"
        assert inst.last_confirmed == "2026-03-17"
        assert inst.confirmation_count == 0
        assert inst.contradiction_count == 0
        assert inst.archived is False

    def test_default_archived_false(self):
        """archived defaults to False."""
        from golem.instinct_store import Instinct

        inst = Instinct(
            id="x",
            text="text",
            category=CATEGORY_COVERAGE,
            confidence=0.5,
            created_at="2026-01-01",
            last_confirmed="2026-01-01",
        )
        assert inst.archived is False

    def test_all_fields_settable(self):
        """All fields can be set at construction."""
        from golem.instinct_store import Instinct

        inst = Instinct(
            id="id1",
            text="test text",
            category=CATEGORY_ARCHITECTURE,
            confidence=0.7,
            created_at="2025-01-01",
            last_confirmed="2025-06-01",
            confirmation_count=3,
            contradiction_count=1,
            archived=True,
        )
        assert inst.confirmation_count == 3
        assert inst.contradiction_count == 1
        assert inst.archived is True


# ---------------------------------------------------------------------------
# InstinctStore.add() tests
# ---------------------------------------------------------------------------


class TestInstinctStoreAdd:
    def test_add_creates_new_instinct(self, tmp_path):
        """add() creates a new Instinct with default confidence 0.5."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("check coverage before merging", CATEGORY_COVERAGE)

        assert inst.text == "check coverage before merging"
        assert inst.category == CATEGORY_COVERAGE
        assert inst.confidence == 0.5
        assert inst.archived is False
        assert inst.confirmation_count == 0
        assert inst.contradiction_count == 0

    def test_add_with_custom_confidence(self, tmp_path):
        """add() accepts a custom initial_confidence."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("some pitfall text here", CATEGORY_ANTIPATTERNS, 0.7)
        assert inst.confidence == 0.7

    def test_add_clamps_confidence_above_max(self, tmp_path):
        """add() clamps initial_confidence above 0.9 down to 0.9."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("some pitfall above max", CATEGORY_ANTIPATTERNS, 1.0)
        assert inst.confidence == 0.9

    def test_add_clamps_confidence_below_min(self, tmp_path):
        """add() clamps initial_confidence below 0.1 up to 0.1."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("some pitfall below min", CATEGORY_ANTIPATTERNS, 0.0)
        assert inst.confidence == 0.1

    def test_add_clamps_confidence_at_exact_min(self, tmp_path):
        """add() allows confidence exactly at 0.1."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("pitfall at min boundary", CATEGORY_ANTIPATTERNS, 0.1)
        assert inst.confidence == 0.1

    def test_add_clamps_confidence_at_exact_max(self, tmp_path):
        """add() allows confidence exactly at 0.9."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("pitfall at max boundary", CATEGORY_ANTIPATTERNS, 0.9)
        assert inst.confidence == 0.9

    def test_add_generates_unique_ids(self, tmp_path):
        """add() assigns unique UUID ids to each new instinct."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst1 = store.add("pitfall alpha unique first", CATEGORY_ANTIPATTERNS)
        inst2 = store.add("pitfall beta unique second", CATEGORY_COVERAGE)
        assert inst1.id != inst2.id

    def test_add_sets_created_at_today(self, tmp_path):
        """add() sets created_at to today's ISO date."""
        from golem.instinct_store import InstinctStore

        today = date.today().isoformat()
        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("check today date pitfall", CATEGORY_COVERAGE)
        assert inst.created_at == today

    def test_add_persists_to_json(self, tmp_path):
        """add() saves instinct to JSON file."""
        from golem.instinct_store import InstinctStore

        path = tmp_path / "store.json"
        store = InstinctStore(path)
        store.add("persisted pitfall test", CATEGORY_ARCHITECTURE)

        data = json.loads(path.read_text())
        assert len(data) == 1
        assert data[0]["text"] == "persisted pitfall test"

    def test_add_duplicate_confirms_existing(self, tmp_path):
        """add() with near-duplicate text confirms the existing instinct instead."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        original = store.add(
            "always run tests before submitting code for review", CATEGORY_ANTIPATTERNS
        )
        original_id = original.id
        original_confidence = original.confidence

        # Near-duplicate: same tokens, slightly different phrasing
        result = store.add(
            "always run tests before submitting code for review", CATEGORY_ANTIPATTERNS
        )

        # Should return the existing instinct (confirmed)
        assert result.id == original_id
        assert result.confidence > original_confidence
        assert result.confirmation_count == 1

    def test_add_duplicate_does_not_create_new_entry(self, tmp_path):
        """add() with duplicate does not add a second instinct."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        store.add(
            "always run tests before submitting code for review", CATEGORY_ANTIPATTERNS
        )
        store.add(
            "always run tests before submitting code for review", CATEGORY_ANTIPATTERNS
        )

        assert len(store.get_all()) == 1

    def test_add_non_duplicate_creates_separate(self, tmp_path):
        """add() with completely different text creates separate instinct."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        store.add("check coverage gaps thoroughly", CATEGORY_COVERAGE)
        store.add(
            "architecture should decouple components properly", CATEGORY_ARCHITECTURE
        )

        all_instincts = store.get_all()
        assert len(all_instincts) == 2

    def test_add_duplicate_fallback_when_reload_misses(self, tmp_path):
        """add() returns original instinct if reload after confirm doesn't find it."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        original = store.add(
            "always run tests before submitting code for review", CATEGORY_ANTIPATTERNS
        )
        original_id = original.id

        # Patch _load to return empty list on the second call (after confirm)
        real_load = store._load
        call_count = 0

        def _patched_load():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return []
            return real_load()

        with patch.object(store, "_load", side_effect=_patched_load):
            result = store.add(
                "always run tests before submitting code for review",
                CATEGORY_ANTIPATTERNS,
            )

        # Falls back to returning the original (pre-confirm) instinct
        assert result.id == original_id


# ---------------------------------------------------------------------------
# InstinctStore.confirm() and contradict() tests
# ---------------------------------------------------------------------------


class TestInstinctStoreConfirmContradict:
    def test_confirm_increases_confidence_by_01(self, tmp_path):
        """confirm() increases confidence by 0.1."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("test pitfall confirm", CATEGORY_ANTIPATTERNS, 0.5)
        store.confirm(inst.id)
        updated = store.get_all()[0]
        assert abs(updated.confidence - 0.6) < 1e-9

    def test_confirm_caps_at_095(self, tmp_path):
        """confirm() caps confidence at 0.95."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("cap test pitfall entry", CATEGORY_ANTIPATTERNS, 0.9)
        store.confirm(inst.id)
        updated = store.get_all()[0]
        assert updated.confidence == 0.95

    def test_confirm_does_not_exceed_095(self, tmp_path):
        """confirm() on already 0.95 confidence stays at 0.95."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("boundary confirm pitfall", CATEGORY_ANTIPATTERNS, 0.9)
        store.confirm(inst.id)  # -> 0.95
        store.confirm(inst.id)  # -> would be 1.05, capped at 0.95
        updated = store.get_all()[0]
        assert updated.confidence == 0.95

    def test_confirm_increments_confirmation_count(self, tmp_path):
        """confirm() increments confirmation_count."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("count confirm pitfall test", CATEGORY_COVERAGE)
        store.confirm(inst.id)
        store.confirm(inst.id)
        updated = store.get_all()[0]
        assert updated.confirmation_count == 2

    def test_confirm_updates_last_confirmed(self, tmp_path):
        """confirm() updates last_confirmed to today."""
        from golem.instinct_store import InstinctStore

        today = date.today().isoformat()
        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("last confirmed date pitfall", CATEGORY_COVERAGE)
        store.confirm(inst.id)
        updated = store.get_all()[0]
        assert updated.last_confirmed == today

    def test_contradict_decreases_confidence_by_01(self, tmp_path):
        """contradict() decreases confidence by 0.1."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("contradict pitfall entry test", CATEGORY_ANTIPATTERNS, 0.5)
        store.contradict(inst.id)
        updated = store.get_all()[0]
        assert abs(updated.confidence - 0.4) < 1e-9

    def test_contradict_floors_at_00(self, tmp_path):
        """contradict() floors confidence at 0.0."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("floor contradict pitfall", CATEGORY_ANTIPATTERNS, 0.1)
        store.contradict(inst.id)
        updated = store.get_all()[0]
        assert updated.confidence == 0.0

    def test_contradict_increments_contradiction_count(self, tmp_path):
        """contradict() increments contradiction_count."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("count contradict pitfall", CATEGORY_COVERAGE)
        store.contradict(inst.id)
        store.contradict(inst.id)
        updated = store.get_all()[0]
        assert updated.contradiction_count == 2

    def test_contradict_archives_below_02(self, tmp_path):
        """contradict() archives instinct if confidence drops below 0.2."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("archive contradict pitfall", CATEGORY_ANTIPATTERNS, 0.2)
        store.contradict(inst.id)  # -> 0.1 < 0.2, should archive
        updated = store.get_all()[0]
        assert updated.archived is True

    def test_contradict_does_not_archive_at_02(self, tmp_path):
        """contradict() does not archive when confidence is still at 0.2."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("do not archive contradict", CATEGORY_ANTIPATTERNS, 0.3)
        store.contradict(inst.id)  # -> 0.2, exactly at threshold
        updated = store.get_all()[0]
        assert updated.archived is False
        assert abs(updated.confidence - 0.2) < 1e-9

    def test_confirm_unarchives_when_confidence_reaches_threshold(self, tmp_path):
        """confirm() un-archives an instinct when confidence rises back to >= 0.2."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        # Start at 0.2, contradict until archived (drops to 0.1, then 0.0)
        inst = store.add(
            "re-emerging pitfall unarchive confirm", CATEGORY_ANTIPATTERNS, 0.2
        )
        store.contradict(inst.id)  # -> 0.1, archived
        store.contradict(inst.id)  # -> 0.0, archived

        archived = store.get_all()[0]
        assert archived.archived is True
        assert archived.confidence == 0.0

        # Confirm twice to bring confidence back to 0.2 (>= threshold)
        store.confirm(inst.id)  # -> 0.1, still below 0.2
        still_archived = store.get_all()[0]
        assert still_archived.archived is True  # not yet unarchived

        store.confirm(inst.id)  # -> 0.2, at threshold -> should unarchive
        unarchived = store.get_all()[0]
        assert unarchived.archived is False
        assert abs(unarchived.confidence - 0.2) < 1e-9

    def test_add_duplicate_of_archived_unarchives_it(self, tmp_path):
        """add() with duplicate of an archived instinct un-archives it via confirm()."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        # Create and archive an instinct
        inst = store.add(
            "always run tests before submitting code for review",
            CATEGORY_ANTIPATTERNS,
            0.2,
        )
        store.contradict(inst.id)  # -> 0.1, archived

        archived = store.get_all()[0]
        assert archived.archived is True

        # Re-observe the same text via add() - should confirm and unarchive
        result = store.add(
            "always run tests before submitting code for review", CATEGORY_ANTIPATTERNS
        )
        assert result.id == inst.id
        assert result.archived is False
        assert result.confidence >= 0.2

        # Should also be visible in get_active()
        active = store.get_active()
        assert len(active) == 1
        assert active[0].id == inst.id


# ---------------------------------------------------------------------------
# InstinctStore.prune() tests
# ---------------------------------------------------------------------------


class TestInstinctStorePrune:
    def test_prune_archives_below_02(self, tmp_path):
        """prune() archives instincts with confidence < 0.2."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("low confidence prunable pitfall", CATEGORY_ANTIPATTERNS, 0.1)
        newly_archived = store.prune()

        assert len(newly_archived) == 1
        assert newly_archived[0].id == inst.id
        updated = store.get_all()[0]
        assert updated.archived is True

    def test_prune_returns_only_newly_archived(self, tmp_path):
        """prune() returns only instincts that were just archived (not already archived)."""
        from golem.instinct_store import Instinct, InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        # Add one already-archived
        inst_archived = Instinct(
            id="already-archived",
            text="already archived pitfall",
            category=CATEGORY_ANTIPATTERNS,
            confidence=0.1,
            created_at="2026-01-01",
            last_confirmed="2026-01-01",
            archived=True,
        )
        # Add one new low-confidence
        inst_low = store.add("newly prunable pitfall entry", CATEGORY_COVERAGE, 0.1)
        # Manually save the already-archived one
        all_instincts = store._load()
        all_instincts.append(inst_archived)
        store._save(all_instincts)

        newly_archived = store.prune()
        # Only the newly archived one should be in the return list
        assert len(newly_archived) == 1
        assert newly_archived[0].id == inst_low.id

    def test_prune_does_not_archive_at_02(self, tmp_path):
        """prune() does not archive instincts with confidence >= 0.2."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        store.add("threshold pitfall entry test", CATEGORY_ANTIPATTERNS, 0.2)
        newly_archived = store.prune()
        assert newly_archived == []

    def test_prune_empty_store(self, tmp_path):
        """prune() on empty store returns empty list."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        result = store.prune()
        assert result == []


# ---------------------------------------------------------------------------
# InstinctStore.get_active() and get_all() tests
# ---------------------------------------------------------------------------


class TestInstinctStoreGetters:
    def test_get_active_excludes_archived(self, tmp_path):
        """get_active() excludes archived instincts."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        active = store.add("coverage gap in verification suite", CATEGORY_COVERAGE, 0.5)
        low = store.add(
            "architecture decouple microservices boundary", CATEGORY_ANTIPATTERNS, 0.2
        )
        store.contradict(low.id)  # -> 0.1 -> archived

        active_list = store.get_active()
        assert len(active_list) == 1
        assert active_list[0].id == active.id

    def test_get_active_empty_store(self, tmp_path):
        """get_active() on empty store returns empty list."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        assert store.get_active() == []

    def test_get_all_includes_archived(self, tmp_path):
        """get_all() includes both active and archived instincts."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        store.add("coverage gap in verification suite", CATEGORY_COVERAGE, 0.5)
        low = store.add(
            "architecture decouple microservices boundary", CATEGORY_ANTIPATTERNS, 0.2
        )
        store.contradict(low.id)  # archives it

        all_instincts = store.get_all()
        assert len(all_instincts) == 2

    def test_get_all_empty_store(self, tmp_path):
        """get_all() on empty store returns empty list."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        assert store.get_all() == []


# ---------------------------------------------------------------------------
# JSON persistence (save/load round-trip) tests
# ---------------------------------------------------------------------------


class TestInstinctStorePersistence:
    def test_round_trip_all_fields(self, tmp_path):
        """_save/_load round-trips all Instinct fields correctly."""
        from golem.instinct_store import Instinct, InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = Instinct(
            id="round-trip-id",
            text="round trip test pitfall",
            category=CATEGORY_ARCHITECTURE,
            confidence=0.75,
            created_at="2026-01-15",
            last_confirmed="2026-02-20",
            confirmation_count=4,
            contradiction_count=2,
            archived=False,
        )
        store._save([inst])
        loaded = store._load()

        assert len(loaded) == 1
        loaded_inst = loaded[0]
        assert loaded_inst.id == "round-trip-id"
        assert loaded_inst.text == "round trip test pitfall"
        assert loaded_inst.category == CATEGORY_ARCHITECTURE
        assert loaded_inst.confidence == 0.75
        assert loaded_inst.created_at == "2026-01-15"
        assert loaded_inst.last_confirmed == "2026-02-20"
        assert loaded_inst.confirmation_count == 4
        assert loaded_inst.contradiction_count == 2
        assert loaded_inst.archived is False

    def test_load_empty_file_returns_empty_list(self, tmp_path):
        """_load on missing file returns empty list."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "nonexistent.json")
        assert store._load() == []

    def test_persistence_across_instances(self, tmp_path):
        """Data persists when a new InstinctStore is created at the same path."""
        from golem.instinct_store import InstinctStore

        path = tmp_path / "shared.json"
        store1 = InstinctStore(path)
        inst = store1.add("persistent across instances pitfall", CATEGORY_COVERAGE)

        store2 = InstinctStore(path)
        loaded = store2.get_all()
        assert len(loaded) == 1
        assert loaded[0].id == inst.id
        assert loaded[0].text == "persistent across instances pitfall"

    def test_atomic_write_via_temp_and_replace(self, tmp_path):
        """_save uses atomic write (temp file + os.replace) not direct write."""
        from golem.instinct_store import Instinct, InstinctStore

        store = InstinctStore(tmp_path / "atomic.json")
        inst = Instinct(
            id="atomic-id",
            text="atomic write test pitfall",
            category=CATEGORY_COVERAGE,
            confidence=0.5,
            created_at="2026-01-01",
            last_confirmed="2026-01-01",
        )
        with patch("os.replace") as mock_replace:
            store._save([inst])
            mock_replace.assert_called_once()

    def test_atomic_write_cleanup_on_failure(self, tmp_path):
        """_save cleans up temp file if write fails."""
        from golem.instinct_store import Instinct, InstinctStore

        store = InstinctStore(tmp_path / "cleanup.json")
        inst = Instinct(
            id="cleanup-id",
            text="cleanup test pitfall atomic",
            category=CATEGORY_COVERAGE,
            confidence=0.5,
            created_at="2026-01-01",
            last_confirmed="2026-01-01",
        )

        with patch("os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                store._save([inst])

        # No temp files left in directory
        remaining = list(tmp_path.iterdir())
        assert all(".instinct_store_" not in f.name for f in remaining)

    def test_atomic_write_cleanup_unlink_also_fails(self, tmp_path):
        """_save propagates replace error when unlink also fails."""
        from golem.instinct_store import Instinct, InstinctStore

        store = InstinctStore(tmp_path / "unlink_fail.json")
        inst = Instinct(
            id="unlink-fail-id",
            text="unlink fail test pitfall",
            category=CATEGORY_COVERAGE,
            confidence=0.5,
            created_at="2026-01-01",
            last_confirmed="2026-01-01",
        )

        with (
            patch("os.replace", side_effect=OSError("disk full")),
            patch("os.unlink", side_effect=OSError("unlink failed")),
        ):
            with pytest.raises(OSError, match="disk full"):
                store._save([inst])


# ---------------------------------------------------------------------------
# generate_agents_md() tests
# ---------------------------------------------------------------------------


class TestGenerateAgentsMd:
    def test_empty_store_with_preamble(self, tmp_path):
        """generate_agents_md() on empty store returns preamble + header."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        content = store.generate_agents_md("custom preamble\n")
        assert "custom preamble" in content

    def test_empty_store_with_empty_preamble_uses_header(self, tmp_path):
        """generate_agents_md() with empty preamble uses _HEADER."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        content = store.generate_agents_md("")
        assert _HEADER in content or "AGENTS.md" in content

    def test_active_instincts_appear_in_output(self, tmp_path):
        """generate_agents_md() includes active instincts."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        store.add("avoid dead code in production systems", CATEGORY_ANTIPATTERNS, 0.5)
        content = store.generate_agents_md("")
        assert "avoid dead code in production systems" in content

    def test_archived_instincts_excluded(self, tmp_path):
        """generate_agents_md() excludes archived instincts."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        inst = store.add("archived pitfall excluded text", CATEGORY_COVERAGE, 0.2)
        store.contradict(inst.id)  # -> 0.1 -> archived

        content = store.generate_agents_md("")
        assert "archived pitfall excluded text" not in content

    def test_groups_by_category(self, tmp_path):
        """generate_agents_md() groups instincts under correct section headers."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        store.add("avoid dead code bad practice", CATEGORY_ANTIPATTERNS, 0.5)
        store.add("coverage gaps hurt quality checks", CATEGORY_COVERAGE, 0.5)
        store.add("decouple components via interfaces", CATEGORY_ARCHITECTURE, 0.5)

        content = store.generate_agents_md("")
        # Each category header should appear
        assert "## Recurring Antipatterns" in content
        assert "## Coverage & Verification Gaps" in content
        assert "## Architecture Notes" in content

    def test_sorted_by_confidence_descending(self, tmp_path):
        """Within a category, instincts are sorted by confidence descending."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        store.add("low confidence antipattern dead code", CATEGORY_ANTIPATTERNS, 0.4)
        store.add(
            "high confidence antipattern empty exception", CATEGORY_ANTIPATTERNS, 0.8
        )
        store.add("medium confidence antipattern coupling", CATEGORY_ANTIPATTERNS, 0.6)

        content = store.generate_agents_md("")

        # Find positions in content
        pos_high = content.index("high confidence antipattern")
        pos_medium = content.index("medium confidence antipattern")
        pos_low = content.index("low confidence antipattern")
        assert pos_high < pos_medium < pos_low

    def test_strong_marker_above_08(self, tmp_path):
        """Instincts with confidence > 0.8 get ' [strong]' appended."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        # Add, then confirm to raise above 0.8
        inst = store.add(
            "strong signal pitfall empty exception pattern", CATEGORY_ANTIPATTERNS, 0.9
        )
        store.confirm(inst.id)  # 0.9 -> 0.95 (> 0.8)

        content = store.generate_agents_md("")
        assert "strong signal pitfall empty exception pattern [strong]" in content

    def test_no_strong_marker_at_08(self, tmp_path):
        """Instincts with confidence exactly 0.8 do NOT get ' [strong]'."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        # Set confidence to exactly 0.8 (not > 0.8)
        inst = store.add(
            "exactly boundary antipattern dead code here", CATEGORY_ANTIPATTERNS, 0.7
        )
        store.confirm(inst.id)  # -> 0.8

        content = store.generate_agents_md("")
        assert "exactly boundary antipattern dead code here [strong]" not in content
        assert "exactly boundary antipattern dead code here" in content

    def test_no_strong_marker_below_08(self, tmp_path):
        """Instincts with confidence < 0.8 do NOT get ' [strong]'."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        store.add("below boundary pitfall coverage gap", CATEGORY_COVERAGE, 0.5)

        content = store.generate_agents_md("")
        assert "[strong]" not in content

    def test_empty_category_not_in_output(self, tmp_path):
        """Categories with no active instincts are omitted from output."""
        from golem.instinct_store import InstinctStore

        store = InstinctStore(tmp_path / "store.json")
        store.add("only antipattern dead code listed", CATEGORY_ANTIPATTERNS, 0.5)

        content = store.generate_agents_md("")
        assert "## Recurring Antipatterns" in content
        assert "## Coverage & Verification Gaps" not in content
        assert "## Architecture Notes" not in content


# ---------------------------------------------------------------------------
# migrate_from_agents_md() tests
# ---------------------------------------------------------------------------


class TestMigrateFromAgentsMd:
    def _make_agents_md(self, entries_by_header: dict) -> str:
        """Helper to build a minimal AGENTS.md content for testing."""
        lines = [_HEADER, _AUTO_COMMENT, "\n"]
        for header, entries in entries_by_header.items():
            lines.append(header)
            for entry in entries:
                lines.append(f"- {entry}\n")
            lines.append("\n")
        return "".join(lines)

    @pytest.mark.parametrize(
        "seen,expected_confidence",
        [
            (1, 0.3),
            (2, 0.4),
            (3, 0.5),
            (4, 0.55),
            (5, 0.6),
            (6, 0.65),
            (7, 0.7),
            (8, 0.75),
            (9, 0.8),  # seen=9 -> 0.5 + 0.05*min(6,6) = 0.5+0.3 = 0.8
            (10, 0.8),  # capped at min(seen-3, 6) = 6
        ],
    )
    def test_seen_count_to_confidence_mapping(
        self, tmp_path, seen, expected_confidence
    ):
        """migrate_from_agents_md maps seen counts to correct initial confidence."""
        from golem.instinct_store import InstinctStore

        agents_md_path = tmp_path / "AGENTS.md"
        entry = f"dead code antipattern seen many times <!-- seen:{seen} last:2026-01-01 -->"
        agents_md_path.write_text(
            self._make_agents_md({"## Recurring Antipatterns\n": [entry]})
        )

        store = InstinctStore(tmp_path / "store.json")
        count = store.migrate_from_agents_md(agents_md_path)

        assert count == 1
        instincts = store.get_all()
        assert len(instincts) == 1
        assert abs(instincts[0].confidence - expected_confidence) < 1e-9

    def test_migrate_bare_entry_no_metadata(self, tmp_path):
        """migrate_from_agents_md handles bare entries without metadata (seen=1)."""
        from golem.instinct_store import InstinctStore

        agents_md_path = tmp_path / "AGENTS.md"
        entry = "bare entry antipattern no metadata here"
        agents_md_path.write_text(
            self._make_agents_md({"## Recurring Antipatterns\n": [entry]})
        )

        store = InstinctStore(tmp_path / "store.json")
        count = store.migrate_from_agents_md(agents_md_path)

        assert count == 1
        instincts = store.get_all()
        assert abs(instincts[0].confidence - 0.3) < 1e-9

    def test_migrate_multiple_categories(self, tmp_path):
        """migrate_from_agents_md imports entries from all categories."""
        from golem.instinct_store import InstinctStore

        agents_md_path = tmp_path / "AGENTS.md"
        content = self._make_agents_md(
            {
                "## Recurring Antipatterns\n": [
                    "antipattern dead code entry <!-- seen:2 last:2026-01-01 -->"
                ],
                "## Coverage & Verification Gaps\n": [
                    "coverage gap in test suite <!-- seen:3 last:2026-02-01 -->"
                ],
                "## Architecture Notes\n": [
                    "architecture note decoupling <!-- seen:1 last:2026-03-01 -->"
                ],
            }
        )
        agents_md_path.write_text(content)

        store = InstinctStore(tmp_path / "store.json")
        count = store.migrate_from_agents_md(agents_md_path)

        assert count == 3
        categories = {i.category for i in store.get_all()}
        assert CATEGORY_ANTIPATTERNS in categories
        assert CATEGORY_COVERAGE in categories
        assert CATEGORY_ARCHITECTURE in categories

    def test_migrate_empty_agents_md(self, tmp_path):
        """migrate_from_agents_md on empty/no-section AGENTS.md returns 0."""
        from golem.instinct_store import InstinctStore

        agents_md_path = tmp_path / "AGENTS.md"
        agents_md_path.write_text(_HEADER + _AUTO_COMMENT)

        store = InstinctStore(tmp_path / "store.json")
        count = store.migrate_from_agents_md(agents_md_path)

        assert count == 0
        assert store.get_all() == []

    def test_migrate_returns_count(self, tmp_path):
        """migrate_from_agents_md returns the count of imported instincts."""
        from golem.instinct_store import InstinctStore

        agents_md_path = tmp_path / "AGENTS.md"
        content = self._make_agents_md(
            {
                "## Recurring Antipatterns\n": [
                    "first antipattern dead code <!-- seen:1 last:2026-01-01 -->",
                    "second antipattern empty exception <!-- seen:2 last:2026-01-01 -->",
                ]
            }
        )
        agents_md_path.write_text(content)

        store = InstinctStore(tmp_path / "store.json")
        count = store.migrate_from_agents_md(agents_md_path)

        assert count == 2

    def test_migrate_skips_empty_text_entries(self, tmp_path):
        """migrate_from_agents_md skips entries that become empty after stripping metadata."""
        from golem.instinct_store import InstinctStore

        agents_md_path = tmp_path / "AGENTS.md"
        # Entry with only metadata and whitespace — text becomes empty after strip
        agents_md_path.write_text(
            self._make_agents_md(
                {
                    "## Recurring Antipatterns\n": [
                        "<!-- seen:2 last:2026-01-01 -->",
                        "real antipattern dead code entry <!-- seen:1 last:2026-01-01 -->",
                    ]
                }
            )
        )

        store = InstinctStore(tmp_path / "store.json")
        count = store.migrate_from_agents_md(agents_md_path)

        assert count == 1
        assert len(store.get_all()) == 1

    def test_migrate_strips_metadata_from_text(self, tmp_path):
        """migrate_from_agents_md stores instinct text without metadata tags."""
        from golem.instinct_store import InstinctStore

        agents_md_path = tmp_path / "AGENTS.md"
        entry = "clean text without tag antipattern <!-- seen:3 last:2026-01-01 -->"
        agents_md_path.write_text(
            self._make_agents_md({"## Recurring Antipatterns\n": [entry]})
        )

        store = InstinctStore(tmp_path / "store.json")
        store.migrate_from_agents_md(agents_md_path)

        instinct = store.get_all()[0]
        assert "<!-- seen:" not in instinct.text
        assert instinct.text == "clean text without tag antipattern"


# ---------------------------------------------------------------------------
# update_agents_md_from_instincts() integration tests
# ---------------------------------------------------------------------------


class TestUpdateAgentsMdFromInstincts:
    def test_creates_agents_md_from_store(self, tmp_path):
        """update_agents_md_from_instincts() writes AGENTS.md from store content."""
        from golem.instinct_store import InstinctStore
        from golem.pitfall_writer import update_agents_md_from_instincts

        path = tmp_path / "AGENTS.md"
        store = InstinctStore(tmp_path / "store.json")
        store.add("dead code antipattern bad practice", CATEGORY_ANTIPATTERNS, 0.5)

        update_agents_md_from_instincts(store, path)

        content = path.read_text()
        assert "dead code antipattern bad practice" in content

    def test_preserves_existing_preamble(self, tmp_path):
        """update_agents_md_from_instincts() preserves preamble from existing AGENTS.md."""
        from golem.instinct_store import InstinctStore
        from golem.pitfall_writer import update_agents_md_from_instincts

        path = tmp_path / "AGENTS.md"
        path.write_text(
            "# Custom Header\nThis is a preamble.\n\n## Recurring Antipatterns\n"
        )

        store = InstinctStore(tmp_path / "store.json")
        store.add("dead code antipattern example bad", CATEGORY_ANTIPATTERNS, 0.5)

        update_agents_md_from_instincts(store, path)

        content = path.read_text()
        assert "Custom Header" in content
        assert "This is a preamble." in content

    def test_uses_default_path_when_none(self, tmp_path):
        """update_agents_md_from_instincts() uses default path when none provided."""
        from golem.instinct_store import InstinctStore
        from golem.pitfall_writer import update_agents_md_from_instincts

        store = InstinctStore(tmp_path / "store.json")
        store.add("dead code antipattern test default path", CATEGORY_ANTIPATTERNS, 0.5)

        # Patch the default path
        mock_path = tmp_path / "default_AGENTS.md"
        with patch("golem.pitfall_writer.PROJECT_ROOT") as mock_root:
            mock_root.parent = tmp_path
            update_agents_md_from_instincts(store, None)
            # The file should be created
            assert (tmp_path / "AGENTS.md").exists()

    def test_atomic_write_in_update_from_instincts(self, tmp_path):
        """update_agents_md_from_instincts() uses atomic write."""
        from golem.instinct_store import InstinctStore
        from golem.pitfall_writer import update_agents_md_from_instincts

        path = tmp_path / "AGENTS.md"
        store = InstinctStore(tmp_path / "store.json")
        store.add("antipattern dead code write test", CATEGORY_ANTIPATTERNS, 0.5)

        with patch("os.replace") as mock_replace:
            update_agents_md_from_instincts(store, path)
            mock_replace.assert_called_once()

    def test_atomic_write_cleanup_on_replace_failure(self, tmp_path):
        """update_agents_md_from_instincts cleans up temp on replace error."""
        from golem.instinct_store import InstinctStore
        from golem.pitfall_writer import update_agents_md_from_instincts

        path = tmp_path / "AGENTS.md"
        store = InstinctStore(tmp_path / "store.json")
        store.add("antipattern replace fail cleanup", CATEGORY_ANTIPATTERNS, 0.5)

        with patch(
            "golem.pitfall_writer.os.replace", side_effect=OSError("replace failed")
        ):
            with pytest.raises(OSError, match="replace failed"):
                update_agents_md_from_instincts(store, path)

        # Temp files cleaned up
        temp_files = list(tmp_path.glob(".agents_md_*"))
        assert temp_files == []

    def test_atomic_write_cleanup_unlink_also_fails(self, tmp_path):
        """update_agents_md_from_instincts propagates error when unlink also fails."""
        from golem.instinct_store import InstinctStore
        from golem.pitfall_writer import update_agents_md_from_instincts

        path = tmp_path / "AGENTS.md"
        store = InstinctStore(tmp_path / "store.json")
        store.add("antipattern unlink fail test entry", CATEGORY_ANTIPATTERNS, 0.5)

        with (
            patch(
                "golem.pitfall_writer.os.replace",
                side_effect=OSError("replace failed"),
            ),
            patch(
                "golem.pitfall_writer.os.unlink",
                side_effect=OSError("unlink failed"),
            ),
        ):
            with pytest.raises(OSError, match="replace failed"):
                update_agents_md_from_instincts(store, path)


# ---------------------------------------------------------------------------
# Orchestrator integration tests
# ---------------------------------------------------------------------------


class TestOrchestratorInstinctIntegration:
    def _make_orch(self, session=None):
        from unittest.mock import MagicMock

        from golem.orchestrator import TaskOrchestrator, TaskSession

        session = session or TaskSession(parent_issue_id=42, parent_subject="Fix")
        task_config = MagicMock()
        task_config.supervisor_mode = False
        task_config.use_worktrees = False
        task_config.task_model = "sonnet"
        task_config.task_timeout_seconds = 300
        task_config.validation_model = "opus"
        task_config.validation_budget_usd = 0.5
        task_config.validation_timeout_seconds = 120
        task_config.max_retries = 1
        task_config.auto_commit = True
        task_config.retry_budget_usd = 5.0
        task_config.preflight_verify = False
        return TaskOrchestrator(session, MagicMock(), task_config, profile=MagicMock())

    def test_instinct_store_initialized_in_init(self):
        """TaskOrchestrator.__init__ creates an InstinctStore instance."""
        from golem.instinct_store import InstinctStore

        orch = self._make_orch()
        assert isinstance(orch._instinct_store, InstinctStore)

    @patch("golem.orchestrator.update_agents_md_from_instincts")
    @patch("golem.orchestrator.extract_pitfalls")
    def test_extract_and_write_pitfalls_uses_instinct_store(
        self, mock_extract, mock_update_from_instincts
    ):
        """_extract_and_write_pitfalls adds pitfalls to instinct store."""
        from golem.orchestrator import TaskSession

        mock_extract.return_value = ["avoid dead code bad antipattern patterns"]
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        orch = self._make_orch(session)

        with (patch.object(orch._signal_accumulator, "get_promoted", return_value=[]),):
            orch._extract_and_write_pitfalls()

        mock_update_from_instincts.assert_called_once()
        # Instinct store should have the pitfall
        instincts = orch._instinct_store.get_all()
        assert len(instincts) == 1
        assert "dead code" in instincts[0].text

    @patch("golem.orchestrator.update_agents_md_from_instincts")
    @patch("golem.orchestrator.extract_pitfalls")
    def test_extract_and_write_pitfalls_promoted_signals_added_to_store(
        self, mock_extract, mock_update_from_instincts
    ):
        """_extract_and_write_pitfalls adds promoted signals to instinct store."""
        from golem.orchestrator import TaskSession

        mock_extract.return_value = []
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        orch = self._make_orch(session)
        promoted = ["coverage gap test failures missing checks"]

        with (
            patch.object(
                orch._signal_accumulator, "get_promoted", return_value=promoted
            ),
            patch.object(orch._signal_accumulator, "clear_promoted"),
        ):
            orch._extract_and_write_pitfalls()

        mock_update_from_instincts.assert_called_once()
        instincts = orch._instinct_store.get_all()
        assert len(instincts) == 1

    @patch("golem.orchestrator.update_agents_md_from_instincts")
    @patch("golem.orchestrator.extract_pitfalls")
    def test_extract_and_write_pitfalls_no_pitfalls_still_prunes(
        self, mock_extract, mock_update_from_instincts
    ):
        """_extract_and_write_pitfalls calls prune() even with no new pitfalls."""
        from golem.orchestrator import TaskSession

        mock_extract.return_value = []
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        orch = self._make_orch(session)

        with patch.object(orch._instinct_store, "prune") as mock_prune:
            with patch.object(
                orch._signal_accumulator, "get_promoted", return_value=[]
            ):
                orch._extract_and_write_pitfalls()
        mock_prune.assert_called_once()

    @patch("golem.orchestrator.update_agents_md_from_instincts")
    @patch("golem.orchestrator.extract_pitfalls")
    def test_extract_and_write_pitfalls_calls_update_from_instincts(
        self, mock_extract, mock_update_from_instincts
    ):
        """_extract_and_write_pitfalls calls update_agents_md_from_instincts."""
        from golem.orchestrator import TaskSession

        mock_extract.return_value = ["dead code unused variable antipattern"]
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        orch = self._make_orch(session)

        with patch.object(orch._signal_accumulator, "get_promoted", return_value=[]):
            orch._extract_and_write_pitfalls()

        mock_update_from_instincts.assert_called_once_with(orch._instinct_store)
