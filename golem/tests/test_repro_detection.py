"""Tests for reproduction test detection in bug-fix diffs."""

import pytest

from golem.validation import detect_reproduction_test


class TestReproductionTestDetection:
    def test_detects_repro_test_in_diff(self):
        """A diff containing a new test function with 'repro' or 'regression' is detected."""
        diff = '''\
+++ b/golem/tests/test_auth.py
@@ -0,0 +1,10 @@
+def test_repro_sso_500_error():
+    """Reproduce: SSO login returns 500."""
+    result = auth_endpoint(method="SSO", token="valid")
+    assert result.status_code == 200
'''
        assert detect_reproduction_test(diff) is True

    def test_detects_regression_test(self):
        """A test with 'regression' in the name counts."""
        diff = """\
+++ b/golem/tests/test_login.py
@@ -0,0 +1,5 @@
+def test_regression_login_timeout():
+    result = login(timeout=0)
+    assert result is not None
"""
        assert detect_reproduction_test(diff) is True

    def test_no_repro_test_detected(self):
        """A diff with no reproduction test returns False."""
        diff = """\
+++ b/golem/auth.py
@@ -10,3 +10,5 @@
+    if token is None:
+        return error_response(400)
"""
        assert detect_reproduction_test(diff) is False

    def test_any_new_test_in_test_file_counts(self):
        """Any new test function added in a test file counts as potential repro test."""
        diff = """\
+++ b/golem/tests/test_auth.py
@@ -0,0 +1,5 @@
+def test_sso_returns_200_with_valid_token():
+    result = auth_endpoint(method="SSO", token="valid")
+    assert result.status_code == 200
"""
        assert detect_reproduction_test(diff) is True

    @pytest.mark.parametrize("diff_text", ["", None])
    def test_empty_or_none_diff_returns_false(self, diff_text):
        """An empty or None diff returns False."""
        assert detect_reproduction_test(diff_text) is False

    def test_test_file_header_but_no_new_test_function(self):
        """A test file is modified but only non-test lines are added."""
        diff = """\
+++ b/golem/tests/test_auth.py
@@ -1,3 +1,5 @@
+import os
+CONSTANT = 42
"""
        assert detect_reproduction_test(diff) is False
