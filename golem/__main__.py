"""Allow ``python -m golem`` invocation."""

# pylint: disable=wrong-import-position

import os

os.environ.setdefault("GOLEM_DATA_DIR", "data")

from .cli import main  # noqa: E402

raise SystemExit(main())
