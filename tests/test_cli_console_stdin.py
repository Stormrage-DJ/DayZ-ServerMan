"""QF-30, criterion 6: stdin from NUL is not a terminal, so a confirmation refuses at once and names --yes."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from session_fixtures import PROFILE_ID, populate, tree_hashes  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402
from dayz_serverman.adapters.windows.console import is_terminal  # noqa: E402
from dayz_serverman.cli.confirm import QUESTION, confirm  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.cli.output import CliFailure, Output  # noqa: E402
from dayz_serverman.cli.review import lifecycle_review  # noqa: E402

PYTHON_ROOT = Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"
# The sentence of criterion 6 that names --yes
NEEDS_YES = "Confirmation is needed. Nothing was changed. Run the command again with --yes."


class NulStream(io.StringIO):
    """A stream like NUL on Windows: isatty says yes, and its descriptor is not a console."""

    def __init__(self, descriptor: int) -> None:
        """Keep the descriptor of an opened NUL device."""
        super().__init__()
        self.descriptor = descriptor
        self.reads = 0

    def isatty(self) -> bool:
        """Answer as Windows does for NUL."""
        return True

    def fileno(self) -> int:
        """Return the NUL descriptor."""
        return self.descriptor

    def readline(self, *_args: object) -> str:
        """Count a read that must never happen."""
        self.reads += 1
        return ""


@unittest.skipUnless(os.name == "nt", "the console check is the Windows rule")
class ConsoleStdinTests(unittest.TestCase):
    """is_terminal needs GetConsoleMode on Windows; a test double without a descriptor keeps its own answer."""

    def test_nul_is_not_a_terminal_and_a_double_without_descriptor_is(self) -> None:
        """The real NUL device and a NUL-like stream are not terminals; an in-memory terminal double is."""
        with open(os.devnull, encoding="utf-8") as nul:
            self.assertTrue(nul.isatty())
            self.assertFalse(is_terminal(nul))
            self.assertFalse(is_terminal(NulStream(nul.fileno())))
        self.assertTrue(is_terminal(FakeTerminal("y\n")))
        self.assertFalse(is_terminal(FakeTerminal("y\n", terminal=False)))
        self.assertFalse(is_terminal(None))

    def test_confirmation_with_a_nul_like_stdin_refuses_without_the_question(self) -> None:
        """Exit 4 at once with the sentence that names --yes; the question is not printed and stdin is not read."""
        stdout, stderr = io.StringIO(), io.StringIO()
        output = Output(stdout, stderr, json_mode=False)
        blocks, value = lifecycle_review("stop", {"profile_id": PROFILE_ID, "display_name": "Livonia"}, False)
        with open(os.devnull, encoding="utf-8") as nul:
            stdin = NulStream(nul.fileno())
            with self.assertRaises(CliFailure) as raised:
                confirm(output, Interrupts(), stdin, yes=False, review=blocks, review_value=value)
        output.failure(raised.exception)
        self.assertEqual((raised.exception.exit_code, stdin.reads), (4, 0))
        self.assertIn(NEEDS_YES, stderr.getvalue())
        self.assertNotIn(QUESTION.strip(), stderr.getvalue())


@unittest.skipUnless(os.name == "nt", "stdin from NUL is the Windows case")
class NulSubprocessTests(unittest.TestCase):
    """A real CLI process with stdin from NUL: `server start` of a stopped server refuses at once, before any submit."""

    def test_server_start_with_stdin_from_nul_exits_4_and_names_yes(self) -> None:
        """Exit 4, the review, the sentence that names --yes, no question; the trees stay unchanged."""
        with tempfile.TemporaryDirectory(prefix="serverman_cli_nul_") as temporary:
            manager, dayz = populate(Path(temporary))
            before = tree_hashes(manager / "config", manager / "data" / "profiles", dayz)
            script = ("import sys; from pathlib import Path; from dayz_serverman.cli import main; "
                      f"sys.exit(main(['server', 'start', '--profile', '{PROFILE_ID}'], Path(sys.argv[1])))")
            environment = {**os.environ, "PYTHONPATH": str(PYTHON_ROOT)}
            finished = subprocess.run([sys.executable, "-c", script, str(manager)], stdin=subprocess.DEVNULL,
                                      capture_output=True, text=True, timeout=120, env=environment)
            self.assertEqual(finished.returncode, 4, finished.stderr)
            self.assertIn("Start DayZ server?", finished.stdout)
            self.assertIn(NEEDS_YES, finished.stderr)
            self.assertNotIn(QUESTION.strip(), finished.stdout + finished.stderr)
            self.assertEqual(tree_hashes(manager / "config", manager / "data" / "profiles", dayz), before)


if __name__ == "__main__":
    unittest.main()
