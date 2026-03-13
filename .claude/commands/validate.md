---
description: Run the full Golem validation pipeline (black, pylint, pytest with 100% coverage)
---

Run these three commands in sequence and report results:

1. `black --check .`
2. `pylint --errors-only golem/`
3. `pytest golem/tests/ -x -q --cov=golem --cov-fail-under=100`

If any command fails, report the exact error output. Do NOT attempt to fix anything - just report results.

Format output as:
- **black**: PASS/FAIL
- **pylint**: PASS/FAIL
- **pytest**: PASS/FAIL (with coverage %)
