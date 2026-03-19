"""Tests for the unused-exports cross-module lint check."""

from pathlib import Path

import pytest

from golem.lint.unused_exports import check_unused_exports


def _make_pkg(tmp_path: Path, name: str = "pkg") -> Path:
    """Create a minimal package directory under tmp_path and return the package dir."""
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    return pkg


class TestCheckUnusedExportsEmptyAndTrivial:
    def test_empty_directory_returns_empty_list(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        result = check_unused_exports(pkg)
        assert result == []

    def test_directory_with_only_init_returns_empty_list(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        result = check_unused_exports(pkg)
        assert result == []

    def test_no_py_files_returns_empty_list(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "readme.txt").write_text("nothing")
        result = check_unused_exports(pkg)
        assert result == []


class TestCheckUnusedExportsViolations:
    def test_unused_public_class_flagged(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "models.py").write_text("class MyModel:\n    pass\n")
        violations = check_unused_exports(pkg)
        assert len(violations) == 1
        v = violations[0]
        assert v["name"] == "MyModel"
        assert v["kind"] == "class"
        assert "MyModel" in v["message"]
        assert v["line"] == 1

    def test_unused_public_function_flagged(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "utils.py").write_text("def helper():\n    pass\n")
        violations = check_unused_exports(pkg)
        assert len(violations) == 1
        v = violations[0]
        assert v["name"] == "helper"
        assert v["kind"] == "function"
        assert "helper" in v["message"]
        assert v["line"] == 1

    def test_unused_async_function_flagged(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "async_utils.py").write_text("async def run_task():\n    pass\n")
        violations = check_unused_exports(pkg)
        assert len(violations) == 1
        v = violations[0]
        assert v["name"] == "run_task"
        assert v["kind"] == "function"

    def test_violation_file_path_is_relative(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "models.py").write_text("class Orphan:\n    pass\n")
        violations = check_unused_exports(pkg)
        assert len(violations) == 1
        # file should be a relative path, not an absolute path
        assert not Path(violations[0]["file"]).is_absolute()

    def test_multiple_unused_exports_in_one_file(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "stuff.py").write_text(
            "class Alpha:\n    pass\n\nclass Beta:\n    pass\n\ndef gamma():\n    pass\n"
        )
        violations = check_unused_exports(pkg)
        names = {v["name"] for v in violations}
        assert names == {"Alpha", "Beta", "gamma"}


class TestCheckUnusedExportsNoViolations:
    def test_class_imported_by_other_file_not_flagged(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "models.py").write_text("class MyModel:\n    pass\n")
        (pkg / "consumer.py").write_text(
            "from pkg.models import MyModel\n\ndef use():\n    return MyModel()\n"
        )
        violations = check_unused_exports(pkg)
        # MyModel is imported by consumer.py, so only the function `use` in
        # consumer.py may appear; MyModel must NOT be in violations
        names = {v["name"] for v in violations}
        assert "MyModel" not in names

    def test_function_imported_by_other_file_not_flagged(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "utils.py").write_text("def helper():\n    pass\n")
        (pkg / "main.py").write_text("from pkg.utils import helper\n")
        violations = check_unused_exports(pkg)
        names = {v["name"] for v in violations}
        assert "helper" not in names

    def test_all_exports_used_returns_empty_list(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "types.py").write_text("class Config:\n    pass\n")
        (pkg / "runner.py").write_text(
            "from pkg.types import Config\n\ndef run(c: Config):\n    pass\n"
        )
        (pkg / "caller.py").write_text("from pkg.runner import run\n")
        violations = check_unused_exports(pkg)
        assert violations == []


class TestCheckUnusedExportsExclusions:
    @pytest.mark.parametrize(
        "name, code",
        [
            ("_private_func", "def _private_func():\n    pass\n"),
            ("_PrivateClass", "class _PrivateClass:\n    pass\n"),
            ("__init__", "def __init__(self):\n    pass\n"),
            ("__str__", "def __str__(self):\n    return ''\n"),
            ("__all__", "__all__ = ['something']\n"),
        ],
        ids=[
            "private_function",
            "private_class",
            "dunder_init",
            "dunder_str",
            "dunder_all_variable",
        ],
    )
    def test_non_public_definitions_not_flagged(self, tmp_path, name, code):
        pkg = _make_pkg(tmp_path)
        (pkg / "module.py").write_text(code)
        violations = check_unused_exports(pkg)
        names = {v["name"] for v in violations}
        assert name not in names

    def test_definitions_in_test_file_not_flagged(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        tests_dir = pkg / "tests"
        tests_dir.mkdir()
        (tests_dir / "__init__.py").write_text("")
        (tests_dir / "test_something.py").write_text(
            "class TestFoo:\n    pass\n\ndef test_bar():\n    pass\n"
        )
        violations = check_unused_exports(pkg)
        names = {v["name"] for v in violations}
        assert "TestFoo" not in names
        assert "test_bar" not in names

    def test_definitions_in_init_py_not_flagged(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "__init__.py").write_text("class PackageAPI:\n    pass\n")
        violations = check_unused_exports(pkg)
        names = {v["name"] for v in violations}
        assert "PackageAPI" not in names

    def test_name_in_dunder_all_not_flagged(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "exports.py").write_text(
            "__all__ = ['PublicThing']\n\nclass PublicThing:\n    pass\n\nclass Internal:\n    pass\n"
        )
        violations = check_unused_exports(pkg)
        names = {v["name"] for v in violations}
        # PublicThing is in __all__, so NOT flagged even if not imported
        assert "PublicThing" not in names
        # Internal is not in __all__ and not imported, so IS flagged
        assert "Internal" in names


class TestCheckUnusedExportsImportForms:
    def test_from_module_import_name_counts_as_import(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "defs.py").write_text("class Processor:\n    pass\n")
        (pkg / "app.py").write_text("from pkg.defs import Processor\n")
        violations = check_unused_exports(pkg)
        names = {v["name"] for v in violations}
        assert "Processor" not in names

    def test_import_name_counts_as_import(self, tmp_path):
        """Plain `import Name` also counts."""
        pkg = _make_pkg(tmp_path)
        (pkg / "defs.py").write_text("class Processor:\n    pass\n")
        (pkg / "app.py").write_text("import Processor\n")
        violations = check_unused_exports(pkg)
        names = {v["name"] for v in violations}
        assert "Processor" not in names

    def test_import_in_test_file_counts(self, tmp_path):
        """Imports in test files still count — tests legitimately import the code."""
        pkg = _make_pkg(tmp_path)
        (pkg / "defs.py").write_text("class Widget:\n    pass\n")
        tests_dir = pkg / "tests"
        tests_dir.mkdir()
        (tests_dir / "__init__.py").write_text("")
        (tests_dir / "test_defs.py").write_text("from pkg.defs import Widget\n")
        violations = check_unused_exports(pkg)
        names = {v["name"] for v in violations}
        assert "Widget" not in names

    def test_import_in_init_counts(self, tmp_path):
        """Imports in __init__.py count."""
        pkg = _make_pkg(tmp_path)
        (pkg / "models.py").write_text("class Record:\n    pass\n")
        (pkg / "__init__.py").write_text("from pkg.models import Record\n")
        violations = check_unused_exports(pkg)
        names = {v["name"] for v in violations}
        assert "Record" not in names

    def test_import_as_alias_counts(self, tmp_path):
        """``import Name as Alias`` — the alias is collected, marking original as imported."""
        pkg = _make_pkg(tmp_path)
        (pkg / "defs.py").write_text("class Widget:\n    pass\n")
        # import Widget as W — both Widget and W end up in imported names
        (pkg / "app.py").write_text("import Widget as W\n")
        violations = check_unused_exports(pkg)
        names = {v["name"] for v in violations}
        assert "Widget" not in names

    def test_from_import_as_alias_counts(self, tmp_path):
        """``from module import Name as Alias`` — both name and alias collected."""
        pkg = _make_pkg(tmp_path)
        (pkg / "defs.py").write_text("class Processor:\n    pass\n")
        (pkg / "app.py").write_text("from pkg.defs import Processor as Proc\n")
        violations = check_unused_exports(pkg)
        names = {v["name"] for v in violations}
        assert "Processor" not in names


class TestCheckUnusedExportsViolationStructure:
    def test_violation_dict_has_required_keys(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "module.py").write_text("class Unused:\n    pass\n")
        violations = check_unused_exports(pkg)
        assert len(violations) == 1
        v = violations[0]
        required_keys = {"file", "line", "name", "kind", "message"}
        assert required_keys.issubset(set(v.keys()))

    def test_violation_line_number_is_correct(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "module.py").write_text(
            "# comment\n" "x = 1\n" "class LateClass:\n" "    pass\n"
        )
        violations = check_unused_exports(pkg)
        assert len(violations) == 1
        assert violations[0]["line"] == 3

    def test_violation_kind_class(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "module.py").write_text("class Klass:\n    pass\n")
        violations = check_unused_exports(pkg)
        assert violations[0]["kind"] == "class"

    def test_violation_kind_function(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "module.py").write_text("def func():\n    pass\n")
        violations = check_unused_exports(pkg)
        assert violations[0]["kind"] == "function"


class TestCheckUnusedExportsNestedDefinitions:
    def test_nested_class_in_function_not_flagged(self, tmp_path):
        """Only module-level definitions should be scanned."""
        pkg = _make_pkg(tmp_path)
        (pkg / "module.py").write_text(
            "def outer():\n    class Inner:\n        pass\n    return Inner\n"
        )
        violations = check_unused_exports(pkg)
        names = {v["name"] for v in violations}
        # `outer` is module-level and unused; `Inner` is nested and should NOT appear
        assert "outer" in names
        assert "Inner" not in names

    def test_nested_function_not_flagged(self, tmp_path):
        """Only module-level definitions scanned."""
        pkg = _make_pkg(tmp_path)
        (pkg / "module.py").write_text(
            "def outer():\n    def inner():\n        pass\n    return inner\n"
        )
        violations = check_unused_exports(pkg)
        names = {v["name"] for v in violations}
        assert "outer" in names
        assert "inner" not in names


class TestCheckUnusedExportsSyntaxError:
    def test_file_with_syntax_error_skipped_gracefully(self, tmp_path):
        """Files that cannot be parsed should be skipped without raising."""
        pkg = _make_pkg(tmp_path)
        (pkg / "broken.py").write_text("def bad syntax(\n")
        (pkg / "good.py").write_text("def good_fn():\n    pass\n")
        # Should not raise; broken.py is skipped
        violations = check_unused_exports(pkg)
        names = {v["name"] for v in violations}
        # good_fn is unused and should be flagged
        assert "good_fn" in names


class TestRealCodebaseHasZeroViolations:
    def test_check_unused_exports_zero_violations_on_real_codebase(self):
        """SPEC-5: check_unused_exports on the real golem package returns 0 violations."""
        golem_root = Path(__file__).resolve().parent.parent
        violations = check_unused_exports(golem_root)
        violation_messages = [v["message"] for v in violations]
        assert violations == [], "Unexpected unused exports:\n" + "\n".join(
            violation_messages
        )
