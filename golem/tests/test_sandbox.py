"""Tests for golem.sandbox — OS-level subprocess resource limits."""

# pylint: disable=missing-class-docstring,missing-function-docstring

import logging
import resource
from unittest.mock import MagicMock, call, patch

import pytest

from golem.core.cli_wrapper import CLIConfig, _sandbox_preexec
from golem.sandbox import (
    SandboxLimits,
    _apply_rlimit,
    get_default_limits,
    make_sandbox_preexec,
)


class TestSandboxLimits:
    def test_default_limits(self):
        limits = SandboxLimits()
        assert limits.cpu_seconds == 3600
        assert limits.memory_bytes == 4 * 1024**3
        assert limits.file_size_bytes == 1 * 1024**3
        assert limits.max_processes == 256
        assert limits.nofile == 1024

    def test_custom_limits(self):
        limits = SandboxLimits(cpu_seconds=60, memory_bytes=1024)
        assert limits.cpu_seconds == 60
        assert limits.memory_bytes == 1024

    @pytest.mark.parametrize(
        "field,value",
        [
            ("cpu_seconds", 7200),
            ("memory_bytes", 2 * 1024**3),
            ("file_size_bytes", 512 * 1024**2),
            ("max_processes", 128),
            ("nofile", 2048),
        ],
    )
    def test_each_field_overridable(self, field, value):
        limits = SandboxLimits(**{field: value})
        assert getattr(limits, field) == value


class TestGetDefaultLimits:
    def test_returns_sandbox_limits_instance(self):
        limits = get_default_limits()
        assert isinstance(limits, SandboxLimits)

    def test_returns_default_values(self):
        limits = get_default_limits()
        assert limits.cpu_seconds == 3600
        assert limits.memory_bytes == 4 * 1024**3

    def test_returns_independent_copies(self):
        """Each call returns a new SandboxLimits (not the same object)."""
        a = get_default_limits()
        b = get_default_limits()
        assert a is not b


class TestApplyRlimit:
    def test_calls_setrlimit_with_correct_args(self):
        with patch("golem.sandbox.resource.setrlimit") as mock_set:
            _apply_rlimit(resource.RLIMIT_CPU, 100, 100)
        mock_set.assert_called_once_with(resource.RLIMIT_CPU, (100, 100))

    def test_os_error_logs_warning_does_not_raise(self, caplog):
        with caplog.at_level(logging.WARNING, logger="golem.sandbox"):
            with patch(
                "golem.sandbox.resource.setrlimit", side_effect=OSError("eperm")
            ):
                _apply_rlimit(resource.RLIMIT_CPU, 999, 999)
        warn_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "Could not set sandbox limit" in r.message
        ]
        assert len(warn_records) == 1

    def test_value_error_logs_warning_does_not_raise(self, caplog):
        with caplog.at_level(logging.WARNING, logger="golem.sandbox"):
            with patch(
                "golem.sandbox.resource.setrlimit", side_effect=ValueError("bad")
            ):
                _apply_rlimit(resource.RLIMIT_NOFILE, 1, 1)
        warn_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "Could not set sandbox limit" in r.message
        ]
        assert len(warn_records) == 1

    def test_success_emits_no_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="golem.sandbox"):
            with patch("golem.sandbox.resource.setrlimit"):
                _apply_rlimit(resource.RLIMIT_CPU, 100, 100)
        assert not any(
            "Could not set sandbox limit" in r.message for r in caplog.records
        )


class TestMakeSandboxPreexec:
    def test_returns_callable(self):
        fn = make_sandbox_preexec()
        assert callable(fn)

    def test_uses_default_limits_when_none_passed(self):
        fn = make_sandbox_preexec(None)
        assert callable(fn)

    def test_custom_limits_callable(self):
        limits = SandboxLimits(cpu_seconds=60)
        fn = make_sandbox_preexec(limits)
        assert callable(fn)

    def test_preexec_sets_cpu_limit(self):
        """Verify CPU limit is actually applied when preexec runs."""
        limits = SandboxLimits(cpu_seconds=999)
        fn = make_sandbox_preexec(limits)
        fn()
        soft, _hard = resource.getrlimit(resource.RLIMIT_CPU)
        assert soft == 999

    def test_preexec_calls_all_five_limits(self):
        """Verify the preexec_fn attempts to set all 5 resource limits."""
        limits = SandboxLimits(
            cpu_seconds=100,
            memory_bytes=1024,
            file_size_bytes=2048,
            max_processes=32,
            nofile=64,
        )
        fn = make_sandbox_preexec(limits)

        with patch("golem.sandbox.resource.setrlimit") as mock_set:
            fn()

        expected_calls = [
            call(resource.RLIMIT_CPU, (100, 100)),
            call(resource.RLIMIT_AS, (1024, 1024)),
            call(resource.RLIMIT_FSIZE, (2048, 2048)),
            call(resource.RLIMIT_NPROC, (32, 32)),
            call(resource.RLIMIT_NOFILE, (64, 64)),
        ]
        assert mock_set.call_args_list == expected_calls

    def test_preexec_continues_after_one_limit_fails(self):
        """A failing limit must not prevent subsequent limits from being applied."""
        call_count = []
        fail_on_first = [True]

        def _side_effect(res_id, _pair):
            call_count.append(res_id)
            if fail_on_first[0]:
                fail_on_first[0] = False
                raise OSError("first limit fails")

        fn = make_sandbox_preexec()
        with patch("golem.sandbox.resource.setrlimit", side_effect=_side_effect):
            fn()

        # All 5 limits were attempted even though the first one failed
        assert len(call_count) == 5

    def test_preexec_handles_os_error_gracefully(self):
        """When setrlimit raises OSError, function logs debug and does not raise."""
        limits = SandboxLimits(memory_bytes=1)
        fn = make_sandbox_preexec(limits)
        with patch(
            "golem.sandbox.resource.setrlimit", side_effect=OSError("permission denied")
        ):
            fn()

    def test_preexec_handles_value_error_gracefully(self):
        """When setrlimit raises ValueError, function logs debug and does not raise."""
        fn = make_sandbox_preexec()
        with patch(
            "golem.sandbox.resource.setrlimit", side_effect=ValueError("bad value")
        ):
            fn()

    def test_each_call_returns_distinct_closure(self):
        """Two calls to make_sandbox_preexec return distinct callables."""
        fn1 = make_sandbox_preexec()
        fn2 = make_sandbox_preexec()
        assert fn1 is not fn2


class TestCLIConfigSandboxFields:
    """CLIConfig exposes sandbox_cpu_seconds and sandbox_memory_gb fields."""

    def test_default_sandbox_cpu_seconds(self):
        cfg = CLIConfig()
        assert cfg.sandbox_cpu_seconds == 3600

    def test_default_sandbox_memory_gb(self):
        cfg = CLIConfig()
        assert cfg.sandbox_memory_gb == 4

    def test_custom_sandbox_cpu_seconds(self):
        cfg = CLIConfig(sandbox_cpu_seconds=7200)
        assert cfg.sandbox_cpu_seconds == 7200

    def test_custom_sandbox_memory_gb(self):
        cfg = CLIConfig(sandbox_memory_gb=8)
        assert cfg.sandbox_memory_gb == 8

    def test_sandbox_enabled_default_true(self):
        cfg = CLIConfig()
        assert cfg.sandbox_enabled is True


class TestSandboxPreexecHelper:
    """_sandbox_preexec returns None when disabled, callable when enabled."""

    def test_returns_none_when_sandbox_disabled(self):
        cfg = CLIConfig(sandbox_enabled=False)
        assert _sandbox_preexec(cfg) is None

    def test_returns_callable_when_sandbox_enabled(self):
        cfg = CLIConfig(sandbox_enabled=True)
        result = _sandbox_preexec(cfg)
        assert callable(result)

    def test_config_values_flow_to_sandbox_limits(self):
        """Custom cpu_seconds and memory_gb are forwarded to SandboxLimits."""
        cfg = CLIConfig(sandbox_cpu_seconds=7200, sandbox_memory_gb=8)
        with patch("golem.core.cli_wrapper.make_sandbox_preexec") as mock_make:
            mock_make.return_value = lambda: None
            _sandbox_preexec(cfg)
        mock_make.assert_called_once()
        limits_arg = mock_make.call_args[0][0]
        assert isinstance(limits_arg, SandboxLimits)
        assert limits_arg.cpu_seconds == 7200
        assert limits_arg.memory_bytes == 8 * 1024**3

    def test_default_config_uses_default_limits(self):
        """Default CLIConfig values produce limits matching GolemFlowConfig defaults."""
        cfg = CLIConfig()  # sandbox_cpu_seconds=3600, sandbox_memory_gb=4
        with patch("golem.core.cli_wrapper.make_sandbox_preexec") as mock_make:
            mock_make.return_value = lambda: None
            _sandbox_preexec(cfg)
        limits_arg = mock_make.call_args[0][0]
        assert limits_arg.cpu_seconds == 3600
        assert limits_arg.memory_bytes == 4 * 1024**3


class TestSandboxPreexecIntegration:
    """invoke_cli passes the correct preexec_fn to subprocess.Popen."""

    def test_invoke_cli_quiet_uses_sandbox_limits(self):
        """_invoke_cli_quiet passes SandboxLimits-configured preexec_fn to Popen."""
        cfg = CLIConfig(
            sandbox_enabled=True,
            sandbox_cpu_seconds=7200,
            sandbox_memory_gb=8,
        )
        mock_proc = MagicMock()
        mock_proc.__enter__ = MagicMock(return_value=mock_proc)
        mock_proc.__exit__ = MagicMock(return_value=False)
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (
            '{"type":"result","result":"ok","cost_usd":0,"duration_ms":1,'
            '"input_tokens":0,"output_tokens":0}\n',
            "",
        )

        captured_preexec = []

        def fake_popen(_cmd, **kwargs):
            captured_preexec.append(kwargs.get("preexec_fn"))
            mock_proc.pid = 12345
            return mock_proc

        with patch("golem.core.cli_wrapper.subprocess.Popen", side_effect=fake_popen):
            with patch(
                "golem.core.cli_wrapper._get_subprocess_env",
                return_value=(None, "/tmp/sandbox", lambda: None),
            ):
                with patch(
                    "golem.core.cli_wrapper._build_command", return_value=["echo"]
                ):
                    from golem.core.cli_wrapper import _invoke_cli_quiet

                    _invoke_cli_quiet("prompt", cfg)

        assert len(captured_preexec) == 1
        assert callable(captured_preexec[0])

    def test_invoke_cli_quiet_no_preexec_when_disabled(self):
        """_invoke_cli_quiet passes None preexec_fn when sandbox_enabled=False."""
        cfg = CLIConfig(sandbox_enabled=False)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (
            '{"type":"result","result":"ok","cost_usd":0,"duration_ms":1,'
            '"input_tokens":0,"output_tokens":0}\n',
            "",
        )
        captured_preexec = []

        def fake_popen(_cmd, **kwargs):
            captured_preexec.append(kwargs.get("preexec_fn"))
            mock_proc.pid = 12346
            return mock_proc

        with patch("golem.core.cli_wrapper.subprocess.Popen", side_effect=fake_popen):
            with patch(
                "golem.core.cli_wrapper._get_subprocess_env",
                return_value=(None, "/tmp/sandbox", lambda: None),
            ):
                with patch(
                    "golem.core.cli_wrapper._build_command", return_value=["echo"]
                ):
                    from golem.core.cli_wrapper import _invoke_cli_quiet

                    _invoke_cli_quiet("prompt", cfg)

        assert len(captured_preexec) == 1
        assert captured_preexec[0] is None
