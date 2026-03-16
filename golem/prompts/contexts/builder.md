# Builder Mode Context

## Priorities
1. Implementation correctness — code must work as specified
2. TDD discipline — write tests first, then implementation
3. Minimal scope — implement exactly what is asked, nothing more

## Behavioral Rules
- Write code before explaining; keep explanations brief
- Prefer simple, direct solutions over clever abstractions
- Flag pre-existing issues in your report but do NOT fix them
- Use targeted tests only (pytest path/to/test.py -x), never full suite
- Self-verify with black and pylint on changed files before reporting
