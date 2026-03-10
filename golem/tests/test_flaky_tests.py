"""Tests for flaky test detection and management."""

import json
from pathlib import Path

from golem.flaky_tests import FlakyTestRegistry, is_flaky


class TestFlakyTestRegistry:
    def test_load_empty_registry(self):
        """Empty or missing file returns empty registry."""
        reg = FlakyTestRegistry(Path("/nonexistent/path.json"))
        assert reg.known_flaky == set()

    def test_load_registry_from_file(self, tmp_path):
        """Registry loads known-flaky test names from JSON file."""
        flaky_file = tmp_path / "flaky.json"
        flaky_file.write_text(
            json.dumps(
                {
                    "known_flaky": [
                        "test_network_timeout",
                        "test_nfs_stale_handle",
                    ]
                }
            )
        )
        reg = FlakyTestRegistry(flaky_file)
        assert "test_network_timeout" in reg.known_flaky
        assert "test_nfs_stale_handle" in reg.known_flaky

    def test_is_flaky_match(self, tmp_path):
        """is_flaky returns True for tests in the known-flaky set."""
        flaky_file = tmp_path / "flaky.json"
        flaky_file.write_text(json.dumps({"known_flaky": ["test_nfs_timeout"]}))
        reg = FlakyTestRegistry(flaky_file)
        assert reg.is_flaky("test_nfs_timeout") is True

    def test_is_flaky_no_match(self, tmp_path):
        """is_flaky returns False for tests NOT in the known-flaky set."""
        flaky_file = tmp_path / "flaky.json"
        flaky_file.write_text(json.dumps({"known_flaky": ["test_nfs_timeout"]}))
        reg = FlakyTestRegistry(flaky_file)
        assert reg.is_flaky("test_real_bug") is False

    def test_record_flaky_appends(self, tmp_path):
        """record_flaky adds new entries and persists to disk."""
        flaky_file = tmp_path / "flaky.json"
        flaky_file.write_text(json.dumps({"known_flaky": []}))
        reg = FlakyTestRegistry(flaky_file)
        reg.record_flaky("test_new_flaky", reason="passed on retry")
        assert "test_new_flaky" in reg.known_flaky
        # Verify persistence
        data = json.loads(flaky_file.read_text())
        assert "test_new_flaky" in data["known_flaky"]

    def test_load_corrupt_file_yields_empty(self, tmp_path):
        """Corrupt JSON file logs a warning and leaves registry empty."""
        flaky_file = tmp_path / "flaky.json"
        flaky_file.write_text("not valid json")
        reg = FlakyTestRegistry(flaky_file)
        assert reg.known_flaky == set()

    def test_filter_flaky_splits_correctly(self, tmp_path):
        """filter_flaky separates real failures from known-flaky ones."""
        flaky_file = tmp_path / "flaky.json"
        flaky_file.write_text(json.dumps({"known_flaky": ["test_nfs_timeout"]}))
        reg = FlakyTestRegistry(flaky_file)
        real, flaky = reg.filter_flaky(
            ["test_nfs_timeout", "test_real_bug", "test_logic_error"]
        )
        assert real == ["test_real_bug", "test_logic_error"]
        assert flaky == ["test_nfs_timeout"]


class TestIsFlakyHelper:
    def test_standalone_function(self, tmp_path):
        """Module-level is_flaky function works with a path."""
        flaky_file = tmp_path / "flaky.json"
        flaky_file.write_text(json.dumps({"known_flaky": ["test_timeout"]}))
        assert is_flaky("test_timeout", flaky_file) is True
        assert is_flaky("test_real", flaky_file) is False
