"""Test package bootstrap for the repository-contained runnable source."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOT = PROJECT_ROOT / "runnable" / "src" / "python"
# Make the runnable source tree importable for every test module
sys.path.insert(0, str(PYTHON_ROOT))

