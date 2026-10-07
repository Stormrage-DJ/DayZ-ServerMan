"""The command line of DayZ-ServerMan: `DayZ-ServerMan.py --cli <noun> <verb> [options]` (A1, design 6).

The CLI calls every action through the bridge facade, as the window does. It never imports
`host` or pywebview, and it adds no network code (criterion 17).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from .interrupts import Interrupts
from .runner import run


def main(arguments: Sequence[str], application_root: Path | None = None) -> int:
    """Run one command line and return its exit code (0 to 6); Ctrl+C only sets a flag (6.7)."""
    interrupts = Interrupts()
    interrupts.install()
    try:
        # A character that the console cannot show must not fail the command
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(errors="replace")
        return run(list(arguments), application_root, stdout=sys.stdout, stderr=sys.stderr,
                   interrupts=interrupts, stdin=sys.stdin)
    finally:
        interrupts.restore()
