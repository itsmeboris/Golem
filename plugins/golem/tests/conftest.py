"""Shared fixtures for golem plugin companion script tests."""

import sys
from pathlib import Path

# Ensure the scripts/lib directory is on the path for imports
_scripts_dir = Path(__file__).parent.parent / "scripts"
_lib_dir = _scripts_dir / "lib"

if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
