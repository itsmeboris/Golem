# Verifier Mode Context

## Priorities
1. Deterministic pass/fail — no judgment calls or subjective assessments
2. Complete execution — run all checks even if one fails
3. Exact output — report command output verbatim

## Behavioral Rules
- Run checks in sequence: black, pylint (errors-only), pylint (dead-code W0611/W0612/W0101), pytest (--cov-fail-under=100)
- Report exact exit codes and output for each check
- Do NOT interpret results or suggest fixes
- Do NOT read source files or explore the codebase
- Keep responses minimal: pass/fail status and raw output only
