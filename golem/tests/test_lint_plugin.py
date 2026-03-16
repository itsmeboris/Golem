# pylint: disable=missing-class-docstring,missing-function-docstring
"""Tests for the custom pylint test-quality checker."""

import astroid
import pylint.testutils

from golem.lint.test_quality import TestQualityChecker


class TestE9901(pylint.testutils.CheckerTestCase):
    CHECKER_CLASS = TestQualityChecker

    def test_str_card_in_assign_flagged(self):
        """body_str = str(card) triggers E9901."""
        node = astroid.extract_node(
            """
        body_str = str(card)  #@
        """,
            module_name="golem.tests.test_example",
        )
        call_node = node.value
        with self.assertAddsMessages(
            pylint.testutils.MessageTest(
                msg_id="str-assertion-on-structured-data",
                node=call_node,
            ),
            ignore_position=True,
        ):
            self.checker.visit_call(call_node)

    def test_str_card_body_in_assign_flagged(self):
        """body_str = str(card["body"]) triggers E9901."""
        node = astroid.extract_node(
            """
        body_str = str(card["body"])  #@
        """,
            module_name="golem.tests.test_example",
        )
        call_node = node.value
        with self.assertAddsMessages(
            pylint.testutils.MessageTest(
                msg_id="str-assertion-on-structured-data",
                node=call_node,
            ),
            ignore_position=True,
        ):
            self.checker.visit_call(call_node)

    def test_str_on_non_structured_not_flagged(self):
        """str(count) should not trigger E9901."""
        node = astroid.extract_node(
            """
        x = str(count)  #@
        """,
            module_name="golem.tests.test_example",
        )
        call_node = node.value
        with self.assertNoMessages():
            self.checker.visit_call(call_node)

    def test_str_card_in_assert_flagged(self):
        """assert "x" in str(card) triggers E9901."""
        node = astroid.extract_node(
            """
        assert "x" in str(card)  #@
        """,
            module_name="golem.tests.test_example",
        )
        # The str() Call is nested inside the assert's Compare
        for child in node.nodes_of_class(astroid.nodes.Call):
            if isinstance(child.func, astroid.nodes.Name) and child.func.name == "str":
                call_node = child
                break
        with self.assertAddsMessages(
            pylint.testutils.MessageTest(
                msg_id="str-assertion-on-structured-data",
                node=call_node,
            ),
            ignore_position=True,
        ):
            self.checker.visit_call(call_node)

    def test_non_str_call_not_flagged(self):
        """len(card) should not trigger E9901."""
        node = astroid.extract_node(
            """
        x = len(card)  #@
        """,
            module_name="golem.tests.test_example",
        )
        call_node = node.value
        with self.assertNoMessages():
            self.checker.visit_call(call_node)

    def test_str_outside_test_file_not_flagged(self):
        """str(card) in non-test files should not trigger."""
        node = astroid.extract_node(
            """
        body_str = str(card)  #@
        """,
            module_name="golem.notifications",
        )
        call_node = node.value
        with self.assertNoMessages():
            self.checker.visit_call(call_node)


class TestW9902(pylint.testutils.CheckerTestCase):
    CHECKER_CLASS = TestQualityChecker

    def test_assertionless_test_flagged(self):
        """Test function with no assertion triggers W9902."""
        node = astroid.extract_node(
            """
        def test_something():  #@
            x = 1 + 1
        """,
            module_name="golem.tests.test_example",
        )
        with self.assertAddsMessages(
            pylint.testutils.MessageTest(
                msg_id="no-test-assertion",
                node=node,
            ),
            ignore_position=True,
        ):
            self.checker.visit_functiondef(node)

    def test_with_assert_not_flagged(self):
        """Test function with assert statement is clean."""
        node = astroid.extract_node(
            """
        def test_something():  #@
            assert 1 == 1
        """,
            module_name="golem.tests.test_example",
        )
        with self.assertNoMessages():
            self.checker.visit_functiondef(node)

    def test_with_pytest_raises_not_flagged(self):
        """Test function with pytest.raises is clean."""
        node = astroid.extract_node(
            """
        def test_something():  #@
            with pytest.raises(ValueError):
                do_thing()
        """,
            module_name="golem.tests.test_example",
        )
        with self.assertNoMessages():
            self.checker.visit_functiondef(node)

    def test_with_mock_assert_not_flagged(self):
        """Test function with mock.assert_called_once is clean."""
        node = astroid.extract_node(
            """
        def test_something():  #@
            mock.assert_called_once()
        """,
            module_name="golem.tests.test_example",
        )
        with self.assertNoMessages():
            self.checker.visit_functiondef(node)

    def test_non_test_function_not_flagged(self):
        """Helper function (not test_*) should not trigger."""
        node = astroid.extract_node(
            """
        def helper():  #@
            x = 1
        """,
            module_name="golem.tests.test_example",
        )
        with self.assertNoMessages():
            self.checker.visit_functiondef(node)

    def test_outside_test_file_not_flagged(self):
        """Test-like function outside test file should not trigger."""
        node = astroid.extract_node(
            """
        def test_something():  #@
            x = 1
        """,
            module_name="golem.flow",
        )
        with self.assertNoMessages():
            self.checker.visit_functiondef(node)


class TestRegister:
    def test_register_adds_checker(self):
        from unittest.mock import MagicMock

        from golem.lint.test_quality import register

        linter = MagicMock()
        register(linter)
        linter.register_checker.assert_called_once()
