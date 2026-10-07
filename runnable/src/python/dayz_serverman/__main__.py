"""Command-line entry point for the DayZ-ServerMan desktop application."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .host.gui_main import run_window


def main(argv: list[str] | None = None) -> int:
    """Run one CLI command after `--cli`, else the desktop application; return the exit code."""
    arguments_list = sys.argv[1:] if argv is None else list(argv)
    # Forward `--cli` the way the starter does, for the source layout
    if arguments_list and arguments_list[0] == "--cli":
        from .cli import main as cli_main

        return cli_main(arguments_list[1:], application_root=None)
    parser = argparse.ArgumentParser(description="Start DayZ-ServerMan")
    parser.add_argument(
        "--manager-root",
        type=Path,
        help="Explicit portable root for a packaged application",
    )
    parser.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--hold-seconds", type=float, default=0.0, help=argparse.SUPPRESS)
    # Parse the requested options, including the hidden smoke switches
    arguments = parser.parse_args(arguments_list)
    # Take the instance lock, build the object graph for the requested manager root and launch the host
    return run_window(
        arguments.manager_root,
        hidden=arguments.smoke,
        smoke=arguments.smoke,
        hold_seconds=arguments.hold_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
