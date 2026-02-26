"""Shared pytest fixtures for the task-agent standalone test suite."""

# pylint: disable=missing-function-docstring

import pytest

from task_agent.core.live_state import LiveState


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

    monkeypatch.setattr("task_agent.core.config.DATA_DIR", data_dir)
    monkeypatch.setattr("task_agent.core.run_log.DATA_DIR", data_dir)
    monkeypatch.setattr(
        "task_agent.core.run_log.DEFAULT_RUN_LOG",
        data_dir / "runs" / "runs.jsonl",
    )
    monkeypatch.setattr("task_agent.core.flow_base.DATA_DIR", data_dir)
    monkeypatch.setattr(
        "task_agent.core.flow_base.TRACES_DIR", data_dir / "traces"
    )

    # Also patch the modules that import DATA_DIR at module level
    monkeypatch.setattr("task_agent.orchestrator.DATA_DIR", data_dir)
    monkeypatch.setattr("task_agent.flow.DATA_DIR", data_dir)
    monkeypatch.setattr("task_agent.cli.DATA_DIR", data_dir)


@pytest.fixture
def temp_config_file(tmp_path):
    return tmp_path / "config.yaml"


@pytest.fixture
def sample_config_content():
    return """
flows:
  task_agent:
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
