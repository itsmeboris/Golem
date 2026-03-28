# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting a Vulnerability

If you discover a security vulnerability in Golem, please report it responsibly.

**Do not open a public issue.** Instead, email **[boris@itsmeboris.com](mailto:boris@itsmeboris.com)** with:

- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if you have one)

You should receive an acknowledgment within 48 hours. We will work with you to understand the issue and coordinate a fix before any public disclosure.

## Scope

Golem executes Claude CLI as a subprocess with full shell access in git worktrees. Security-relevant areas include:

- **Prompt injection** — malicious task descriptions that manipulate agent behavior
- **Configuration injection** — environment variable or config file manipulation
- **API authentication** — dashboard and control API access controls
- **Webhook URLs** — Slack/Teams notification endpoints stored in config

## Best Practices for Operators

- Set `api_key` in dashboard config to protect the submission API
- Set `admin_token` to restrict config and flow control endpoints
- Use `budget_per_task_usd` to limit blast radius of any single task
- Run the daemon in an isolated environment with limited filesystem access
- Review `AGENTS.md` periodically — it is auto-maintained by the learning loop
