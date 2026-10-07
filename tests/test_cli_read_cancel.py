"""Task 3.2, QF-20 (design 7, "Ctrl+C at other points"): Ctrl+C during a read lets the call finish, then exits 5."""

from __future__ import annotations

import io
import json
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from session_fixtures import populate, tree_hashes  # noqa: E402
from test_cli_check_order import RecordingHandler  # noqa: E402
from dayz_serverman import session_observer  # noqa: E402
from dayz_serverman.cli import runner, wording  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402


class ReadCancelTests(unittest.TestCase):
    """The SIGINT handler fires inside a bridge call of a read; the command ends cancelled."""

    @classmethod
    def setUpClass(cls) -> None:
        """Build one populated manager root and DayZ root."""
        cls.temporary = tempfile.TemporaryDirectory(prefix="serverman_cli_cancel_")
        cls.manager, cls.dayz = populate(Path(cls.temporary.name))

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove the roots."""
        cls.temporary.cleanup()

    def run_pressed(self, arguments: list[str], *, method: str) -> tuple[int, str, str, list[str]]:
        """Run a command line; Ctrl+C is pressed while the observer runs `method`; return the calls made."""
        interrupts = Interrupts()
        real = session_observer.ObserverSession.call
        made: list[str] = []

        def call(session, name, parameters=None):
            """Press Ctrl+C during the call, then let it finish."""
            made.append(name)
            if name == method:
                interrupts.handle(signal.SIGINT, None)
            return real(session, name, parameters)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(session_observer.ObserverSession, "call", call):
            code = runner.run(arguments, self.manager, stdout=stdout, stderr=stderr, interrupts=interrupts)
        self.assertFalse(interrupts.requested)
        return code, stdout.getvalue(), stderr.getvalue(), made

    def test_server_status_in_text_mode_exits_5_after_the_call(self) -> None:
        """The call finished; the result is not printed; the sentence says nothing changed."""
        code, stdout, stderr, made = self.run_pressed(["server", "status"], method="get_server_status")
        self.assertEqual((code, stdout), (5, ""))
        self.assertEqual(stderr.strip(), wording.READ_CANCELLED)
        self.assertEqual(made, ["get_server_status"])

    def test_profile_list_in_json_mode_exits_5_with_the_cancelled_code(self) -> None:
        """One JSON document with CANCELLED; stderr stays empty."""
        code, stdout, stderr, _made = self.run_pressed(["profile", "list", "--json"], method="list_profiles")
        document = json.loads(stdout)
        self.assertEqual((code, document["success"], document["error"]["code"]), (5, False, "CANCELLED"))
        self.assertEqual(stderr, "")

    def test_a_read_without_ctrl_c_still_exits_0(self) -> None:
        """No press: the same read succeeds."""
        stdout, stderr = io.StringIO(), io.StringIO()
        code = runner.run(["server", "status", "--json"], self.manager, stdout=stdout, stderr=stderr,
                          interrupts=Interrupts())
        self.assertEqual((code, json.loads(stdout.getvalue())["success"]), (0, True))

    def test_ctrl_c_in_the_pre_step_of_a_write_exits_5_before_the_lock(self) -> None:
        """A write's read-only pre-step finishes; no owner session opens and no handler runs; nothing changed."""
        handler = RecordingHandler()
        before = tree_hashes(self.manager, self.dayz)
        with patch.object(runner, "load_handler", return_value=handler), \
                patch.object(runner.owner_sessions, "open_owner_session") as owner:
            code, stdout, _stderr, _made = self.run_pressed(["server", "stop", "--json"], method="list_profiles")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (5, "CANCELLED"))
        owner.assert_not_called()
        self.assertEqual(handler.contexts, [])
        self.assertEqual(tree_hashes(self.manager, self.dayz), before)


if __name__ == "__main__":
    unittest.main()
