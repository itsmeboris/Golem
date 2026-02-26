"""Tests for CLI worktree/workdir preparation (skills copy, sandbox compat)."""

# pylint: disable=missing-class-docstring,missing-function-docstring

from pathlib import Path
from unittest.mock import patch

from task_agent.core.cli_wrapper import _copy_subdir, _prepare_work_dir


class TestCopySubdir:
    """Regression: .claude/skills must be copied (not symlinked) for bwrap sandbox."""

    def test_copies_directory_not_symlink(self, tmp_path):
        """Skills dir must be a real directory, not a symlink, so bwrap can access it."""
        src_claude = tmp_path / "src" / ".claude"
        src_skills = src_claude / "skills"
        src_skills.mkdir(parents=True)
        (src_skills / "test-skill").mkdir()
        (src_skills / "test-skill" / "SKILL.md").write_text("# Test")

        dst_claude = tmp_path / "dst" / ".claude"
        dst_claude.mkdir(parents=True)

        created: list[Path] = []
        with patch("task_agent.core.cli_wrapper._PROJECT_CLAUDE_DIR", src_claude):
            _copy_subdir(dst_claude, "skills", created)

        dst_skills = dst_claude / "skills"
        assert dst_skills.exists()
        assert dst_skills.is_dir()
        assert not dst_skills.is_symlink(), "skills must be copied, not symlinked"
        assert (dst_skills / "test-skill" / "SKILL.md").read_text() == "# Test"
        assert dst_skills in created

    def test_skips_if_dst_already_exists(self, tmp_path):
        src_claude = tmp_path / "src" / ".claude"
        (src_claude / "skills").mkdir(parents=True)

        dst_claude = tmp_path / "dst" / ".claude"
        dst_skills = dst_claude / "skills"
        dst_skills.mkdir(parents=True)
        (dst_skills / "existing.txt").write_text("keep")

        created: list[Path] = []
        with patch("task_agent.core.cli_wrapper._PROJECT_CLAUDE_DIR", src_claude):
            _copy_subdir(dst_claude, "skills", created)

        assert (dst_skills / "existing.txt").read_text() == "keep"
        assert not created

    def test_skips_if_src_missing(self, tmp_path):
        src_claude = tmp_path / "src" / ".claude"
        src_claude.mkdir(parents=True)

        dst_claude = tmp_path / "dst" / ".claude"
        dst_claude.mkdir(parents=True)

        created: list[Path] = []
        with patch("task_agent.core.cli_wrapper._PROJECT_CLAUDE_DIR", src_claude):
            _copy_subdir(dst_claude, "skills", created)

        assert not (dst_claude / "skills").exists()
        assert not created


class TestPrepareWorkDir:
    """Regression: _prepare_work_dir must produce a real skills dir, not a symlink."""

    def test_skills_are_copied_not_symlinked(self, tmp_path):
        """End-to-end: skills in a prepared work dir must not be symlinks."""
        src_claude = tmp_path / "src_project" / ".claude"
        src_skills = src_claude / "skills"
        src_skills.mkdir(parents=True)
        (src_skills / "my-skill").mkdir()
        (src_skills / "my-skill" / "SKILL.md").write_text("# Skill")

        # Also need settings.json for _write_settings_json
        (src_claude / "settings.json").write_text("{}")

        # Create a hooks dir with the agent-safe hook
        hooks_dir = src_claude / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "mcp_inject_credentials.py").write_text("# hook")

        # Also need project .mcp.json
        project_root = tmp_path / "src_project"
        (project_root / ".mcp.json").write_text('{"mcpServers": {}}')

        workdir = tmp_path / "workdir"
        workdir.mkdir()

        with (
            patch("task_agent.core.cli_wrapper._PROJECT_ROOT", project_root),
            patch("task_agent.core.cli_wrapper._PROJECT_CLAUDE_DIR", src_claude),
        ):
            cleanup = _prepare_work_dir(str(workdir), mcp_servers=[])

        try:
            skills_dst = workdir / ".claude" / "skills"
            assert skills_dst.exists(), ".claude/skills should exist in workdir"
            assert (
                not skills_dst.is_symlink()
            ), ".claude/skills must be copied, not symlinked (bwrap can't follow symlinks)"
            assert (skills_dst / "my-skill" / "SKILL.md").read_text() == "# Skill"
        finally:
            cleanup()
