# pylint: disable=too-few-public-methods
"""Tests for golem.errors — error taxonomy and hierarchy."""

import pytest

from golem.errors import (
    GolemError,
    InfrastructureError,
    TaskExecutionError,
    ValidationError,
)


class TestGolemError:
    def test_is_exception(self):
        assert issubclass(GolemError, Exception)

    def test_retryable_default(self):
        assert GolemError.retryable is False

    def test_raise_and_catch(self):
        with pytest.raises(GolemError, match="something broke"):
            raise GolemError("something broke")

    def test_str(self):
        assert str(GolemError("msg")) == "msg"


class TestInfrastructureError:
    def test_is_golem_error(self):
        assert issubclass(InfrastructureError, GolemError)
        assert issubclass(InfrastructureError, Exception)

    def test_retryable(self):
        assert InfrastructureError.retryable is True

    def test_raise_and_catch_as_golem_error(self):
        with pytest.raises(GolemError):
            raise InfrastructureError("worktree failed")

    def test_str(self):
        assert str(InfrastructureError("worktree failed")) == "worktree failed"


class TestTaskExecutionError:
    def test_is_golem_error(self):
        assert issubclass(TaskExecutionError, GolemError)
        assert issubclass(TaskExecutionError, Exception)

    def test_not_retryable(self):
        assert TaskExecutionError.retryable is False

    def test_raise_and_catch(self):
        with pytest.raises(TaskExecutionError, match="agent failed"):
            raise TaskExecutionError("agent failed")

    def test_str(self):
        assert str(TaskExecutionError("agent failed")) == "agent failed"


class TestValidationError:
    def test_is_golem_error(self):
        assert issubclass(ValidationError, GolemError)
        assert issubclass(ValidationError, Exception)

    def test_retryable(self):
        assert ValidationError.retryable is True

    def test_raise_and_catch(self):
        with pytest.raises(ValidationError, match="no verdict"):
            raise ValidationError("no verdict")

    def test_str(self):
        assert str(ValidationError("no verdict")) == "no verdict"
