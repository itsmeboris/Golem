"""Wrapper for invoking Claude/Agent CLI as a subprocess."""

# pylint: disable=too-many-lines

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .stream_printer import StreamPrinter as _StreamPrinter

logger = logging.getLogger("golem.core.cli_wrapper")

# .parent chain: core/ → task_agent/ (package root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

ProgressCallback = Callable[[dict], None]

_SOURCE_MCP_JSON = Path.home() / ".cursor" / "mcp.json"

_active_procs: dict[int, subprocess.Popen] = {}
_active_procs_lock = threading.Lock()


def _track_proc(proc: subprocess.Popen) -> None:
    with _active_procs_lock:
        _active_procs[proc.pid] = proc


def _untrack_proc(pid: int) -> None:
    with _active_procs_lock:
        _active_procs.pop(pid, None)


def kill_all_active(timeout: float = 5.0) -> int:
    """Send SIGTERM to every tracked CLI subprocess, SIGKILL after *timeout*.

    Returns the number of processes that were killed.
    """
    with _active_procs_lock:
        procs = list(_active_procs.values())
        _active_procs.clear()

    if not procs:
        return 0

    for proc in procs:
        try:
            proc.send_signal(signal.SIGTERM)
        except OSError:
            pass

    deadline = time.monotonic() + timeout
    alive = list(procs)
    while alive and time.monotonic() < deadline:
        time.sleep(0.2)
        alive = [p for p in alive if p.poll() is None]

    for proc in alive:
        try:
            proc.kill()
        except OSError:
            pass

    logger.info("Killed %d active CLI process(es)", len(procs))
    return len(procs)


def active_process_count() -> int:
    """Return the number of currently tracked CLI subprocesses."""
    with _active_procs_lock:
        return len(_active_procs)


class CLIType(Enum):
    """Supported CLI binary types."""

    AGENT = "agent"
    CLAUDE = "claude"


def _cwd_for_cli(cli_type: CLIType):
    """Return (cwd, cleanup) — temp sandbox for both Claude and Agent.

    Using a temp dir (even for Claude) ensures ``_prepare_work_dir``
    filtering runs, keeping interactive-only hooks out of child agents.
    """
    sandbox = tempfile.mkdtemp(
        prefix="flow_sandbox_" if cli_type == CLIType.CLAUDE else "agent_sandbox_"
    )

    def _remove():
        shutil.rmtree(sandbox, ignore_errors=True)

    return sandbox, _remove


@dataclass
class CLIConfig:
    """Configuration for a CLI invocation (model, budget, timeout)."""

    cli_type: CLIType = CLIType.AGENT
    max_budget_usd: float = 0.0
    model: str = "sonnet"
    timeout_seconds: int = 300
    mcp_servers: list[str] = field(default_factory=list)
    system_prompt: str = ""
    cwd: str = ""  # Override working directory (empty = project root for Claude)


@dataclass
class CLIResult:
    """Structured result of a CLI invocation with cost/token metrics."""

    output: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    trace_events: list[dict] = field(default_factory=list)


class CLIError(Exception):
    """Raised when a CLI invocation fails."""

    def __init__(self, message: str, returncode: int = 1, stderr: str = ""):
        if stderr:
            message = f"{message}\nstderr: {stderr.strip()}"
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class _ScopedHome:
    """Context manager that creates a temp HOME with a filtered mcp.json.

    Used by ``_get_subprocess_env()`` when ``config.mcp_servers`` is set.
    Symlinks everything from the real ``~/.cursor/`` except ``mcp.json``,
    which is rewritten to contain only the requested MCP servers.
    """

    def __init__(self, servers: list[str]):
        self._servers = servers
        self._tmpdir: tempfile.TemporaryDirectory | None = None

    def __enter__(self) -> dict[str, str]:
        real_home = Path.home()
        real_cursor = real_home / ".cursor"
        if not _SOURCE_MCP_JSON.exists():
            return _clean_env()

        self._tmpdir = tempfile.TemporaryDirectory(prefix="agent_mcp_")
        fake_home = Path(self._tmpdir.name)
        fake_cursor = fake_home / ".cursor"
        fake_cursor.mkdir()

        for item in real_cursor.iterdir():
            if item.name == "mcp.json":
                continue
            dest = fake_cursor / item.name
            try:
                dest.symlink_to(item)
            except OSError:
                if item.is_file():
                    shutil.copy2(item, dest)

        real_config = real_home / ".config"
        if real_config.is_dir():
            (fake_home / ".config").symlink_to(real_config)

        with open(_SOURCE_MCP_JSON, encoding="utf-8") as fh:
            full_cfg = json.load(fh)

        filtered = {
            k: v
            for k, v in full_cfg.get("mcpServers", {}).items()
            if k in self._servers
        }
        with open(fake_cursor / "mcp.json", "w", encoding="utf-8") as fh:
            json.dump({"mcpServers": filtered}, fh, indent=2)

        env = _clean_env()
        env["HOME"] = str(fake_home)
        logger.info("Scoped MCP env: %s (%d servers)", fake_home, len(filtered))
        return env

    def __exit__(self, *_exc):
        if self._tmpdir:
            self._tmpdir.cleanup()
            self._tmpdir = None


def _clean_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a copy of the environment without nesting-guard variables.

    Only strips the specific variables that Claude Code uses to detect
    nested sessions (``CLAUDECODE``, ``CLAUDE_CODE_*``).  Other
    ``CLAUDE_*`` variables (e.g. ``CLAUDE_API_KEY``) are preserved so
    that child processes can still authenticate.
    """
    base = dict(env) if env is not None else dict(os.environ)
    # Strip nesting guards and session metadata so child Claude
    # processes start fresh (not constrained by parent session).
    for key in list(base):
        if key == "CLAUDECODE" or key.startswith("CLAUDE_CODE_"):
            del base[key]
    return base


_PROJECT_MCP_JSON = _PROJECT_ROOT / ".mcp.json"
_PROJECT_CLAUDE_DIR = _PROJECT_ROOT / ".claude"


def _prepare_work_dir(cwd: str, mcp_servers: list[str]) -> Callable[[], None]:
    """Prepare a non-project-root CWD with Claude CLI config for child processes.

    Sets up the full environment a child Claude CLI needs to discover MCP
    servers, inject credentials, and access skills:

    - ``.mcp.json`` — filtered to *mcp_servers*
    - ``.mcp.env`` — symlink for credential-injection hook
    - ``.claude/settings.local.json`` — MCP enablement + sandbox config
    - ``.claude/settings.json`` — MCP credential injection hook only
    - ``.claude/hooks/`` — symlink to project-root hooks
    - ``.claude/skills/`` — symlink to project-root skills

    Returns a cleanup callable that removes all created artifacts.
    """
    cwd_path = Path(cwd).resolve()
    created: list[Path] = []

    if cwd_path == _PROJECT_ROOT.resolve():
        return lambda: None

    _copy_mcp_json(cwd_path, mcp_servers, created)
    _copy_mcp_env(cwd_path, created)
    _copy_claude_dir(cwd_path, created)

    if not created:
        return lambda: None

    def _cleanup():
        for path in reversed(created):
            try:
                if path.is_symlink() or path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    try:
                        path.rmdir()  # only succeeds if empty
                    except OSError:
                        pass
            except OSError:
                pass

    return _cleanup


def _copy_mcp_json(cwd_path: Path, mcp_servers: list[str] | None, created: list[Path]) -> None:
    """Copy filtered ``.mcp.json`` to *cwd_path*.

    *mcp_servers* controls filtering:
    - ``None`` — copy all servers (no filtering)
    - ``["a", "b"]`` — copy only named servers
    - ``[]`` — empty list means **no MCP** — skip copying entirely
    """
    if mcp_servers is not None and not mcp_servers:
        # Explicitly empty = no MCP servers wanted.
        return

    dst = cwd_path / ".mcp.json"
    if dst.exists():
        return
    if not _PROJECT_MCP_JSON.exists():
        return

    try:
        with open(_PROJECT_MCP_JSON, encoding="utf-8") as fh:
            full_cfg = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return

    servers = full_cfg.get("mcpServers", {})
    if mcp_servers:
        servers = {k: v for k, v in servers.items() if k in mcp_servers}
    if not servers:
        return

    try:
        dst.write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")
        created.append(dst)
        logger.info("Propagated .mcp.json to %s (%d servers)", cwd_path, len(servers))
    except OSError as exc:
        logger.warning("Could not write .mcp.json to %s: %s", cwd_path, exc)


def _copy_mcp_env(cwd_path: Path, created: list[Path]) -> None:
    """Symlink ``.mcp.env`` so the credential-injection hook finds it."""
    dst = cwd_path / ".mcp.env"
    src = _PROJECT_ROOT / ".mcp.env"
    if dst.exists() or dst.is_symlink():
        return
    if not src.is_file():
        return

    try:
        dst.symlink_to(src.resolve())
        created.append(dst)
        logger.info("Symlinked .mcp.env -> %s", src.resolve())
    except OSError as exc:
        logger.warning("Could not symlink .mcp.env to %s: %s", cwd_path, exc)


def _copy_claude_dir(cwd_path: Path, created: list[Path]) -> None:
    """Set up ``.claude/`` in *cwd_path* with settings, hooks, and skills."""
    if not _PROJECT_CLAUDE_DIR.is_dir():
        return

    claude_dir = cwd_path / ".claude"
    if not claude_dir.exists():
        try:
            claude_dir.mkdir(exist_ok=True)
            created.append(claude_dir)
        except OSError as exc:
            logger.warning("Could not create .claude/ in %s: %s", cwd_path, exc)
            return

    # settings.local.json — MCP enablement, sandbox config
    _write_settings_local(claude_dir, created)

    # settings.json — only the MCP credential injection hook
    _write_settings_json(claude_dir, created)

    # hooks/ — only include the MCP credential injection hook so that
    # interactive-only hooks (continual-learning, pylint-on-stop, etc.)
    # don't hijack autonomous agent sessions.
    _copy_hooks_filtered(claude_dir, created)

    # skills/ — copy (not symlink) so bwrap sandbox can access them.
    # Symlinks resolve outside the worktree and bwrap can't bind-mount them.
    _copy_subdir(claude_dir, "skills", created)


def _write_settings_local(claude_dir: Path, created: list[Path]) -> None:
    """Write ``settings.local.json`` for MCP enablement and Bash permissions.

    Includes sandbox config with ``autoAllowBashIfSandboxed`` so Bash
    is permitted in worktree directories. The child agent also uses
    ``--dangerously-skip-permissions`` on the CLI for full bypass.
    """
    dst = claude_dir / "settings.local.json"
    existed = dst.exists()
    settings: dict = {
        "enableAllProjectMcpServers": True,
        "permissions": {
            "allow": [
                "Read",
                "Edit",
                "Write",
                "Glob",
                "Grep",
                "Bash",
                "WebSearch",
                "WebFetch",
                "Task",
                "NotebookEdit",
                "ToolSearch",
            ],
        },
        "sandbox": {
            "enabled": True,
            "autoAllowBashIfSandboxed": True,
        },
    }

    try:
        # Always overwrite — existing file may have restrictive permissions
        # that block the child agent (which uses --dangerously-skip-permissions).
        dst.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        if not existed:
            created.append(dst)
        logger.info("Wrote settings.local.json to %s", claude_dir)
    except OSError as exc:
        logger.warning("Could not write settings.local.json: %s", exc)


def _write_settings_json(claude_dir: Path, created: list[Path]) -> None:
    """Write ``settings.json`` with only the MCP credential injection hook.

    Always overwrites — the CWD may already contain a ``settings.json``
    with restrictive interactive-mode permissions (e.g. ``Bash(git *)``).
    The autonomous agent needs the broad permissions from
    ``settings.local.json`` to take effect, so ``settings.json`` must
    contain only hooks (no ``permissions`` key).
    """
    dst = claude_dir / "settings.json"
    existed = dst.exists()

    # Extract MCP hooks from the project's settings.json (if any).
    minimal_hooks: dict = {}
    src = _PROJECT_CLAUDE_DIR / "settings.json"
    if src.is_file():
        try:
            with open(src, encoding="utf-8") as fh:
                settings = json.load(fh)
        except (json.JSONDecodeError, OSError):
            settings = {}

        # For autonomous agents, keep only the MCP credential injection hook.
        # Other hooks (pylint-on-stop, audit-edits) are for interactive use.
        for event, hook_list in settings.get("hooks", {}).items():
            if event == "PreToolUse":
                filtered = [
                    entry
                    for entry in hook_list
                    if "mcp" in entry.get("matcher", "").lower()
                ]
                if filtered:
                    minimal_hooks[event] = filtered

    try:
        dst.write_text(
            json.dumps({"hooks": minimal_hooks}, indent=2),
            encoding="utf-8",
        )
        if not existed:
            created.append(dst)
        logger.info("Wrote settings.json (MCP hook only) to %s", claude_dir)
    except OSError as exc:
        logger.warning("Could not write settings.json: %s", exc)


# Hooks that are safe to run in autonomous agent sessions.
_AGENT_SAFE_HOOKS = {"mcp_inject_credentials.py"}


def _copy_hooks_filtered(claude_dir: Path, created: list[Path]) -> None:
    """Create a ``.claude/hooks/`` dir with only agent-safe hook scripts."""
    src_dir = _PROJECT_CLAUDE_DIR / "hooks"
    if not src_dir.is_dir():
        return
    dst_dir = claude_dir / "hooks"
    if dst_dir.exists() or dst_dir.is_symlink():
        return

    try:
        dst_dir.mkdir(exist_ok=True)
        created.append(dst_dir)
    except OSError as exc:
        logger.warning("Could not create .claude/hooks/ in %s: %s", claude_dir, exc)
        return

    for hook in src_dir.iterdir():
        if hook.name not in _AGENT_SAFE_HOOKS:
            continue
        dst = dst_dir / hook.name
        try:
            dst.symlink_to(hook.resolve())
            created.append(dst)
            logger.info("Linked agent-safe hook %s", hook.name)
        except OSError as exc:
            logger.warning("Could not link hook %s: %s", hook.name, exc)


def _copy_subdir(claude_dir: Path, name: str, created: list[Path]) -> None:
    """Copy ``.claude/<name>/`` from the project root into *claude_dir*.

    A physical copy (instead of a symlink) is required so that ``bwrap``
    sandbox can bind-mount the directory — symlinks resolve to paths
    outside the worktree, which ``bwrap`` cannot access.
    """
    dst = claude_dir / name
    src = _PROJECT_CLAUDE_DIR / name
    if dst.exists() or dst.is_symlink():
        return
    if not src.is_dir():
        return

    try:
        shutil.copytree(src, dst, symlinks=True)
        created.append(dst)
        logger.info("Copied .claude/%s from %s", name, src.resolve())
    except OSError as exc:
        logger.warning("Could not copy .claude/%s: %s", name, exc)


def _get_subprocess_env(config: CLIConfig):
    """Return (env_dict, cwd, cleanup) for subprocess.

    For ``CLIType.CLAUDE``, uses *config.cwd* when set (e.g. a worktree),
    otherwise creates a temporary directory.  In both cases the CWD
    differs from the project root so ``_prepare_work_dir`` copies a
    filtered Claude CLI configuration — keeping only agent-safe hooks
    and stripping interactive-only ones (continual-learning, pylint, …).

    For ``CLIType.AGENT``, creates a temporary sandbox directory and
    optionally a scoped HOME to filter ``mcp.json``.
    """
    if config.cli_type == CLIType.CLAUDE:
        cwd_is_project_root = (
            config.cwd
            and Path(config.cwd).resolve() == _PROJECT_ROOT.resolve()
        )
        if config.cwd and not cwd_is_project_root:
            # Explicit CWD (e.g. worktree) — use as-is.
            cwd = config.cwd
            cwd_cleanup: Callable[[], None] = lambda: None
        else:
            # No CWD or CWD is the project root — use a temp dir so
            # _prepare_work_dir runs its settings sanitization.
            # Running directly in the project root would skip
            # _prepare_work_dir (which checks for CWD == _PROJECT_ROOT),
            # leaving restrictive interactive-mode permissions in
            # .claude/settings.json that block Bash for child agents.
            cwd = tempfile.mkdtemp(prefix="flow_sandbox_")
            _cwd = cwd  # capture for closure

            def cwd_cleanup():
                shutil.rmtree(_cwd, ignore_errors=True)

        workdir_cleanup = _prepare_work_dir(cwd, config.mcp_servers)
        env = _clean_env()
        # Skip heavy pre-commit tests in agent worktrees — the supervisor
        # runs its own validation pass before committing.
        if config.cwd and not cwd_is_project_root:
            env["AGENT_WORKTREE"] = "1"

        def _cleanup():
            workdir_cleanup()
            cwd_cleanup()

        return env, cwd, _cleanup

    sandbox = tempfile.mkdtemp(prefix="agent_sandbox_")

    if not config.mcp_servers:
        return None, sandbox, lambda: shutil.rmtree(sandbox, ignore_errors=True)

    scope = _ScopedHome(config.mcp_servers)
    env = scope.__enter__()  # pylint: disable=unnecessary-dunder-call

    def _cleanup():
        scope.__exit__(None, None, None)
        shutil.rmtree(sandbox, ignore_errors=True)

    return env, sandbox, _cleanup


def _build_agent_command(config: CLIConfig, output_format: str = "json") -> list[str]:
    cmd = [
        "agent",
        "-p",
        "--trust",
        "--output-format",
        output_format,
        "--approve-mcps",
        "--model",
        config.model,
    ]
    if output_format == "stream-json":
        cmd.append("--stream-partial-output")
    return cmd


def _build_claude_command(config: CLIConfig, output_format: str = "json") -> list[str]:
    cmd = [
        "claude",
        "-p",
        "--output-format",
        output_format,
        "--model",
        config.model,
        "--dangerously-skip-permissions",
    ]
    if config.max_budget_usd > 0:
        cmd.extend(["--max-budget-usd", str(config.max_budget_usd)])
    if config.system_prompt:
        cmd.extend(["--append-system-prompt", config.system_prompt])
    if output_format == "stream-json":
        cmd.append("--verbose")
    return cmd


def _build_command(config: CLIConfig, output_format: str = "json") -> list[str]:
    if config.cli_type == CLIType.AGENT:
        return _build_agent_command(config, output_format)
    return _build_claude_command(config, output_format)


def invoke_cli(prompt: str, config: CLIConfig, verbose: bool = False) -> CLIResult:
    """Run the CLI with *prompt* and return a CLIResult with metrics.

    When *verbose* is True, uses stream-json format and prints real-time
    progress (tool calls, thinking, content) to the console.
    """
    if verbose:
        return _invoke_cli_verbose(prompt, config)
    return _invoke_cli_quiet(prompt, config)


def _extract_metrics(data: dict) -> dict:
    """Pull cost/token/duration metrics from a CLI JSON response.

    Handles both ``agent`` and ``claude`` output formats:
      - agent: cost_usd, input_tokens, output_tokens at top level
      - claude: total_cost_usd, usage.input_tokens, usage.output_tokens
    """
    cost = data.get("cost_usd") or data.get("total_cost_usd") or 0
    usage = data.get("usage", {})
    input_tokens = data.get("input_tokens") or usage.get("input_tokens") or 0
    output_tokens = data.get("output_tokens") or usage.get("output_tokens") or 0
    return {
        "cost_usd": float(cost),
        "duration_ms": int(data.get("duration_ms", 0)),
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
    }


def _parse_stream_output(stdout: str) -> tuple[dict, list[dict]]:
    """Parse stream-json stdout into (data_dict, trace_events).

    Tries JSONL line-by-line first; falls back to single-JSON parsing.
    """
    traces: list[dict] = []
    result_event: dict | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        traces.append(event)
        if event.get("type") == "result":
            result_event = event

    if result_event is not None:
        return result_event, traces

    try:
        return json.loads(stdout), traces
    except json.JSONDecodeError:
        return {"raw_output": stdout, "parse_error": True}, traces


def _extract_error_from_stream_output(stdout: str, stderr: str) -> str:
    """Extract error content from CLI output, filtering out the system init JSON.

    In stream-json mode the init message lists every available tool and can
    exceed 2000 characters, drowning out the actual error.  This function
    skips ``{"type":"system","subtype":"init",...}`` lines and returns only
    the error-relevant content from stdout and stderr.
    """
    parts: list[str] = []

    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
            if event.get("type") == "system" and event.get("subtype") == "init":
                continue
        except (json.JSONDecodeError, TypeError):
            pass
        parts.append(stripped)

    if stderr and stderr.strip():
        parts.append(stderr.strip())

    combined = "\n".join(parts)
    if not combined:
        return "CLI exited during init with no error details"
    return combined[:3000]


def _invoke_cli_quiet(prompt: str, config: CLIConfig) -> CLIResult:
    cmd = _build_command(config, "stream-json")

    logger.info("Invoking %s CLI with model %s", config.cli_type.value, config.model)

    env, sandbox, cleanup = _get_subprocess_env(config)
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(  # pylint: disable=consider-using-with
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=sandbox,
        )
        _track_proc(proc)
        stdout, stderr = proc.communicate(input=prompt, timeout=config.timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        if proc is not None:
            proc.kill()
            proc.communicate()
        raise CLIError(
            f"CLI timed out after {config.timeout_seconds}s",
            returncode=-1,
            stderr="timeout",
        ) from exc
    except FileNotFoundError as err:
        raise CLIError(
            f"CLI not found: {config.cli_type.value}. Is it installed and in PATH?",
            returncode=-1,
        ) from err
    finally:
        if proc is not None:
            _untrack_proc(proc.pid)
        cleanup()

    if proc.returncode != 0:
        raise CLIError(
            f"CLI failed with exit code {proc.returncode}",
            returncode=proc.returncode,
            stderr=_extract_error_from_stream_output(stdout, stderr),
        )

    data, traces = _parse_stream_output(stdout)

    if data.get("is_error"):
        error_text = data.get("result", "") or "unknown error"
        raise CLIError(f"CLI reported error: {error_text}", returncode=1)

    metrics = _extract_metrics(data)
    return CLIResult(
        output=data,
        cost_usd=metrics["cost_usd"],
        duration_ms=metrics["duration_ms"],
        input_tokens=metrics["input_tokens"],
        output_tokens=metrics["output_tokens"],
        trace_events=traces,
    )


def _invoke_cli_verbose(prompt: str, config: CLIConfig) -> CLIResult:
    cmd = _build_command(config, "stream-json")
    logger.info(
        "Invoking %s CLI with model %s (verbose)",
        config.cli_type.value,
        config.model,
    )
    start = time.time()
    collected: dict = {"output": {}, "metrics": {}, "traces": []}
    printer = _StreamPrinter(sys.stderr)

    env, sandbox, cleanup = _get_subprocess_env(config)
    try:
        with subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            cwd=sandbox,
        ) as proc:
            _track_proc(proc)
            try:
                if proc.stdin is not None:
                    proc.stdin.write(prompt)
                    proc.stdin.close()
                if proc.stdout is None:
                    raise CLIError("Failed to capture stdout", returncode=-1)

                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    collected["traces"].append(event)
                    printer.handle(event)

                    if event.get("type") == "result":
                        collected["output"] = {"result": event.get("result", "")}
                        collected["metrics"] = _extract_metrics(event)

                proc.wait()
                if proc.returncode != 0:
                    stderr_out = proc.stderr.read() if proc.stderr else ""
                    raise CLIError(
                        f"CLI failed with exit code {proc.returncode}",
                        returncode=proc.returncode,
                        stderr=stderr_out,
                    )
            finally:
                _untrack_proc(proc.pid)

    except FileNotFoundError as err:
        raise CLIError(
            f"CLI not found: {config.cli_type.value}. Is it installed and in PATH?",
            returncode=-1,
        ) from err
    finally:
        cleanup()

    elapsed = time.time() - start
    logger.info("CLI completed in %.1fs", elapsed)

    collected["metrics"].setdefault("duration_ms", int(elapsed * 1000))
    return CLIResult(
        output=collected["output"] or {"raw_output": "", "parse_error": True},
        cost_usd=collected["metrics"].get("cost_usd", 0.0),
        duration_ms=collected["metrics"]["duration_ms"],
        input_tokens=collected["metrics"].get("input_tokens", 0),
        output_tokens=collected["metrics"].get("output_tokens", 0),
        trace_events=collected["traces"],
    )


def invoke_cli_raw(prompt: str, config: CLIConfig) -> str:
    """Run the CLI with *prompt* and return raw text output."""
    cmd = _build_command(config, "text")

    logger.info("Invoking %s CLI (raw mode)", config.cli_type.value)

    cwd, cleanup = _cwd_for_cli(config.cli_type)
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(  # pylint: disable=consider-using-with
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )
        _track_proc(proc)
        stdout, stderr = proc.communicate(input=prompt, timeout=config.timeout_seconds)
    except subprocess.TimeoutExpired as err:
        if proc is not None:
            proc.kill()
            proc.communicate()
        raise CLIError(
            f"CLI timed out after {config.timeout_seconds}s",
            returncode=-1,
            stderr=str(err),
        ) from err
    except FileNotFoundError as err:
        raise CLIError(
            f"CLI not found: {config.cli_type.value}",
            returncode=-1,
        ) from err
    finally:
        if proc is not None:
            _untrack_proc(proc.pid)
        cleanup()

    if proc.returncode != 0:
        raise CLIError(
            f"CLI failed: {stderr}",
            returncode=proc.returncode,
            stderr=stderr,
        )

    return stdout


def invoke_cli_streaming(
    prompt: str,
    config: CLIConfig,
    callback: ProgressCallback | None = None,
) -> str:
    """Run the CLI in streaming mode, calling *callback* for each event."""
    cmd = _build_command(config, "stream-json")

    logger.info("Invoking %s CLI (streaming mode)", config.cli_type.value)

    start_time = time.time()
    full_output = []
    cwd, cleanup = _cwd_for_cli(config.cli_type)

    try:
        with subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=cwd,
        ) as proc:
            _track_proc(proc)
            try:
                if proc.stdin is not None:
                    proc.stdin.write(prompt)
                    proc.stdin.close()
                if proc.stdout is None:
                    raise CLIError("Failed to capture stdout", returncode=-1)

                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        event = json.loads(line)
                        full_output.append(event)

                        if callback:
                            callback(event)

                    except json.JSONDecodeError:
                        full_output.append({"raw": line})

                proc.wait()

                if proc.returncode != 0:
                    stderr = proc.stderr.read() if proc.stderr else ""
                    raise CLIError(
                        f"CLI failed with exit code {proc.returncode}",
                        returncode=proc.returncode,
                        stderr=stderr,
                    )
            finally:
                _untrack_proc(proc.pid)

    except FileNotFoundError as err:
        raise CLIError(
            f"CLI not found: {config.cli_type.value}. Is it installed and in PATH?",
            returncode=-1,
        ) from err
    finally:
        cleanup()

    elapsed = time.time() - start_time
    logger.info("CLI completed in %.1fs", elapsed)

    return json.dumps(full_output)


def invoke_cli_monitored(
    prompt: str,
    config: CLIConfig,
    callback: ProgressCallback | None = None,
) -> CLIResult:
    """Stream-json with real-time callbacks AND structured CLIResult return.

    Combines the best of ``_invoke_cli_verbose`` (returns ``CLIResult`` with
    metrics) and ``invoke_cli_streaming`` (per-event callback).  Also uses
    ``_get_subprocess_env`` for proper MCP scoping, which
    ``invoke_cli_streaming`` lacks.
    """
    cmd = _build_command(config, "stream-json")
    logger.info(
        "Invoking %s CLI with model %s (monitored)",
        config.cli_type.value,
        config.model,
    )

    start = time.time()
    collected: dict = {"output": {}, "metrics": {}, "traces": []}

    env, cwd, cleanup = _get_subprocess_env(config)
    try:
        with subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            cwd=cwd,
        ) as proc:
            _track_proc(proc)
            try:
                if proc.stdin is not None:
                    proc.stdin.write(prompt)
                    proc.stdin.close()
                if proc.stdout is None:
                    raise CLIError("Failed to capture stdout", returncode=-1)

                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    collected["traces"].append(event)

                    if callback:
                        callback(event)

                    if event.get("type") == "result":
                        collected["output"] = {"result": event.get("result", "")}
                        collected["metrics"] = _extract_metrics(event)

                proc.wait()
                if proc.returncode != 0:
                    stderr_out = proc.stderr.read() if proc.stderr else ""
                    raise CLIError(
                        f"CLI failed with exit code {proc.returncode}",
                        returncode=proc.returncode,
                        stderr=stderr_out,
                    )
            finally:
                _untrack_proc(proc.pid)

    except FileNotFoundError as err:
        raise CLIError(
            f"CLI not found: {config.cli_type.value}. Is it installed and in PATH?",
            returncode=-1,
        ) from err
    finally:
        cleanup()

    elapsed = time.time() - start
    logger.info("CLI (monitored) completed in %.1fs", elapsed)

    collected["metrics"].setdefault("duration_ms", int(elapsed * 1000))
    return CLIResult(
        output=collected["output"] or {"raw_output": "", "parse_error": True},
        cost_usd=collected["metrics"].get("cost_usd", 0.0),
        duration_ms=collected["metrics"]["duration_ms"],
        input_tokens=collected["metrics"].get("input_tokens", 0),
        output_tokens=collected["metrics"].get("output_tokens", 0),
        trace_events=collected["traces"],
    )
