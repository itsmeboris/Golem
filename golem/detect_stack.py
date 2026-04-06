# golem/detect_stack.py
"""Buildpack-style stack detection for per-repo verify config.

Entry point: detect_verify_config(repo_root, *, dry_run=True) -> VerifyConfig
"""

import json
import logging
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .verify_config import VerifyCommand, VerifyConfig

logger = logging.getLogger("golem.detect_stack")

_JOB_KEYWORDS_TEST = frozenset({"test", "tests", "ci", "check", "verify", "pytest"})
_JOB_KEYWORDS_LINT = frozenset({"lint", "format", "style"})

# Prefixes for setup/install commands that should never be promoted to
# test/lint roles during CI parsing.
_SETUP_PREFIXES = (
    "pip install",
    "pip3 install",
    "npm ci",
    "npm install",
    "yarn install",
    "pnpm install",
    "cargo build",
    "go mod",
    "bundle install",
    "apt ",
    "apt-get ",
    "brew ",
    "sudo ",
    "cd ",
    "mkdir ",
    "cp ",
    "mv ",
    "chmod ",
    "export ",
    "source ",
    "set ",
    "curl ",
    "wget ",
    "git ",
)


def _detect_python(root: Path) -> list[VerifyCommand]:
    """Return verify commands for a Python repo. Returns [] if no Python markers."""
    markers = {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "pytest.ini",
        "conftest.py",
    }
    if not any((root / m).exists() for m in markers):
        return []

    cmds: list[VerifyCommand] = []
    pyproject = root / "pyproject.toml"
    has_ruff = False

    if pyproject.exists():
        content = pyproject.read_text(encoding="utf-8", errors="ignore")
        if "[tool.ruff]" in content:
            has_ruff = True
            cmds.append(
                VerifyCommand(
                    role="format",
                    cmd=["ruff", "format", "--check", "."],
                    source="auto-detected",
                )
            )
            cmds.append(
                VerifyCommand(
                    role="lint", cmd=["ruff", "check", "."], source="auto-detected"
                )
            )
        elif "[tool.black]" in content:
            cmds.append(
                VerifyCommand(
                    role="format", cmd=["black", "--check", "."], source="auto-detected"
                )
            )

    if not has_ruff:
        cmds.append(
            VerifyCommand(
                role="lint",
                cmd=["pylint", "--errors-only", "."],
                source="auto-detected",
            )
        )

    # Test runner detection
    test_markers = {"pytest.ini", "conftest.py"}
    pyproject_has_pytest = False
    if pyproject.exists():
        pyproject_has_pytest = "[tool.pytest" in pyproject.read_text(
            encoding="utf-8", errors="ignore"
        )

    if any((root / m).exists() for m in test_markers) or pyproject_has_pytest:
        cmds.append(VerifyCommand(role="test", cmd=["pytest"], source="auto-detected"))
    elif any((root / m).exists() for m in markers):
        cmds.append(
            VerifyCommand(
                role="test", cmd=["python", "-m", "pytest"], source="auto-detected"
            )
        )

    return cmds


def _detect_javascript(root: Path) -> list[VerifyCommand]:
    """Return verify commands for a JS/TS repo. Returns [] if no package.json."""
    pkg_path = root / "package.json"
    if not pkg_path.exists():
        return []
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to parse package.json in %s: %s", root, exc)
        return []

    scripts = pkg.get("scripts", {})
    if not scripts:
        return []

    cmds: list[VerifyCommand] = []
    _SCRIPT_ROLES = {
        "test": "test",
        "jest": "test",
        "mocha": "test",
        "vitest": "test",
        "lint": "lint",
        "eslint": "lint",
        "tslint": "lint",
        "format": "format",
        "prettier": "format",
        "typecheck": "typecheck",
    }
    for script_name in scripts:
        role = _SCRIPT_ROLES.get(script_name.lower())
        if role:
            cmds.append(
                VerifyCommand(
                    role=role, cmd=["npm", "run", script_name], source="auto-detected"
                )
            )
    return cmds


def _detect_rust(root: Path) -> list[VerifyCommand]:
    """Return verify commands for a Rust repo. Returns [] if no Cargo.toml."""
    if not (root / "Cargo.toml").exists():
        return []
    return [
        VerifyCommand(
            role="format", cmd=["cargo", "fmt", "--check"], source="auto-detected"
        ),
        VerifyCommand(
            role="lint",
            cmd=["cargo", "clippy", "--", "-D", "warnings"],
            source="auto-detected",
        ),
        VerifyCommand(role="test", cmd=["cargo", "test"], source="auto-detected"),
    ]


def _detect_go(root: Path) -> list[VerifyCommand]:
    """Return verify commands for a Go repo. Returns [] if no go.mod."""
    if not (root / "go.mod").exists():
        return []
    return [
        VerifyCommand(role="format", cmd=["gofmt", "-l", "."], source="auto-detected"),
        VerifyCommand(role="lint", cmd=["go", "vet", "./..."], source="auto-detected"),
        VerifyCommand(role="test", cmd=["go", "test", "./..."], source="auto-detected"),
    ]


def _detect_ruby(root: Path) -> list[VerifyCommand]:
    """Return verify commands for a Ruby repo. Returns [] if no Gemfile."""
    if not (root / "Gemfile").exists():
        return []
    cmds: list[VerifyCommand] = []
    if (root / ".rubocop.yml").exists():
        cmds.append(VerifyCommand(role="lint", cmd=["rubocop"], source="auto-detected"))
    cmds.append(
        VerifyCommand(
            role="test", cmd=["bundle", "exec", "rspec"], source="auto-detected"
        )
    )
    return cmds


def _detect_makefile_targets(root: Path) -> set[str]:
    """Return set of target names found in root/Makefile."""
    import re  # pylint: disable=import-outside-toplevel

    makefile = root / "Makefile"
    if not makefile.exists():
        return set()
    try:
        content = makefile.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()
    return {
        m.group(1)
        for m in re.finditer(r"^([a-zA-Z][a-zA-Z0-9_-]*):", content, re.MULTILINE)
    }


# Regex to strip leading "cd <dir> &&" and "VAR=value" env wrappers
_CD_AND_RE = re.compile(r"^cd\s+\S+\s*&&\s*")
_ENV_ASSIGN_RE = re.compile(r"^[A-Z_][A-Z_0-9]*=\S+\s+")


def _strip_shell_wrappers(line: str) -> str:
    """Strip common shell wrappers (cd ... &&, VAR=val) from a command line.

    Returns the effective command after stripping, or "" if nothing remains.
    """
    # Strip "cd dir && rest"
    line = _CD_AND_RE.sub("", line)
    # Strip leading env assignments like "CI=1 pytest"
    while _ENV_ASSIGN_RE.match(line):
        line = _ENV_ASSIGN_RE.sub("", line, count=1)
    return line.strip()


def _parse_github_actions(root: Path) -> list[VerifyCommand]:
    """Extract run commands from .github/workflows/*.yml test/lint jobs."""
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return []

    cmds: list[VerifyCommand] = []
    for wf_path in sorted(workflows_dir.glob("*.yml")):
        try:
            raw = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError) as exc:
            logger.debug("Skipping malformed workflow %s: %s", wf_path.name, exc)
            continue
        if not isinstance(raw, dict) or "jobs" not in raw:
            continue
        for job_name, job in raw["jobs"].items():
            if not isinstance(job, dict):
                continue
            name_lower = job_name.lower()
            if any(kw in name_lower for kw in _JOB_KEYWORDS_TEST):
                role = "test"
            elif any(kw in name_lower for kw in _JOB_KEYWORDS_LINT):
                role = "lint"
            else:
                continue
            for step in job.get("steps", []):
                if not isinstance(step, dict):
                    continue
                run = step.get("run", "")
                if not run:
                    continue
                for line in run.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("echo "):
                        continue
                    # Strip wrappers first, then check for setup prefixes
                    effective = _strip_shell_wrappers(line)
                    if not effective:
                        continue
                    if any(effective.startswith(pfx) for pfx in _SETUP_PREFIXES):
                        continue
                    try:
                        parts = shlex.split(effective)
                    except ValueError:
                        # Unmatched quotes or other shell syntax — skip
                        continue
                    cmds.append(VerifyCommand(role=role, cmd=parts, source="ci-parsed"))
                    break
    return cmds


def _dry_run_tool(tool: str, root: Path) -> bool:
    """Return True if tool responds to --version within 5s."""
    try:
        result = subprocess.run(
            [tool, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(root),
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def _filter_unavailable(cmds: list[VerifyCommand], root: Path) -> list[VerifyCommand]:
    """Remove commands whose tool is not available on PATH. Caches per tool."""
    available: dict[str, bool] = {}
    result = []
    for cmd in cmds:
        tool = cmd.cmd[0]
        if tool not in available:
            available[tool] = _dry_run_tool(tool, root)
        if available[tool]:
            result.append(cmd)
        else:
            logger.info("Dropping detected command %s — tool not on PATH", tool)
    return result


def detect_verify_config(repo_root: str, *, dry_run: bool = True) -> VerifyConfig:
    """Detect verification commands for a repo using buildpack-style scanning.

    Runs language detectors, supplements with CI-parsed commands (de-duplicating
    by role), and optionally validates tools are on PATH.
    """
    root = Path(repo_root).resolve()
    stack: list[str] = []
    all_cmds: list[VerifyCommand] = []

    detectors = [
        ("python", _detect_python),
        ("javascript", _detect_javascript),
        ("rust", _detect_rust),
        ("go", _detect_go),
        ("ruby", _detect_ruby),
    ]
    for lang, detector in detectors:
        lang_cmds = detector(root)
        if lang_cmds:
            stack.append(lang)
            all_cmds.extend(lang_cmds)

    # CI-parsed commands supplement roles not already covered
    existing_roles = {c.role for c in all_cmds}
    for cmd in _parse_github_actions(root):
        if cmd.role not in existing_roles:
            all_cmds.append(cmd)
            existing_roles.add(cmd.role)

    if dry_run and all_cmds:
        all_cmds = _filter_unavailable(all_cmds, root)

    return VerifyConfig(
        version=1,
        commands=all_cmds,
        detected_at=datetime.now(timezone.utc).isoformat(),
        stack=stack,
    )
