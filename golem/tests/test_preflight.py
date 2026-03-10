"""Tests for pre-flight base branch verification."""

from unittest.mock import MagicMock, patch

from golem.core.config import GolemFlowConfig


class TestPreflightVerification:
    def test_preflight_enabled_by_default(self):
        """GolemFlowConfig.preflight_verify defaults to True."""
        cfg = GolemFlowConfig()
        assert cfg.preflight_verify is True

    def test_preflight_skipped_when_disabled(self):
        """When preflight_verify=False, _preflight_check does NOT call run_verification."""
        from golem.orchestrator import TaskOrchestrator, TaskSession

        session = TaskSession(parent_issue_id=99)
        cfg = GolemFlowConfig(preflight_verify=False)
        orch = TaskOrchestrator(session, MagicMock(), cfg, profile=MagicMock())
        with patch("golem.orchestrator.run_verification") as mock_verify:
            with patch("pathlib.Path.is_dir", return_value=True):
                with patch("pathlib.Path.exists", return_value=True):
                    orch._preflight_check("/tmp/fake")
                    mock_verify.assert_not_called()

    def test_preflight_passes_continues_normally(self):
        """When preflight verification passes, no error is raised."""
        from golem.orchestrator import TaskOrchestrator, TaskSession

        session = TaskSession(parent_issue_id=99)
        cfg = GolemFlowConfig(preflight_verify=True)
        orch = TaskOrchestrator(session, MagicMock(), cfg, profile=MagicMock())

        mock_result = MagicMock(passed=True)
        with patch("golem.orchestrator.run_verification", return_value=mock_result):
            with patch("pathlib.Path.is_dir", return_value=True):
                with patch("pathlib.Path.exists", return_value=True):
                    orch._preflight_check("/tmp/fake")  # Should not raise

    def test_preflight_fails_raises_infrastructure_error(self):
        """When base branch tests fail, raise InfrastructureError."""
        import pytest
        from golem.errors import InfrastructureError
        from golem.orchestrator import TaskOrchestrator, TaskSession

        session = TaskSession(parent_issue_id=99)
        cfg = GolemFlowConfig(preflight_verify=True)
        orch = TaskOrchestrator(session, MagicMock(), cfg, profile=MagicMock())

        mock_result = MagicMock(
            passed=False,
            pytest_ok=False,
            pytest_output="FAILED test_foo.py::test_bar - AssertionError",
            black_ok=False,
            black_output="would reformat foo.py",
            pylint_ok=False,
            pylint_output="E0001: syntax error",
        )
        with patch("golem.orchestrator.run_verification", return_value=mock_result):
            with patch("pathlib.Path.is_dir", return_value=True):
                with patch("pathlib.Path.exists", return_value=True):
                    with pytest.raises(InfrastructureError, match="Base branch"):
                        orch._preflight_check("/tmp/fake")
