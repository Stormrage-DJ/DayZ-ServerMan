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


def _say(message: str, cli: bool) -> None:
    """Print one bootstrap message: on stderr in CLI mode, so stdout holds only the command result."""
    print(message, file=sys.stderr if cli else sys.stdout)


def _ensure_runtime(cli: bool = False) -> None:
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
        _say("Creating the DayZ-ServerMan local Python environment...", cli)
        venv.EnvBuilder(with_pip=True).create(VENV_ROOT)

    # Reinstall dependencies whenever requirements.txt changes
    digest = hashlib.sha256(REQUIREMENTS_FILE.read_bytes()).hexdigest()
    installed_digest = (
        REQUIREMENTS_STAMP.read_text(encoding="ascii").strip()
        if REQUIREMENTS_STAMP.is_file()
        else ""
    )
    if installed_digest != digest:
        _say("Installing DayZ-ServerMan Python dependencies...", cli)
        # In CLI mode the pip output goes to stderr as well
        install_output = {"stdout": sys.stderr} if cli else {}
        subprocess.run(
            [str(local_python), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)],
            check=True,
            **install_output,
        )
        REQUIREMENTS_STAMP.write_text(digest + "\n", encoding="ascii")

    # Relaunch through the local interpreter so dependencies are importable
    if Path(sys.executable).resolve() != local_python.resolve():
        command = [str(local_python), str(Path(__file__).resolve()), *sys.argv[1:]]
        exit_code = _relaunch_cli(command) if cli else subprocess.call(command)
        raise SystemExit(exit_code)


def _relaunch_cli(command: list[str]) -> int:
    """Run the CLI child and wait for it; Ctrl+C reaches the child through the shared console only."""
    process = subprocess.Popen(command)
    # The parent never ends before the child, so a Ctrl+C here only keeps waiting
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            continue


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
    """Route `--cli` to the command line, else parse hidden smoke flags and launch the window."""
    arguments_list = sys.argv[1:] if argv is None else list(argv)
    # The command line is chosen only by `--cli` as the first argument
    if arguments_list and arguments_list[0] == "--cli":
        return run_cli(arguments_list[1:])
    parser = argparse.ArgumentParser(description="Start DayZ-ServerMan")
    parser.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--hold-seconds", type=float, default=0.0, help=argparse.SUPPRESS)
    arguments = parser.parse_args(arguments_list)
    # Prepare the runtime before importing the application package
    _ensure_runtime()
    _prepare_imports()

    # Import the application only after the runtime bootstrap
    from dayz_serverman.host.gui_main import run_window

    # Take the instance lock, build the application graph and guarantee shutdown on exit
    return run_window(
        APPLICATION_ROOT,
        frontend_root=FRONTEND_ROOT,
        hidden=arguments.smoke,
        smoke=arguments.smoke,
        hold_seconds=arguments.hold_seconds,
    )


def run_cli(arguments: list[str]) -> int:
    """Bootstrap with messages on stderr, then run one CLI command for this folder."""
    # Prepare the runtime before importing the application package
    _ensure_runtime(cli=True)
    _prepare_imports()

    # Import the command line only after the runtime bootstrap
    from dayz_serverman.cli import main as cli_main

    # Run the command against the manager root beside this starter
    return cli_main(arguments, application_root=APPLICATION_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
