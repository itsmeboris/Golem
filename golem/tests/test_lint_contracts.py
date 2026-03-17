"""Tests for the AST-based return type extractor."""

import pytest

from golem.lint.contracts import extract_return_types


def _make_pkg(tmp_path, name="pkg"):
    """Create a named package directory under tmp_path and return (pkg_dir, module_prefix)."""
    pkg = tmp_path / name
    pkg.mkdir()
    return pkg, name


class TestExtractReturnTypes:
    def test_empty_directory_returns_empty_dict(self, tmp_path):
        pkg, _ = _make_pkg(tmp_path)
        result = extract_return_types(pkg)
        assert result == {}

    def test_directory_with_no_py_files_returns_empty_dict(self, tmp_path):
        pkg, _ = _make_pkg(tmp_path)
        (pkg / "readme.txt").write_text("nothing here")
        result = extract_return_types(pkg)
        assert result == {}

    def test_py_file_with_no_functions_returns_empty_dict(self, tmp_path):
        pkg, _ = _make_pkg(tmp_path)
        (pkg / "empty.py").write_text("x = 1\n")
        result = extract_return_types(pkg)
        assert result == {}

    def test_public_function_with_annotation(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "utils.py").write_text("def greet(name: str) -> str:\n    return name\n")
        result = extract_return_types(pkg)
        assert result == {f"{prefix}.utils:greet": "str"}

    def test_public_function_without_annotation(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "utils.py").write_text("def greet(name):\n    return name\n")
        result = extract_return_types(pkg)
        assert result == {f"{prefix}.utils:greet": None}

    def test_private_function_excluded(self, tmp_path):
        pkg, _ = _make_pkg(tmp_path)
        (pkg / "utils.py").write_text("def _helper() -> int:\n    return 1\n")
        result = extract_return_types(pkg)
        assert result == {}

    def test_mix_public_and_private(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "utils.py").write_text(
            "def public() -> bool:\n    return True\n\ndef _private() -> int:\n    return 1\n"
        )
        result = extract_return_types(pkg)
        assert result == {f"{prefix}.utils:public": "bool"}

    def test_module_path_from_nested_package(self, tmp_path):
        pkg = tmp_path / "golem"
        pkg.mkdir()
        (pkg / "utils.py").write_text("def foo() -> int:\n    return 1\n")
        result = extract_return_types(pkg)
        assert result == {"golem.utils:foo": "int"}

    def test_deeply_nested_module(self, tmp_path):
        nested = tmp_path / "golem" / "lint"
        nested.mkdir(parents=True)
        (nested / "contracts.py").write_text("def bar() -> str:\n    return ''\n")
        result = extract_return_types(tmp_path / "golem")
        assert result == {"golem.lint.contracts:bar": "str"}

    def test_init_py_included(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "__init__.py").write_text("def setup() -> None:\n    pass\n")
        result = extract_return_types(pkg)
        assert result == {f"{prefix}:setup": "None"}

    def test_class_method_public(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "models.py").write_text(
            "class MyClass:\n    def method(self) -> str:\n        return ''\n"
        )
        result = extract_return_types(pkg)
        assert result == {f"{prefix}.models:MyClass.method": "str"}

    def test_class_method_private_excluded(self, tmp_path):
        pkg, _ = _make_pkg(tmp_path)
        (pkg / "models.py").write_text(
            "class MyClass:\n    def _private(self) -> int:\n        return 1\n"
        )
        result = extract_return_types(pkg)
        assert result == {}

    def test_class_methods_mix(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "models.py").write_text(
            "class MyClass:\n"
            "    def public(self) -> bool:\n        return True\n"
            "    def _private(self) -> int:\n        return 1\n"
        )
        result = extract_return_types(pkg)
        assert result == {f"{prefix}.models:MyClass.public": "bool"}

    def test_nested_class(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "models.py").write_text(
            "class Outer:\n"
            "    class Inner:\n"
            "        def method(self) -> str:\n            return ''\n"
        )
        result = extract_return_types(pkg)
        assert result == {f"{prefix}.models:Outer.Inner.method": "str"}

    def test_syntax_error_file_skipped_gracefully(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "bad.py").write_text("def broken(:\n    pass\n")
        (pkg / "good.py").write_text("def ok() -> int:\n    return 1\n")
        result = extract_return_types(pkg)
        assert result == {f"{prefix}.good:ok": "int"}

    def test_async_function_included(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "tasks.py").write_text("async def run() -> None:\n    pass\n")
        result = extract_return_types(pkg)
        assert result == {f"{prefix}.tasks:run": "None"}

    def test_async_private_function_excluded(self, tmp_path):
        pkg, _ = _make_pkg(tmp_path)
        (pkg / "tasks.py").write_text("async def _run() -> None:\n    pass\n")
        result = extract_return_types(pkg)
        assert result == {}

    def test_complex_return_type(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "utils.py").write_text(
            "def get_data() -> dict[str, list[int]]:\n    return {}\n"
        )
        result = extract_return_types(pkg)
        assert result == {f"{prefix}.utils:get_data": "dict[str, list[int]]"}

    def test_optional_return_type(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "utils.py").write_text(
            "from typing import Optional\n"
            "def find(x: int) -> Optional[str]:\n    return None\n"
        )
        result = extract_return_types(pkg)
        assert result == {f"{prefix}.utils:find": "Optional[str]"}

    def test_multiple_files(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "a.py").write_text("def func_a() -> int:\n    return 1\n")
        (pkg / "b.py").write_text("def func_b() -> str:\n    return ''\n")
        result = extract_return_types(pkg)
        assert result == {
            f"{prefix}.a:func_a": "int",
            f"{prefix}.b:func_b": "str",
        }

    def test_static_method_in_class(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "models.py").write_text(
            "class MyClass:\n"
            "    @staticmethod\n"
            "    def create() -> 'MyClass':\n        return MyClass()\n"
        )
        result = extract_return_types(pkg)
        assert result == {f"{prefix}.models:MyClass.create": "'MyClass'"}

    def test_classmethod_in_class(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "models.py").write_text(
            "class MyClass:\n"
            "    @classmethod\n"
            "    def from_dict(cls, d: dict) -> 'MyClass':\n        return cls()\n"
        )
        result = extract_return_types(pkg)
        assert result == {f"{prefix}.models:MyClass.from_dict": "'MyClass'"}

    @pytest.mark.parametrize(
        "annotation,expected",
        [
            ("int", "int"),
            ("str", "str"),
            ("bool", "bool"),
            ("None", "None"),
            ("list[int]", "list[int]"),
            ("tuple[str, int]", "tuple[str, int]"),
        ],
    )
    def test_various_annotations(self, tmp_path, annotation, expected):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "utils.py").write_text(f"def func() -> {annotation}:\n    pass\n")
        result = extract_return_types(pkg)
        assert result == {f"{prefix}.utils:func": expected}

    def test_multiple_functions_in_file(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "utils.py").write_text(
            "def first() -> int:\n    return 1\n\ndef second() -> str:\n    return ''\n"
        )
        result = extract_return_types(pkg)
        assert result == {
            f"{prefix}.utils:first": "int",
            f"{prefix}.utils:second": "str",
        }

    def test_function_at_module_level_and_in_class(self, tmp_path):
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "mixed.py").write_text(
            "def standalone() -> int:\n    return 1\n\n"
            "class Container:\n    def method(self) -> str:\n        return ''\n"
        )
        result = extract_return_types(pkg)
        assert result == {
            f"{prefix}.mixed:standalone": "int",
            f"{prefix}.mixed:Container.method": "str",
        }
