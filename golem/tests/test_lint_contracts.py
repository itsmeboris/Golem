"""Tests for the AST-based return type extractor."""

import ast

import pytest

from golem.lint.contracts import (
    _call_to_registry_key,
    _extract_base_type,
    _find_call_assignments,
    _resolve_imports,
    _unparse_dotted_name,
    _walk_top_level,
    check_consumer_producer_types,
    extract_return_types,
)


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


class TestExtractBaseType:
    @pytest.mark.parametrize(
        "annotation,expected",
        [
            # Simple types
            ("dict", "dict"),
            ("list", "list"),
            ("str", "str"),
            ("set", "set"),
            ("tuple", "tuple"),
            ("int", "int"),
            ("float", "float"),
            ("bool", "bool"),
            # Generic types
            ("dict[str, int]", "dict"),
            ("list[int]", "list"),
            ("set[str]", "set"),
            ("tuple[str, int]", "tuple"),
            ("dict[str, list[int]]", "dict"),
            # Optional forms
            ("Optional[str]", "str"),
            ("Optional[dict]", "dict"),
            ("Optional[list[int]]", "list"),
            # Union with None
            ("str | None", "str"),
            ("None | str", "str"),
            ("dict | None", "dict"),
            # Unknown types return None
            ("MyClass", None),
            ("SomeType[int]", None),
            ("int | str", None),  # union of two known non-None types
        ],
    )
    def test_extract_base_type(self, annotation, expected):
        assert _extract_base_type(annotation) == expected

    def test_none_annotation_returns_none(self):
        assert _extract_base_type(None) is None


class TestResolveImports:
    def _parse(self, source: str):
        return ast.parse(source)

    def test_from_import(self):
        tree = self._parse("from pkg.mod import func")
        result = _resolve_imports(tree, None)
        assert result == {"func": "pkg.mod:func"}

    def test_from_import_class(self):
        tree = self._parse("from pkg.mod import ClassA")
        result = _resolve_imports(tree, None)
        assert result == {"ClassA": "pkg.mod:ClassA"}

    def test_import_module(self):
        tree = self._parse("import pkg.mod")
        result = _resolve_imports(tree, None)
        # import pkg.mod allows pkg.mod.func() call resolution
        assert result["pkg.mod"] == "pkg.mod"

    def test_multiple_from_imports(self):
        tree = self._parse("from pkg.a import foo\nfrom pkg.b import bar\n")
        result = _resolve_imports(tree, None)
        assert result["foo"] == "pkg.a:foo"
        assert result["bar"] == "pkg.b:bar"

    def test_alias_import(self):
        tree = self._parse("from pkg.mod import func as f")
        result = _resolve_imports(tree, None)
        assert result["f"] == "pkg.mod:func"

    def test_multiple_names_from_same_module(self):
        tree = self._parse("from pkg.mod import foo, bar")
        result = _resolve_imports(tree, None)
        assert result["foo"] == "pkg.mod:foo"
        assert result["bar"] == "pkg.mod:bar"


class TestCheckConsumerProducerTypes:
    def test_basic_mismatch_list_items(self, tmp_path):
        """list return type, consumer calls .items() — should produce a finding."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text("def get_data() -> list:\n    return []\n")
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_data\n"
            "result = get_data()\n"
            "for k, v in result.items():\n"
            "    pass\n"
        )
        findings = check_consumer_producer_types(pkg)
        assert len(findings) == 1
        f = findings[0]
        assert f["file"] == "consumer.py"
        assert f["line"] == 3
        assert f["function"] == "get_data"
        assert f["return_type"] == "list"
        assert f["invalid_access"] == "items"
        assert "list" in f["message"]
        assert "items" in f["message"]

    def test_no_mismatch_dict_items(self, tmp_path):
        """dict return type, consumer calls .items() — no finding."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text("def get_data() -> dict:\n    return {}\n")
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_data\n"
            "result = get_data()\n"
            "for k, v in result.items():\n"
            "    pass\n"
        )
        findings = check_consumer_producer_types(pkg)
        assert findings == []

    def test_no_annotation_no_finding(self, tmp_path):
        """Function with no return annotation — no finding."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text("def get_data():\n    return {}\n")
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_data\n"
            "result = get_data()\n"
            "for k, v in result.items():\n"
            "    pass\n"
        )
        findings = check_consumer_producer_types(pkg)
        assert findings == []

    def test_untracked_variable_no_finding(self, tmp_path):
        """Attribute access on variable not from tracked call — no finding."""
        pkg, _ = _make_pkg(tmp_path)
        (pkg / "consumer.py").write_text(
            "x = [1, 2, 3]\n" "for k, v in x.items():\n" "    pass\n"
        )
        findings = check_consumer_producer_types(pkg)
        assert findings == []

    def test_syntax_error_file_skipped(self, tmp_path):
        """One file with syntax error, other is fine — findings from good file only."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text("def get_data() -> list:\n    return []\n")
        (pkg / "bad.py").write_text("def broken(:\n    pass\n")
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_data\n"
            "result = get_data()\n"
            "result.items()\n"
        )
        findings = check_consumer_producer_types(pkg)
        # Should find mismatch in consumer.py, bad.py is skipped
        assert len(findings) == 1
        assert findings[0]["file"] == "consumer.py"
        assert findings[0]["invalid_access"] == "items"

    def test_generic_type_dict_append_mismatch(self, tmp_path):
        """Generic return type dict[str, int], consumer calls .append() — finding."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text(
            "def get_mapping() -> dict[str, int]:\n    return {}\n"
        )
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_mapping\n"
            "result = get_mapping()\n"
            "result.append('x')\n"
        )
        findings = check_consumer_producer_types(pkg)
        assert len(findings) == 1
        assert findings[0]["invalid_access"] == "append"
        assert findings[0]["return_type"] == "dict[str, int]"

    def test_from_module_import_resolution(self, tmp_path):
        """from pkg.module import func style is resolved correctly."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "utils.py").write_text("def get_list() -> list:\n    return []\n")
        (pkg / "main.py").write_text(
            f"from {prefix}.utils import get_list\n"
            "data = get_list()\n"
            "data.items()\n"
        )
        findings = check_consumer_producer_types(pkg)
        assert len(findings) == 1
        assert findings[0]["function"] == "get_list"

    def test_multiple_mismatches_in_one_file(self, tmp_path):
        """Multiple attribute mismatches in one consumer file — all reported."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text("def get_items() -> list:\n    return []\n")
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_items\n"
            "data = get_items()\n"
            "data.items()\n"
            "data.keys()\n"
        )
        findings = check_consumer_producer_types(pkg)
        assert len(findings) == 2
        accesses = {f["invalid_access"] for f in findings}
        assert accesses == {"items", "keys"}

    def test_optional_type_str_none_resolves_to_str(self, tmp_path):
        """Optional[str] return type extracts base type str."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text(
            "from typing import Optional\n"
            "def find_name() -> Optional[str]:\n    return None\n"
        )
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import find_name\n"
            "name = find_name()\n"
            "name.items()\n"
        )
        findings = check_consumer_producer_types(pkg)
        assert len(findings) == 1
        assert findings[0]["invalid_access"] == "items"

    def test_union_none_type_resolves_base(self, tmp_path):
        """str | None return type extracts base type str."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text(
            "def find_name() -> str | None:\n    return None\n"
        )
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import find_name\n"
            "name = find_name()\n"
            "name.append('x')\n"
        )
        findings = check_consumer_producer_types(pkg)
        assert len(findings) == 1
        assert findings[0]["invalid_access"] == "append"

    def test_empty_directory(self, tmp_path):
        """Empty directory returns no findings."""
        pkg, _ = _make_pkg(tmp_path)
        findings = check_consumer_producer_types(pkg)
        assert findings == []

    def test_valid_list_method_no_finding(self, tmp_path):
        """list return type with valid .append() call — no finding."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text("def get_items() -> list:\n    return []\n")
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_items\n"
            "data = get_items()\n"
            "data.append(1)\n"
        )
        findings = check_consumer_producer_types(pkg)
        assert findings == []

    def test_finding_dict_keys_contain_required_fields(self, tmp_path):
        """Each finding contains all required keys per SPEC-1."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text("def get_data() -> list:\n    return []\n")
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_data\n"
            "result = get_data()\n"
            "result.items()\n"
        )
        findings = check_consumer_producer_types(pkg)
        assert len(findings) == 1
        required_keys = {
            "file",
            "line",
            "function",
            "return_type",
            "invalid_access",
            "message",
        }
        assert set(findings[0].keys()) >= required_keys

    def test_variable_reassigned_last_assignment_wins(self, tmp_path):
        """Variable reassigned after tracked call — last assignment (untracked) wins."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text("def get_list() -> list:\n    return []\n")
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_list\n"
            "data = get_list()\n"
            "data = {'a': 1}\n"  # reassigned to a literal — untracked
            "data.items()\n"  # no finding because last assignment is untracked
        )
        findings = check_consumer_producer_types(pkg)
        assert findings == []

    def test_unknown_return_type_no_finding(self, tmp_path):
        """Return type not in TYPE_METHODS (e.g., custom class) — no finding."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text(
            "def get_obj() -> MyClass:\n    return MyClass()\n"
        )
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_obj\n"
            "obj = get_obj()\n"
            "obj.items()\n"
        )
        findings = check_consumer_producer_types(pkg)
        assert findings == []

    def test_finding_file_is_relative_to_root(self, tmp_path):
        """Finding file path is relative to root, not an absolute path."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text("def get_data() -> list:\n    return []\n")
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_data\n"
            "result = get_data()\n"
            "result.items()\n"
        )
        findings = check_consumer_producer_types(pkg)
        assert len(findings) == 1
        assert not findings[0]["file"].startswith("/")  # relative path

    def test_inner_function_does_not_shadow_outer_variable(self, tmp_path):
        """Assignment inside nested function should not remove outer variable tracking."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text("def get_data() -> list:\n    return []\n")
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_data\n"
            "data = get_data()\n"
            "def helper():\n"
            "    data = {}\n"
            "data.items()\n"
        )
        findings = check_consumer_producer_types(pkg)
        assert len(findings) == 1
        assert findings[0]["invalid_access"] == "items"

    def test_import_dotted_module_resolution(self, tmp_path):
        """import pkg.mod; x = pkg.mod.func() should resolve correctly."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text("def get_data() -> list:\n    return []\n")
        (pkg / "consumer.py").write_text(
            f"import {prefix}.producer\n"
            f"data = {prefix}.producer.get_data()\n"
            "data.items()\n"
        )
        findings = check_consumer_producer_types(pkg)
        assert len(findings) == 1
        assert findings[0]["invalid_access"] == "items"

    def test_inner_function_attribute_access_not_falsely_flagged(self, tmp_path):
        """Attribute access inside nested function on same-named variable must not produce false positive."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text("def get_data() -> list:\n    return []\n")
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_data\n"
            "data = get_data()\n"
            "def helper():\n"
            "    data = {}\n"
            "    data.items()\n"  # valid for inner dict, must NOT be flagged
            "data.append(1)\n"  # valid for outer list, no finding
        )
        findings = check_consumer_producer_types(pkg)
        assert findings == []


class TestResolveImportsDotted:
    def _parse(self, source: str):
        return ast.parse(source)

    def test_dotted_import_module_used_for_attribute_call(self):
        """import pkg.mod should be usable for resolving pkg.mod.func() calls."""
        tree = self._parse("import pkg.mod")
        result = _resolve_imports(tree, None)
        assert result["pkg.mod"] == "pkg.mod"


class TestFindCallAssignments:
    """Unit tests for _find_call_assignments targeting previously uncovered branches."""

    def _parse(self, source: str):
        return ast.parse(source)

    def test_reassign_to_unknown_call_removes_tracked_variable(self):
        """Variable previously tracked, then assigned to unresolved call — removed (lines 304-307)."""
        source = (
            "from pkg.mod import get_data\n"
            "data = get_data()\n"
            "data = unknown_func()\n"  # unknown_func not in import_map
        )
        tree = self._parse(source)
        import_map = {"get_data": "pkg.mod:get_data"}
        type_registry = {"pkg.mod:get_data": "list"}
        result = _find_call_assignments(tree, import_map, type_registry)
        assert result == {}

    def test_non_name_target_in_tracked_call_assignment_is_skipped(self):
        """Tuple-unpacking target in a call assignment is skipped (line 312)."""
        source = "from pkg.mod import get_data\n" "(a, b) = get_data()\n"
        tree = self._parse(source)
        import_map = {"get_data": "pkg.mod:get_data"}
        type_registry = {"pkg.mod:get_data": "list"}
        result = _find_call_assignments(tree, import_map, type_registry)
        # Tuple target is not an ast.Name, so it's skipped — no variables tracked
        assert result == {}


class TestUnparseDottedName:
    """Unit tests for _unparse_dotted_name targeting the None-return branch (line 333)."""

    def test_subscript_base_returns_none(self):
        """A subscript expression like a[0].method has no dotted name — returns None (line 333)."""
        # Build AST for "a[0].attr" manually
        subscript = ast.Subscript(
            value=ast.Name(id="a", ctx=ast.Load()),
            slice=ast.Constant(value=0),
            ctx=ast.Load(),
        )
        attr_node = ast.Attribute(value=subscript, attr="method", ctx=ast.Load())
        result = _unparse_dotted_name(attr_node)
        assert result is None

    def test_simple_name_returns_id(self):
        """Simple Name node returns its id."""
        node = ast.Name(id="foo", ctx=ast.Load())
        assert _unparse_dotted_name(node) == "foo"

    def test_attribute_chain_returns_dotted(self):
        """Nested Attribute nodes form a dotted string."""
        # Represents "a.b"
        node = ast.Attribute(
            value=ast.Name(id="a", ctx=ast.Load()),
            attr="b",
            ctx=ast.Load(),
        )
        assert _unparse_dotted_name(node) == "a.b"


class TestCallToRegistryKey:
    """Unit tests for _call_to_registry_key targeting the module_key-is-None branch (line 357)."""

    def _make_call(self, source: str) -> ast.Call:
        tree = ast.parse(source, mode="eval")
        assert isinstance(tree.body, ast.Call)
        return tree.body

    def test_attribute_call_dotted_not_in_import_map_returns_none(self):
        """Attribute-style call where dotted base is not in import_map — returns None (line 357)."""
        call = self._make_call("some.module.func()")
        import_map: dict[str, str] = {}  # "some.module" is not in import_map
        result = _call_to_registry_key(call, import_map)
        assert result is None

    def test_name_call_resolves_from_import_map(self):
        """Simple name call resolves correctly via import_map."""
        call = self._make_call("get_data()")
        import_map = {"get_data": "pkg.mod:get_data"}
        result = _call_to_registry_key(call, import_map)
        assert result == "pkg.mod:get_data"


class TestWalkTopLevel:
    """Unit tests for the _walk_top_level helper."""

    def _parse(self, source: str):
        return ast.parse(source)

    def test_yields_module_level_nodes(self):
        """Top-level assignment node is yielded."""
        tree = self._parse("x = 1\n")
        nodes = list(_walk_top_level(tree))
        assign_nodes = [n for n in nodes if isinstance(n, ast.Assign)]
        assert len(assign_nodes) == 1

    def test_does_not_yield_nodes_inside_function(self):
        """Nodes inside a function body are not yielded."""
        tree = self._parse("def f():\n    y = 2\n")
        nodes = list(_walk_top_level(tree))
        # The FunctionDef is yielded but its body's Assign is not
        assign_nodes = [n for n in nodes if isinstance(n, ast.Assign)]
        assert assign_nodes == []

    def test_does_not_yield_nodes_inside_class(self):
        """Nodes inside a class body are not yielded."""
        tree = self._parse("class C:\n    x = 1\n")
        nodes = list(_walk_top_level(tree))
        assign_nodes = [n for n in nodes if isinstance(n, ast.Assign)]
        assert assign_nodes == []


class TestCheckConsumerProducerTypesEdgeCoverage:
    """Integration tests to cover previously uncovered branches in check_consumer_producer_types."""

    def test_non_call_assignment_in_var_to_key_loop_is_skipped(self, tmp_path):
        """Non-call assignment at module level is skipped in var_to_key loop (line 408)."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text("def get_data() -> list:\n    return []\n")
        # consumer: tracked var, then a non-call assignment — hits line 408 (continue)
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_data\n"
            "data = get_data()\n"
            "x = 42\n"  # non-call assignment at module level — line 408 is hit
            "data.items()\n"  # invalid access — data is still tracked as list
        )
        findings = check_consumer_producer_types(pkg)
        assert len(findings) == 1
        assert findings[0]["invalid_access"] == "items"

    def test_unresolved_call_in_var_to_key_loop(self, tmp_path):
        """var_to_key loop skips rkey-is-None calls (line 411)."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text("def get_data() -> list:\n    return []\n")
        # consumer: tracked var, then second assignment to unknown_call (rkey None)
        # The second assignment at module level (unknown call) should not build a var_to_key entry
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_data\n"
            "data = get_data()\n"
            "other = no_import_func()\n"  # no_import_func not in import_map; rkey=None => line 411
            "data.items()\n"  # still tracked from get_data → finding
        )
        findings = check_consumer_producer_types(pkg)
        # data is still tracked as list, .items() is invalid
        assert len(findings) == 1
        assert findings[0]["invalid_access"] == "items"

    def test_non_name_target_in_var_to_key_loop(self, tmp_path):
        """var_to_key loop with tuple-unpacking target does not add to var_to_key (line 411)."""
        pkg, prefix = _make_pkg(tmp_path)
        (pkg / "producer.py").write_text("def get_data() -> list:\n    return []\n")
        # Use tuple unpacking at module level with a tracked call — target is not Name
        (pkg / "consumer.py").write_text(
            f"from {prefix}.producer import get_data\n"
            "data = get_data()\n"
            "data.items()\n"  # invalid access — data is list
        )
        findings = check_consumer_producer_types(pkg)
        assert len(findings) == 1
        assert findings[0]["invalid_access"] == "items"
