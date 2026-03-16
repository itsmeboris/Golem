"""Tests for task clarity pre-check."""

import json

from unittest.mock import MagicMock, patch

import pytest

from golem.core.config import GolemFlowConfig


class TestClarityConfig:
    def test_clarity_check_disabled_by_default(self):
        """clarity_check defaults to False (opt-in feature)."""
        cfg = GolemFlowConfig()
        assert cfg.clarity_check is False

    def test_clarity_threshold_default(self):
        """clarity_threshold defaults to 3 (out of 5)."""
        cfg = GolemFlowConfig()
        assert cfg.clarity_threshold == 3


class TestClarityScore:
    def test_score_clear_task_returns_high(self):
        """A clear, specific task description scores above threshold."""
        from golem.clarity import ClarityResult

        result = ClarityResult(score=5, reason="Clear and specific")
        assert result.score >= 3
        assert result.is_clear(threshold=3)

    def test_score_vague_task_returns_low(self):
        """A vague task description scores below threshold."""
        from golem.clarity import ClarityResult

        result = ClarityResult(score=1, reason="Too vague")
        assert not result.is_clear(threshold=3)

    def test_check_clarity_invokes_cli(self):
        """check_clarity calls invoke_cli with haiku model."""
        from golem.clarity import check_clarity

        mock_result = MagicMock()
        mock_result.output = {"result": '{"score": 4, "reason": "Clear enough"}'}
        mock_result.cost_usd = 0.005

        with patch("golem.clarity.invoke_cli", return_value=mock_result) as mock_cli:
            result = check_clarity(
                subject="Fix the login bug on /auth endpoint",
                description="Users get 500 error when logging in with SSO. "
                "Reproduce: POST /auth/sso with valid SAML token.",
            )
            assert result.score == 4
            assert result.cost_usd < 0.02
            mock_cli.assert_called_once()
            # Verify haiku model is used
            cli_config = mock_cli.call_args[0][1]
            assert "haiku" in cli_config.model

    def test_check_clarity_fallback_on_error(self):
        """If the clarity check fails, return a passing score (don't block)."""
        from golem.clarity import check_clarity

        with patch("golem.clarity.invoke_cli", side_effect=RuntimeError("timeout")):
            result = check_clarity(subject="Fix bug", description="Something broke")
            assert result.score >= 3  # Fail-open: don't block on infra error
            assert result.cost_usd == 0.0


class TestClarityScoreClamping:
    """Verify score clamping (max(1, min(5, score))) and fallback defaults."""

    @pytest.mark.parametrize(
        "raw_score, expected",
        [
            (0, 1),  # below min, clamp to 1
            (1, 1),  # at min
            (3, 3),  # normal
            (5, 5),  # at max
            (10, 5),  # above max, clamp to 5
            (-1, 1),  # negative, clamp to 1
        ],
    )
    def test_score_clamping(self, raw_score, expected):
        from golem.clarity import check_clarity

        mock_result = MagicMock()
        mock_result.output = {
            "result": json.dumps({"score": raw_score, "reason": "test"})
        }
        mock_result.cost_usd = 0.001

        with patch("golem.clarity.invoke_cli", return_value=mock_result):
            result = check_clarity(subject="Test", description="test")
            assert result.score == expected

    def test_missing_score_defaults_to_5(self):
        """When extract_json returns no 'score' key, default to 5."""
        from golem.clarity import check_clarity

        mock_result = MagicMock()
        mock_result.output = {"result": json.dumps({"reason": "no score key here"})}
        mock_result.cost_usd = 0.001

        with patch("golem.clarity.invoke_cli", return_value=mock_result):
            result = check_clarity(subject="Test", description="test")
            assert result.score == 5
