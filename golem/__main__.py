"""Allow ``python -m golem`` invocation."""

# pylint: disable=wrong-import-position

import shutil
import sys
from pathlib import Path

# Disable .pyc bytecode caching — on NFS, stale .pyc files cause
# AttributeError crashes when code is updated between daemon restarts.
sys.dont_write_bytecode = True

# Remove leftover __pycache__ dirs before any golem imports so Python
# never loads a stale .pyc compiled from an older source file.
for _cache in Path(__file__).resolve().parent.rglob("__pycache__"):
    shutil.rmtree(_cache, ignore_errors=True)

from .cli import main  # noqa: E402

raise SystemExit(main())
