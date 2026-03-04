# TODO

- [ ] Mobile responsiveness for task dashboard (hamburger menu broken, layout not optimized for small screens, stats cards unreadable on mobile)
- [ ] Post-merge sanity check: after merge_and_cleanup, verify agent branch additions survive in the merged result — manual commits on master can silently overwrite agent work during rebase
- [ ] Antipattern rules for validation: add checks (in validation prompt or as static analysis) for traceback leaks in responses, private attribute access across modules, string-matching for control flow, etc.
- [ ] Task prompt template: instruct agents to use `@pytest.mark.parametrize` instead of duplicating test methods that differ by one variable
- [ ] Pipeline flow graph for task dashboard: add an interactive DAG/flow visualization (e.g. stage dependency graph) alongside or replacing the waterfall table, to show execution flow between pipeline stages
