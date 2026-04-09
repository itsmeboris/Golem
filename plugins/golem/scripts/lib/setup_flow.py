"""Setup flow helpers — signal collection and verify.yaml finalization."""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


# Files to look for when collecting repo signals
_SIGNAL_FILES = [
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".github/workflows",
    ".gitlab-ci.yml",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
]


def _resolve_python_cmd(cmd: list[str]) -> list[str]:
    """Normalize python commands to use the current interpreter.

    Replaces 'python', 'python3', or any python variant at cmd[0] with
    sys.executable so that verify commands run under the same interpreter
    that has the project's tools installed (black, pylint, pytest, etc.).
    """
    if not cmd:
        return cmd
    if re.match(r"^python(3(\.\d+)*)?$", cmd[0]):
        return [sys.executable] + cmd[1:]
    return cmd


def verify_commands(repo_path: str) -> dict:
    """Run all verify commands from golem.md internally.

    Returns a dict with results per command. Only includes error details
    for failed commands — passing commands are reported minimally to save tokens.
    """
    root = Path(repo_path)
    golem_md = root / "golem.md"

    if not golem_md.exists():
        return {"ok": False, "error": "golem.md not found"}

    content = golem_md.read_text()
    commands = _parse_verify_commands(content)

    if not commands:
        return {"ok": False, "error": "No verify commands found in golem.md"}

    results = []
    all_passed = True

    for cmd_entry in commands:
        cmd = _resolve_python_cmd(list(cmd_entry["cmd"]))
        timeout = cmd_entry.get("timeout", 120)
        role = cmd_entry["role"]

        # Pre-check: verify the executable exists before running
        import shutil as _shutil

        exe = cmd[0] if cmd else ""
        if exe != sys.executable and not _shutil.which(exe):
            all_passed = False
            results.append({
                "role": role,
                "cmd": cmd,
                "passed": False,
                "error": f"'{exe}' not found in PATH. "
                f"Check the command name or install the tool.",
            })
            continue

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                text=True,
                cwd=str(root),
            )
            passed = proc.returncode == 0
            entry = {
                "role": role,
                "cmd": cmd,
                "passed": passed,
            }
            if not passed:
                all_passed = False
                # Include error output only for failures
                entry["stdout"] = proc.stdout[-2000:] if proc.stdout else ""
                entry["stderr"] = proc.stderr[-2000:] if proc.stderr else ""
                entry["returncode"] = proc.returncode
            results.append(entry)
        except FileNotFoundError as exc:
            all_passed = False
            results.append({
                "role": role,
                "cmd": cmd,
                "passed": False,
                "error": f"Command not found: {exc}",
            })
        except subprocess.TimeoutExpired:
            all_passed = False
            results.append({
                "role": role,
                "cmd": cmd,
                "passed": False,
                "error": f"Timed out after {timeout}s",
            })

    return {
        "ok": all_passed,
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": [r for r in results if not r["passed"]],
        "results": results,
    }


def collect_repo_signals(repo_path: str) -> dict:
    """Collect repo signals for golem.md generation.

    Returns a dict with detected files and their existence status.
    """
    root = Path(repo_path)
    detected = {}
    for name in _SIGNAL_FILES:
        target = root / name
        detected[name] = target.exists() or target.is_dir()

    # Check for existing golem.md and verify.yaml
    detected["golem.md"] = (root / "golem.md").exists()
    detected[".golem/verify.yaml"] = (root / ".golem" / "verify.yaml").exists()

    return {
        "repo_path": str(root.resolve()),
        "repo_name": root.resolve().name,
        "detected_files": {k: v for k, v in detected.items() if v},
        "missing_files": {k: v for k, v in detected.items() if not v},
    }


def finalize_setup(repo_path: str) -> dict:
    """Parse golem.md verify section and write .golem/verify.yaml.

    Also adds golem.md to .gitignore if not already present.
    Returns status dict.
    """
    root = Path(repo_path)
    golem_md = root / "golem.md"

    if not golem_md.exists():
        return {"ok": False, "error": "golem.md not found"}

    content = golem_md.read_text()
    commands = _parse_verify_commands(content)

    if not commands:
        return {"ok": False, "error": "No verify commands found in golem.md"}

    # Use golem's own verify_config module to write, getting path-safety
    # checks and schema validation for free. We shell out to a small Python
    # snippet that imports from the installed golem package.
    config = {
        "version": 1,
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "stack": _detect_stack_from_golem_md(content),
        "commands": commands,
    }

    # Validate commands match VerifyCommand schema
    valid_roles = {"format", "lint", "test", "typecheck"}
    for cmd_entry in commands:
        if cmd_entry.get("role") not in valid_roles:
            return {"ok": False, "error": f"Invalid role: {cmd_entry.get('role')}"}
        if not isinstance(cmd_entry.get("cmd"), list) or not cmd_entry["cmd"]:
            return {"ok": False, "error": f"Invalid cmd: {cmd_entry.get('cmd')}"}

    # Write via golem's save_verify_config for path-safety and schema compliance.
    # Use sys.executable to find the Python that has golem installed (we're running
    # from the same environment as the golem CLI).
    python_exe = sys.executable or "python3"
    try:
        result = subprocess.run(
            [
                python_exe, "-c",
                "import json, sys; "
                "from golem.verify_config import VerifyConfig, VerifyCommand, save_verify_config; "
                "data = json.loads(sys.stdin.read()); "
                "cmds = [VerifyCommand(role=c['role'], cmd=c['cmd'], source=c.get('source', 'agent-discovered'), "
                "timeout=c.get('timeout')) for c in data['commands']]; "
                f"vc = VerifyConfig(version=1, commands=cmds, detected_at=data['detected_at'], stack=data['stack']); "
                f"save_verify_config({repr(str(root))}, vc); "
                "print('ok')",
            ],
            input=json.dumps(config),
            capture_output=True,
            timeout=10,
            text=True,
        )
        if result.returncode != 0:
            return {"ok": False, "error": f"save_verify_config failed: {result.stderr.strip()}"}
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"Failed to write verify.yaml: {exc}"}

    # Add golem.md to .gitignore
    _ensure_gitignored(root, "golem.md")

    verify_yaml_path = root / ".golem" / "verify.yaml"
    return {
        "ok": True,
        "verify_yaml_path": str(verify_yaml_path),
        "command_count": len(commands),
    }


def _parse_verify_commands(content: str) -> list[dict]:
    """Parse structured verify commands from golem.md content.

    Expected format per line:
    - **role:** `test` | **cmd:** `["pytest", ...]` | **timeout:** 120
    """
    commands = []
    in_verify = False

    for line in content.splitlines():
        stripped = line.strip()

        # Track when we're in the ### verify section
        if stripped.startswith("### verify"):
            in_verify = True
            continue
        if stripped.startswith("### ") and in_verify:
            break  # Left the verify section

        if not in_verify or not stripped.startswith("- **role:**"):
            continue

        # Parse: - **role:** `test` | **cmd:** `[...]` | **timeout:** 120
        role_match = re.search(r"\*\*role:\*\*\s*`(\w+)`", stripped)
        cmd_match = re.search(r"\*\*cmd:\*\*\s*`(\[.*?\])`", stripped)
        timeout_match = re.search(r"\*\*timeout:\*\*\s*(\d+)", stripped)

        if role_match and cmd_match:
            try:
                cmd_list = json.loads(cmd_match.group(1))
            except json.JSONDecodeError:
                continue

            entry = {
                "role": role_match.group(1),
                "cmd": cmd_list,
                "source": "agent-discovered",
            }
            if timeout_match:
                entry["timeout"] = int(timeout_match.group(1))
            commands.append(entry)

    return commands


def _detect_stack_from_golem_md(content: str) -> list[str]:
    """Extract stack languages from golem.md Stack section."""
    stack = []
    in_stack = False
    lang_map = {
        "python": "python",
        "javascript": "javascript",
        "typescript": "typescript",
        "go": "go",
        "rust": "rust",
        "java": "java",
        "ruby": "ruby",
    }

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Stack"):
            in_stack = True
            continue
        if stripped.startswith("## ") and in_stack:
            break

        if in_stack:
            lower = stripped.lower()
            for key, lang in lang_map.items():
                if key in lower and lang not in stack:
                    stack.append(lang)

    return stack


def _ensure_gitignored(root: Path, entry: str) -> None:
    """Add entry to .gitignore if not already present."""
    gitignore = root / ".gitignore"
    if gitignore.exists():
        existing = gitignore.read_text()
        if entry in existing.splitlines():
            return
        if not existing.endswith("\n"):
            existing += "\n"
        gitignore.write_text(existing + entry + "\n")
    else:
        gitignore.write_text(entry + "\n")
