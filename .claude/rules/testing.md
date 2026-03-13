# Testing Rules

- 100% test coverage required (`--cov-fail-under=100`)
- Use `@pytest.mark.parametrize` for test cases with repeated logic
- Tests go in `golem/tests/` mirroring source structure
- Mock at boundaries, not internals - verify behavior, not mock behavior
- Every bug fix must include a reproduction test
- Run full suite before claiming completion: `pytest golem/tests/ -x -q --cov=golem --cov-fail-under=100`
