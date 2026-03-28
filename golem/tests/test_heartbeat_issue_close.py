"""Tests for heartbeat issue-close wiring via issue_mode.

The old single-repo submit paths (_submit_single, _submit_promoted, _submit_batch)
have been moved to HeartbeatManager _for_worker methods.  issue_mode is tested via
the _submit_single_for_worker and _submit_promoted_for_worker tests in
test_heartbeat.py::TestMultiRepoScheduler.

This file is preserved as a placeholder; no per-repo submission tests remain here.
"""
