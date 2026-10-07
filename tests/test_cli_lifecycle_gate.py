"""Criteria 28 and 29 (Product Owner ruling of 2026-10-07 15:07:57 on QF-31 and QF-32)."""

from __future__ import annotations

import json
import signal
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lifecycle_cli_fixtures import OTHER_ID, LifecycleRoot, saved_state  # noqa: E402
from session_fixtures import PROFILE_ID  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.cli.commands import lifecycle  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402

PROFILE = ["--profile", PROFILE_ID]


class StateGateTests(LifecycleRoot):
    """Criterion 28: refusals that one status read decides come before the question."""

    def refused(self, arguments: list[str], expected: int, code: str) -> None:
        """Without --yes and without a terminal: the exit code, no review, no question, stdin not read."""
        before = saved_state(self.manager, self.dayz)
        stdin = FakeTerminal("y\n", terminal=False)
        exit_code, stdout, stderr = self.cli(*arguments, stdin=stdin)
        self.assertEqual((exit_code, stdout, stdin.reads), (expected, "", 0), stderr)
        self.assertNotIn("Continue?", stderr)
        exit_code, stdout, _stderr = self.cli(*arguments, "--json", stdin=stdin)
        self.assertEqual((exit_code, json.loads(stdout)["error"]["code"]), (expected, code))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_each_state_refusal(self) -> None:
        """Stop and restart of a stopped server; start of a running one; D11; an unowned server; --yes too."""
        self.refused(["server", "stop", *PROFILE], 3, "CONTROL_CONFLICT")
        self.refused(["server", "restart", *PROFILE], 3, "CONTROL_CONFLICT")
        self.started()
        launches = list(self.table.launches)
        self.refused(["server", "start", *PROFILE], 3, "CONTROL_CONFLICT")
        exit_code, stdout, _stderr = self.cli("server", "start", *PROFILE, "--yes", "--json")
        self.assertEqual((exit_code, json.loads(stdout)["error"]["code"]), (3, "CONTROL_CONFLICT"))
        self.refused(["server", "stop", "--profile", OTHER_ID], 3, "INVALID_REQUEST")
        self.refused(["server", "restart", "--profile", OTHER_ID], 3, "INVALID_REQUEST")
        self.assertEqual((self.table.launches, self.table.stops), (launches, 0))

    def test_a_recovery_block_exits_6_before_the_question(self) -> None:
        """A recovery block: exit 6 for start, stop and restart."""
        block = ("An interrupted restore must be finished.", "RESTORE_BACKUP")
        with patch.object(OperationManager, "recovery_block_pair", lambda _self: block):
            for verb in ("start", "stop", "restart"):
                with self.subTest(verb=verb):
                    self.refused(["server", verb, *PROFILE], 6, "RECOVERY_REQUIRED")


class ReadyWaitStopTests(LifecycleRoot):
    """Criterion 29: Ctrl+C after a succeeded start exits 0 and says that the wait was stopped."""

    def test_ctrl_c_during_the_wait_exits_0(self) -> None:
        """The wait ends at once; JSON success with the last status and the stopped-wait marker."""
        for mode in ((), ("--json",)):
            with self.subTest(mode=mode):
                interrupts = Interrupts()
                with patch.object(lifecycle, "SLEEP", lambda _seconds: interrupts.handle(signal.SIGINT, None)):
                    code, stdout, stderr = self.cli("server", "start", *PROFILE, "--yes", "--wait-ready", *mode,
                                                    interrupts=interrupts)
                self.assertEqual(code, 0, stderr)
                if mode:
                    value = json.loads(stdout)["value"]
                    self.assertEqual((value["ready_wait_stopped"], value["result"]["state"]),
                                     (True, "RUNNING_MANAGED"))
                else:
                    self.assertIn("stopped waiting for it to be ready; it keeps running.", stdout)
                self.cli("server", "stop", *PROFILE, "--yes")

    def test_ctrl_c_during_a_start_that_succeeds_exits_0_without_a_wait(self) -> None:
        """Ctrl+C at the launch: the start still succeeds, no readiness poll runs, exit 0."""
        interrupts = Interrupts()
        self.table.on_launch = lambda: interrupts.handle(signal.SIGINT, None)
        sleeps: list[float] = []
        with patch.object(lifecycle, "SLEEP", sleeps.append):
            code, stdout, stderr = self.cli("server", "start", *PROFILE, "--yes", "--wait-ready", "--json",
                                            interrupts=interrupts)
        self.assertEqual((code, json.loads(stdout)["value"]["ready_wait_stopped"], sleeps), (0, True, []), stderr)


if __name__ == "__main__":
    unittest.main()
