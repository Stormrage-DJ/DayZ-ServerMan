"""Starter bootstrap tests for requirement checks and relaunch."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STARTER = PROJECT_ROOT / "runnable" / "DayZ-ServerMan.py"


def load_starter():
    """Load the starter module from its file path."""
    spec = importlib.util.spec_from_file_location("dayz_serverman_starter", STARTER)
    if spec is None or spec.loader is None:
        raise RuntimeError("starter module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StarterBootstrapTests(unittest.TestCase):
    """Starter runtime bootstrap contracts for dependencies."""
    def test_missing_requirements_is_rejected(self) -> None:
        """A missing requirements file fails the bootstrap."""
        module = load_starter()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.object(module, "REQUIREMENTS_FILE", root / "requirements.txt"),
                patch.object(module.os.environ, "get", return_value=None),
            ):
                # The bootstrap must fail when the requirements file is absent
                with self.assertRaisesRegex(RuntimeError, "requirements.txt"):
                    module._ensure_runtime()

    def test_changed_requirements_install_and_relaunch(self) -> None:
        """Changed requirements install into the venv and relaunch the starter."""
        module = load_starter()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            requirements = root / "requirements.txt"
            requirements.write_text("pywebview==6.2.1\n", encoding="utf-8")
            # Seed a virtual environment with a local interpreter binary
            venv_root = root / ".venv"
            local_python = venv_root / "Scripts" / "python.exe"
            local_python.parent.mkdir(parents=True)
            local_python.write_bytes(b"")
            stamp = venv_root / ".requirements.sha256"

            with (
                patch.object(module, "VENV_ROOT", venv_root),
                patch.object(module, "REQUIREMENTS_FILE", requirements),
                patch.object(module, "REQUIREMENTS_STAMP", stamp),
                patch.object(module, "_local_python", return_value=local_python),
                patch.object(module.os.environ, "get", return_value=None),
                patch.object(module.subprocess, "run") as install,
                patch.object(module.subprocess, "call", return_value=0) as relaunch,
            ):
                # The relaunch raises SystemExit with the returned exit code
                with self.assertRaises(SystemExit) as raised:
                    module._ensure_runtime()

            install.assert_called_once()
            relaunch.assert_called_once()
            self.assertEqual(0, raised.exception.code)
            self.assertEqual(64, len(stamp.read_text(encoding="ascii").strip()))


if __name__ == "__main__":
    unittest.main()
