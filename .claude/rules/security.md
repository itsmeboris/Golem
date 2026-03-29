# Security Patterns

## Path Traversal Prevention
- Always `resolve()` + `relative_to()` to validate paths stay within allowed directories
- Use `O_NOFOLLOW` for atomic opens to prevent symlink attacks
- Never use user-controlled paths in `open()` without validation
- Check both the resolved path AND the original path (prevent double-encoding attacks)

```python
# GOOD
resolved = Path(user_path).resolve()
resolved.relative_to(allowed_base)  # raises ValueError if outside

# GOOD — atomic open with symlink protection
fd = os.open(str(resolved), os.O_RDONLY | os.O_NOFOLLOW)
```

## Subprocess Sanitization
- Always use `preexec_fn=make_sandbox_preexec()` from `golem/sandbox.py`
- Always set `timeout` on subprocess calls
- Never pass user input directly into shell commands
- Use list form of commands, not string form (no `shell=True`)
- Escape or validate any user-controlled arguments

```python
# GOOD
subprocess.run(
    ["git", "diff", "--name-only", validated_ref],
    timeout=30,
    capture_output=True,
    preexec_fn=make_sandbox_preexec(),
)

# BAD — shell injection risk
subprocess.run(f"git diff {user_input}", shell=True)
```

## XSS Prevention in Dashboard
- Escape ALL dynamic content before inserting into HTML
- The `esc()` function must escape: `&`, `<`, `>`, `"`, `'` (all five HTML-significant chars)
- Never use string interpolation in `onclick` handlers with user data — use `data-` attributes + `addEventListener`
- Validate that no `alert()` calls remain (use `showToast()` instead)

```javascript
// BAD — single-quote injection in onclick
`<td onclick="fn('${esc(userValue)}')">`

// GOOD — data attribute + event delegation
`<td data-value="${esc(userValue)}">`
// then: element.addEventListener('click', (e) => fn(e.target.dataset.value))
```

## Secret Handling
- API keys from environment variables only, never hardcoded
- Never log secrets — mask in error messages
- Use `_require_api_key()` for all API endpoints (both reads and mutations)
- Store keys in `~/.golem/` config, not in repo

## Input Validation
- Validate at system boundaries (user input, external APIs, issue tracker data)
- Use `TypedDict` contracts from `golem/types.py` for structured data
- Sanitize issue tracker input before interpolation into prompts
- Reject inputs that don't match expected patterns rather than trying to clean them

## CORS and Rate Limiting
- CORS restricted to localhost origins only (`127\.0\.0\.1|localhost`)
- Rate limiting on all mutation endpoints (sliding window, 10 req/min)
- API key auth on ALL `/api/*` routes (reads and writes)

## Dependency Security
- Pin dependencies in `pyproject.toml`
- No `eval()`, `exec()`, or `__import__()` on user-controlled input
- Validate MCP tool schemas at registration time (`validate_tool_schema()`)
