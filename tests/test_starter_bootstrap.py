"""Starter bootstrap tests for requirement checks and relaunch."""
from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch


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


def seeded_runtime(root: Path) -> tuple[Path, Path, Path]:
    """Write a requirements file; return the requirements, the venv root and the stamp."""
    requirements = root / "requirements.txt"
    requirements.write_text("pywebview==6.2.1\n", encoding="utf-8")
    venv_root = root / ".venv"
    return requirements, venv_root, venv_root / ".requirements.sha256"


class StarterCliRoutingTests(unittest.TestCase):
    """Task 2.4: `--cli` routing, stderr bootstrap and the waiting relaunch parent (design 5.1)."""

    def _bootstrap_patches(self, module, root: Path, *, local_exists: bool) -> ExitStack:
        """Enter the patches of one bootstrap run in a temporary folder and return the stack."""
        requirements, venv_root, stamp = seeded_runtime(root)
        local_python = venv_root / "Scripts" / "python.exe"
        # Seed the interpreter only when the environment should already exist
        if local_exists:
            local_python.parent.mkdir(parents=True)
            local_python.write_bytes(b"")
        stack = ExitStack()
        stack.enter_context(patch.object(module, "VENV_ROOT", venv_root))
        stack.enter_context(patch.object(module, "REQUIREMENTS_FILE", requirements))
        stack.enter_context(patch.object(module, "REQUIREMENTS_STAMP", stamp))
        stack.enter_context(patch.object(module, "_local_python", return_value=local_python))
        stack.enter_context(patch.object(module.os.environ, "get", return_value=None))
        return stack

    def test_cli_routes_to_the_cli_entry_and_never_to_the_window(self) -> None:
        """`--cli` calls cli.main with the remaining arguments and APPLICATION_ROOT; no window path runs."""
        module = load_starter()
        fake_cli = types.ModuleType("dayz_serverman.cli")
        fake_cli.main = MagicMock(return_value=4)
        import dayz_serverman.host.gui_main as gui_main

        with (
            patch.dict(sys.modules, {"dayz_serverman.cli": fake_cli}),
            patch.object(module, "_ensure_runtime") as ensure,
            patch.object(module, "_prepare_imports"),
            patch.object(gui_main, "run_window") as window,
            patch.object(gui_main, "launch_application") as launch,
        ):
            code = module.main(["--cli", "server", "status", "--json"])
        # The CLI result is the process result
        self.assertEqual(code, 4)
        fake_cli.main.assert_called_once_with(
            ["server", "status", "--json"], application_root=module.APPLICATION_ROOT
        )
        ensure.assert_called_once_with(cli=True)
        window.assert_not_called()
        launch.assert_not_called()

    def test_cli_not_first_keeps_the_window_path(self) -> None:
        """`--cli` counts only as the first argument; elsewhere argparse refuses it as today."""
        module = load_starter()
        with patch.object(module, "run_cli") as run_cli, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                module.main(["--smoke", "--cli"])
        run_cli.assert_not_called()

    def test_cli_bootstrap_messages_and_pip_output_go_to_stderr(self) -> None:
        """In CLI mode the bootstrap prints nothing on stdout and pip writes to stderr."""
        module = load_starter()
        stdout, stderr = io.StringIO(), io.StringIO()
        process = MagicMock()
        process.wait.return_value = 0
        with tempfile.TemporaryDirectory() as temporary:
            with (
                self._bootstrap_patches(module, Path(temporary), local_exists=False),
                patch.object(module.venv, "EnvBuilder") as builder,
                patch.object(module.subprocess, "run") as install,
                patch.object(module.subprocess, "Popen", return_value=process),
                patch.object(module.subprocess, "call") as call,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                # The fake builder creates only the folder that holds the stamp
                builder.return_value.create.side_effect = lambda root: Path(root).mkdir(parents=True)
                with self.assertRaises(SystemExit):
                    module._ensure_runtime(cli=True)
        # Both messages are on stderr and the pip child writes its output to stderr
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Creating the DayZ-ServerMan local Python environment...", stderr.getvalue())
        self.assertIn("Installing DayZ-ServerMan Python dependencies...", stderr.getvalue())
        builder.assert_called_once()
        self.assertIs(install.call_args.kwargs["stdout"], stderr)
        call.assert_not_called()

    def test_cli_relaunch_parent_survives_ctrl_c_and_returns_the_child_code(self) -> None:
        """The waiting parent ignores KeyboardInterrupt and exits with the child's code."""
        module = load_starter()
        process = MagicMock()
        process.wait.side_effect = [KeyboardInterrupt(), KeyboardInterrupt(), 5]
        with tempfile.TemporaryDirectory() as temporary:
            with (
                self._bootstrap_patches(module, Path(temporary), local_exists=True),
                patch.object(module.subprocess, "run"),
                patch.object(module.subprocess, "Popen", return_value=process) as popen,
                patch.object(module.subprocess, "call") as call,
                patch.object(module.sys, "argv", ["DayZ-ServerMan.py", "--cli", "status"]),
                redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as raised:
                    module._ensure_runtime(cli=True)
        self.assertEqual(raised.exception.code, 5)
        self.assertEqual(process.wait.call_count, 3)
        # The relaunched child gets `--cli` again as its first argument
        self.assertEqual(popen.call_args.args[0][2:], ["--cli", "status"])
        call.assert_not_called()

    def test_gui_relaunch_still_uses_subprocess_call(self) -> None:
        """Without `--cli` the relaunch keeps subprocess.call and the messages stay on stdout."""
        module = load_starter()
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            with (
                self._bootstrap_patches(module, Path(temporary), local_exists=True),
                patch.object(module.subprocess, "run") as install,
                patch.object(module.subprocess, "Popen") as popen,
                patch.object(module.subprocess, "call", return_value=0) as call,
                redirect_stdout(stdout),
            ):
                with self.assertRaises(SystemExit):
                    module._ensure_runtime()
        call.assert_called_once()
        popen.assert_not_called()
        self.assertNotIn("stdout", install.call_args.kwargs)
        self.assertIn("Installing DayZ-ServerMan Python dependencies...", stdout.getvalue())

    def test_package_main_forwards_cli_with_the_source_layout(self) -> None:
        """`python -m dayz_serverman --cli ...` calls cli.main with application_root None."""
        from dayz_serverman import __main__ as module_main

        fake_cli = types.ModuleType("dayz_serverman.cli")
        fake_cli.main = MagicMock(return_value=0)
        with (
            patch.dict(sys.modules, {"dayz_serverman.cli": fake_cli}),
            patch.object(module_main, "run_window") as window,
        ):
            self.assertEqual(module_main.main(["--cli", "status"]), 0)
        fake_cli.main.assert_called_once_with(["status"], application_root=None)
        window.assert_not_called()


if __name__ == "__main__":
    unittest.main()
