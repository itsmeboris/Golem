"""Tests for agents/golem/workdir.py — work-dir resolution logic."""

# pylint: disable=missing-class-docstring,missing-function-docstring

from golem.workdir import (
    _parse_description_workdir,
    _parse_subject_workdir,
    resolve_work_dir,
)


class TestParseDescriptionWorkdir:
    def test_empty_description_returns_empty(self):
        assert _parse_description_workdir("") == ""

    def test_existing_directory_returned(self, tmp_path):
        desc = f"work_dir: {tmp_path}"
        assert _parse_description_workdir(desc) == str(tmp_path)

    def test_nonexistent_directory_returns_empty(self, tmp_path):
        missing = tmp_path / "no_such_dir"
        desc = f"work_dir: {missing}"
        assert _parse_description_workdir(desc) == ""

    def test_path_outside_allowed_bases_returns_empty(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        allowed = [str(tmp_path / "other")]
        desc = f"work_dir: {target}"
        assert _parse_description_workdir(desc, allowed_bases=allowed) == ""

    def test_path_inside_allowed_bases_returned(self, tmp_path):
        target = tmp_path / "allowed" / "sub"
        target.mkdir(parents=True)
        allowed = [str(tmp_path / "allowed")]
        desc = f"work_dir: {target}"
        assert _parse_description_workdir(desc, allowed_bases=allowed) == str(target)

    def test_case_insensitive_key(self, tmp_path):
        for key in ("Work_Dir:", "WORK_DIR:", "work_DIR:"):
            desc = f"{key} {tmp_path}"
            assert _parse_description_workdir(desc) == str(
                tmp_path
            ), f"failed for {key}"

    def test_multiline_directive_not_on_first_line(self, tmp_path):
        desc = f"Some preamble text\nwork_dir: {tmp_path}\nmore text"
        assert _parse_description_workdir(desc) == str(tmp_path)


class TestParseSubjectWorkdir:
    def test_empty_subject_returns_empty(self):
        assert _parse_subject_workdir("", {"MYPROJECT": "/path"}) == ""

    def test_empty_work_dirs_returns_empty(self):
        assert _parse_subject_workdir("[MYPROJECT] task", {}) == ""

    def test_matching_tag_returns_path(self):
        result = _parse_subject_workdir(
            "[MYPROJECT] fix bug", {"MYPROJECT": "/some/path"}
        )
        assert result == "/some/path"

    def test_agent_tag_skipped(self):
        result = _parse_subject_workdir("[AGENT] task", {"AGENT": "/agent/path"})
        assert result == ""

    def test_agent_tag_skipped_falls_through(self):
        result = _parse_subject_workdir(
            "[AGENT][MYPROJECT] task", {"AGENT": "/a", "MYPROJECT": "/cs"}
        )
        assert result == "/cs"

    def test_case_insensitive_matching(self):
        result = _parse_subject_workdir("[myproject] task", {"MYPROJECT": "/path"})
        assert result == "/path"

    def test_first_matching_tag_wins(self):
        result = _parse_subject_workdir(
            "[FOO][BAR] task", {"FOO": "/foo", "BAR": "/bar"}
        )
        assert result == "/foo"

    def test_unknown_tag_returns_empty(self):
        result = _parse_subject_workdir("[UNKNOWN] task", {"MYPROJECT": "/path"})
        assert result == ""


class TestResolveWorkDir:
    def test_description_has_highest_priority(self, tmp_path):
        # Create a real directory under an allowed base so description directive works
        base = tmp_path / "base"
        desc_dir = base / "desc"
        desc_dir.mkdir(parents=True)

        result = resolve_work_dir(
            subject="[MYPROJECT] task",
            description=f"work_dir: {desc_dir}",
            work_dirs={"MYPROJECT": str(base)},
            default_work_dir=str(tmp_path / "default"),
            project_root=str(tmp_path / "root"),
        )
        assert result == str(desc_dir)

    def test_subject_tag_over_default_and_root(self, tmp_path):
        result = resolve_work_dir(
            subject="[MYPROJECT] task",
            description="",
            work_dirs={"MYPROJECT": str(tmp_path / "cs")},
            default_work_dir=str(tmp_path / "default"),
            project_root=str(tmp_path / "root"),
        )
        assert result == str(tmp_path / "cs")

    def test_default_work_dir_used_when_no_directive_or_subject(self, tmp_path):
        default = str(tmp_path / "default")
        result = resolve_work_dir(
            subject="[UNKNOWN] task",
            description="",
            work_dirs={"MYPROJECT": str(tmp_path / "cs")},
            default_work_dir=default,
            project_root=str(tmp_path / "root"),
        )
        assert result == default

    def test_project_root_is_final_fallback(self, tmp_path):
        root = str(tmp_path / "root")
        result = resolve_work_dir(
            subject="plain task",
            description="",
            work_dirs={},
            default_work_dir="",
            project_root=root,
        )
        assert result == root

    def test_full_priority_chain(self, tmp_path):
        """Verify all four levels: description > subject > default > root."""
        base = tmp_path / "base"
        desc_dir = base / "desc"
        desc_dir.mkdir(parents=True)
        cs_path = str(base)
        default = str(tmp_path / "default")
        root = str(tmp_path / "root")
        work_dirs = {"MYPROJECT": cs_path}

        # Level 1: description wins
        assert resolve_work_dir(
            "[MYPROJECT] t", f"work_dir: {desc_dir}", work_dirs, default, root
        ) == str(desc_dir)

        # Level 2: subject wins when no description directive
        assert (
            resolve_work_dir("[MYPROJECT] t", "", work_dirs, default, root) == cs_path
        )

        # Level 3: default wins when no description or subject match
        assert resolve_work_dir("[UNKNOWN] t", "", work_dirs, default, root) == default

        # Level 4: root as final fallback
        assert resolve_work_dir("plain", "", {}, "", root) == root
