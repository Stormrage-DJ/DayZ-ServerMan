"""Command-line entry point for the DayZ-ServerMan desktop application."""

from __future__ import annotations

import argparse
from pathlib import Path

from .composition import build_composition
from .host.runtime import launch_application


def main(argv: list[str] | None = None) -> int:
    """Run the desktop application and return its exit code."""
    parser = argparse.ArgumentParser(description="Start DayZ-ServerMan")
    parser.add_argument(
        "--manager-root",
        type=Path,
        help="Explicit portable root for a packaged application",
    )
    parser.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--hold-seconds", type=float, default=0.0, help=argparse.SUPPRESS)
    # Parse the requested options, including the hidden smoke switches
    arguments = parser.parse_args(argv)
    # Build the production object graph for the requested manager root
    composition = build_composition(arguments.manager_root)
    try:
        # Launch the WebView2 host and return its exit code
        return launch_application(
            composition,
            hidden=arguments.smoke,
            smoke=arguments.smoke,
            hold_seconds=arguments.hold_seconds,
        )
    finally:
        # Release owned resources even when the host fails to start
        composition.shutdown.request_shutdown()
        composition.shutdown.wait_for_close(5)


if __name__ == "__main__":
    raise SystemExit(main())
