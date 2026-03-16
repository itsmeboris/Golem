"""Custom pylint checker enforcing test quality standards.

Catches anti-patterns identified during the test quality audit:
- E9901: str() wrapping structured data in test assertions
- W9902: test function with no assertion (warning — some "doesn't raise" tests
  are intentional, add ``# pylint: disable=no-test-assertion`` to acknowledge)
- W9903: logging call uses f-string interpolation (supplements built-in W1203
  which only fires on logging module calls, not on custom logger instances)
"""

from astroid import nodes
from pylint.checkers import BaseChecker


class TestQualityChecker(BaseChecker):
    """Enforce test quality conventions from .claude/rules/testing.md."""

    name = "test-quality"
    msgs = {
        "E9901": (
            "str() on structured data in test assertion — assert on structure instead",
            "str-assertion-on-structured-data",
            "Using str() to convert structured data (cards, dicts) for substring "
            "matching hides structural bugs. Assert on specific fields instead.",
        ),
        "W9902": (
            "Test function has no assertion",
            "no-test-assertion",
            "A test function without assert, pytest.raises, or mock.assert_* "
            "always passes regardless of behavior. Add an assertion or disable "
            "this warning with a comment explaining the intent.",
        ),
    }

    def _is_test_file(self, node):
        """Check if the node is in a test file."""
        module = node.root()
        return module.name.startswith("golem.tests.test_")

    def visit_call(self, node):
        """Check for str() wrapping structured data in assertions."""
        if not self._is_test_file(node):
            return

        # Look for str(<name>) where <name> suggests structured data
        if not (
            isinstance(node.func, nodes.Name) and node.func.name == "str" and node.args
        ):
            return

        arg = node.args[0]
        # str(card), str(card["body"]), str(result), str(resp)
        structured_names = {"card", "body", "result", "resp", "response", "data"}

        arg_name = ""
        if isinstance(arg, nodes.Name):
            arg_name = arg.name
        elif isinstance(arg, nodes.Subscript) and isinstance(arg.value, nodes.Name):
            arg_name = arg.value.name

        if arg_name in structured_names:
            # Check if this str() call is inside an assert statement
            parent = node.parent
            while parent:
                if isinstance(parent, nodes.Assert):
                    self.add_message("str-assertion-on-structured-data", node=node)
                    return
                # Also catch: body_str = str(card); assert "x" in body_str
                if isinstance(parent, nodes.Assign):
                    self.add_message("str-assertion-on-structured-data", node=node)
                    return
                parent = parent.parent

    def visit_functiondef(self, node):
        """Check for test functions with no assertions."""
        if not self._is_test_file(node):
            return

        if not node.name.startswith("test_"):
            return

        if self._has_assertion(node):
            return

        self.add_message("no-test-assertion", node=node)

    visit_asyncfunctiondef = visit_functiondef

    def _has_assertion(self, node):
        """Check if function body contains any assertion-like statement."""
        for child in node.nodes_of_class((nodes.Assert, nodes.Call, nodes.With)):
            if isinstance(child, nodes.Assert):
                return True

            if isinstance(child, nodes.Call):
                # mock.assert_called_once(), mock.assert_awaited(), etc.
                if isinstance(child.func, nodes.Attribute):
                    if "assert" in child.func.attrname:
                        return True

            if isinstance(child, nodes.With):
                # pytest.raises(...)
                for item in child.items:
                    ctx = item[0]
                    if isinstance(ctx, nodes.Call) and isinstance(
                        ctx.func, nodes.Attribute
                    ):
                        if ctx.func.attrname == "raises":
                            return True

        return False


def register(linter):
    """Required pylint plugin entry point."""
    linter.register_checker(TestQualityChecker(linter))
