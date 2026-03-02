"""Error taxonomy for golem task execution.

Separates infrastructure failures (retryable automatically) from task
failures (not golem's fault) so retry budgets are spent correctly.
"""


class GolemError(Exception):
    """Base class for all golem errors."""

    retryable: bool = False


class InfrastructureError(GolemError):
    """Worktree creation, permission, CWD, event loop issues."""

    retryable = True


class TaskExecutionError(GolemError):
    """Agent failed its task (not golem's fault)."""

    retryable = False


class ValidationError(GolemError):
    """Validation agent failed to produce a verdict."""

    retryable = True
