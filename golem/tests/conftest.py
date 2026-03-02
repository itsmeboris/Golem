"""Shared pytest fixtures for the golem standalone test suite."""

# pylint: disable=missing-function-docstring

import pytest

from golem.core.live_state import LiveState


@pytest.fixture(autouse=True)
def _reset_live_state():
    LiveState.reset()
    yield
    LiveState.reset()


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    """Redirect all data paths to a temp directory so tests don't touch real state."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setattr("golem.core.config.DATA_DIR", data_dir)
    monkeypatch.setattr("golem.core.run_log.DATA_DIR", data_dir)
    monkeypatch.setattr(
        "golem.core.run_log.DEFAULT_RUN_LOG",
        data_dir / "runs" / "runs.jsonl",
    )
    monkeypatch.setattr("golem.core.flow_base.DATA_DIR", data_dir)
    monkeypatch.setattr("golem.core.flow_base.TRACES_DIR", data_dir / "traces")

    # Also patch the modules that import DATA_DIR at module level
    monkeypatch.setattr("golem.orchestrator.DATA_DIR", data_dir)
    monkeypatch.setattr("golem.flow.DATA_DIR", data_dir)
    monkeypatch.setattr("golem.flow.SUBMISSIONS_DIR", data_dir / "submissions")
    monkeypatch.setattr("golem.cli.DATA_DIR", data_dir)


@pytest.fixture
def temp_config_file(tmp_path):
    return tmp_path / "config.yaml"


@pytest.fixture
def sample_config_content():
    return """
flows:
  golem:
    enabled: true
    projects:
      - test-project
    task_model: sonnet
    profile: redmine

claude:
  cli_type: agent
  model: sonnet
  timeout_seconds: 600

dashboard:
  port: 8082
"""


@pytest.fixture
def mock_env(monkeypatch):
    def _mock_env(**kwargs):
        for key, value in kwargs.items():
            monkeypatch.setenv(key, value)

    return _mock_env
