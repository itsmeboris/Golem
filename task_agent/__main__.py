"""Allow ``python -m task_agent`` invocation."""

# pylint: disable=wrong-import-position

import os

os.environ.setdefault("GOLEM_DATA_DIR", "data")

from .cli import main  # noqa: E402

raise SystemExit(main())
