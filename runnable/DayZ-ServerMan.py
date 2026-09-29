"""Start DayZ-ServerMan directly from its movable source folder."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path


# Paths resolved beside this starter so the folder stays movable
APPLICATION_ROOT = Path(__file__).resolve().parent
PYTHON_ROOT = APPLICATION_ROOT / "src" / "python"
FRONTEND_ROOT = APPLICATION_ROOT / "src" / "frontend"
VENV_ROOT = APPLICATION_ROOT / ".venv"
REQUIREMENTS_FILE = APPLICATION_ROOT / "requirements.txt"
REQUIREMENTS_STAMP = VENV_ROOT / ".requirements.sha256"


def _local_python() -> Path:
    """Return the interpreter path of the local environment."""
    return VENV_ROOT / "Scripts" / "python.exe"


def _ensure_runtime() -> None:
    """Create the local environment and relaunch through it when required."""
    # Skip bootstrap when the caller explicitly opts out
    if os.environ.get("DAYZ_SERVERMAN_SKIP_BOOTSTRAP") == "1":
        return
    # Refuse to run without the dependency list beside the starter
    if not REQUIREMENTS_FILE.is_file():
        raise RuntimeError("DayZ-ServerMan requires requirements.txt beside its starter.")

    # Create the local virtual environment when it is missing
    local_python = _local_python()
    if not local_python.is_file():
        print("Creating the DayZ-ServerMan local Python environment...")
        venv.EnvBuilder(with_pip=True).create(VENV_ROOT)

    # Reinstall dependencies whenever requirements.txt changes
    digest = hashlib.sha256(REQUIREMENTS_FILE.read_bytes()).hexdigest()
    installed_digest = (
        REQUIREMENTS_STAMP.read_text(encoding="ascii").strip()
        if REQUIREMENTS_STAMP.is_file()
        else ""
    )
    if installed_digest != digest:
        print("Installing DayZ-ServerMan Python dependencies...")
        subprocess.run(
            [str(local_python), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)],
            check=True,
        )
        REQUIREMENTS_STAMP.write_text(digest + "\n", encoding="ascii")

    # Relaunch through the local interpreter so dependencies are importable
    if Path(sys.executable).resolve() != local_python.resolve():
        exit_code = subprocess.call(
            [str(local_python), str(Path(__file__).resolve()), *sys.argv[1:]]
        )
        raise SystemExit(exit_code)


def _prepare_imports() -> None:
    """Check the bundled layout and expose the source package on the import path."""
    # Fail clearly when a required folder is missing
    if not PYTHON_ROOT.is_dir() or not FRONTEND_ROOT.is_dir():
        raise RuntimeError(
            "DayZ-ServerMan requires src\\python and src\\frontend beside its starter."
        )
    # Keep the movable folder free of bytecode caches
    sys.dont_write_bytecode = True
    # Make the dayz_serverman package importable from the source tree
    sys.path.insert(0, str(PYTHON_ROOT))


def main(argv: list[str] | None = None) -> int:
    """Parse hidden smoke flags, bootstrap the runtime, and launch the application."""
    parser = argparse.ArgumentParser(description="Start DayZ-ServerMan")
    parser.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--hold-seconds", type=float, default=0.0, help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    # Prepare the runtime before importing the application package
    _ensure_runtime()
    _prepare_imports()

    # Import the application only after the runtime bootstrap
    from dayz_serverman.composition import build_composition
    from dayz_serverman.host.runtime import launch_application

    # Build the application graph and guarantee shutdown on exit
    composition = build_composition(APPLICATION_ROOT)
    try:
        return launch_application(
            composition,
            frontend_root=FRONTEND_ROOT,
            hidden=arguments.smoke,
            smoke=arguments.smoke,
            hold_seconds=arguments.hold_seconds,
        )
    finally:
        composition.shutdown.request_shutdown()
        composition.shutdown.wait_for_close(5)


if __name__ == "__main__":
    raise SystemExit(main())
