"""Task 3.3: `server start/stop/restart`, `schedule set/clear`, `profile backup-after-stop` (10.1).

Criteria 5, 6, 10, 13, 15, 25 and 26 through real compositions over a fake process table; each
command run is its own session, so a stop after a start is an adoption (P1).
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lifecycle_cli_fixtures import OTHER_ID, LifecycleRoot, saved_state  # noqa: E402
from session_fixtures import PROFILE_ID  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402
from dayz_serverman.cli.commands import lifecycle  # noqa: E402
from dayz_serverman.domain.lifecycle import InventorySnapshot, LifecycleFailure, ProcessObservation  # noqa: E402
from dayz_serverman.session import open_owner_session  # noqa: E402

PROFILE = ["--profile", PROFILE_ID]


class LifecycleCommandTests(LifecycleRoot):
    """Each lifecycle command against a populated root with two profiles and a fake process table."""

    def test_start_then_stop_in_another_session(self) -> None:
        """Start writes the record; a later session adopts and stops it (P1); both end with exit 0."""
        code, stdout, stderr = self.cli("server", "start", *PROFILE, "--yes")
        self.assertEqual(code, 0, stderr)
        self.assertIn("Start DayZ server?", stdout)
        self.assertIn("Server started.", stderr)
        self.assertEqual((self.table.launches, self.ownership()["server"]["profile_id"]), ([700], PROFILE_ID))
        code, stdout, stderr = self.cli("server", "stop", *PROFILE, "--yes", "--json")
        self.assertEqual(code, 0, stderr)
        value = json.loads(stdout)["value"]
        self.assertEqual((value["operations"][0]["kind"], value["operations"][0]["state"]),
                         ("STOP_SERVER", "SUCCEEDED"))
        self.assertEqual((value["review"]["backup_after_stop"], value["result"]["state"]), (False, "STOPPED"))
        self.assertEqual(self.table.stops, 1)
        self.assertIsNone(self.ownership()["server"])

    def test_restart_with_and_without_backup(self) -> None:
        """A restart stops and starts; with --backup-after-stop it also creates a backup in between."""
        self.started()
        code, _stdout, stderr = self.cli("server", "restart", *PROFILE, "--yes", "--no-backup-after-stop")
        self.assertEqual(code, 0, stderr)
        self.assertEqual((self.table.stops, self.table.launches), (1, [700, 701]))
        backups = len(list((self.manager / "backups").rglob("*.zip")))
        code, stdout, stderr = self.cli("server", "restart", *PROFILE, "--yes", "--backup-after-stop", "--json")
        self.assertEqual(code, 0, stderr)
        self.assertIn("backup", json.loads(stdout)["value"]["result"])
        self.assertEqual((self.table.stops, len(list((self.manager / "backups").rglob("*.zip")))), (2, backups + 1))

    def test_saved_backup_choice_is_the_default(self) -> None:
        """profile backup-after-stop on: a stop without the flag backs up; off: it does not."""
        code, _stdout, stderr = self.cli("profile", "backup-after-stop", *PROFILE, "on")
        self.assertEqual(code, 0, stderr)
        self.started()
        code, stdout, _stderr = self.cli("server", "stop", *PROFILE, "--yes", "--json")
        self.assertEqual((code, json.loads(stdout)["value"]["review"]["backup_after_stop"]), (0, True))
        code, stdout, _stderr = self.cli("profile", "backup-after-stop", *PROFILE, "off", "--json")
        self.assertEqual((code, json.loads(stdout)["value"]["result"]["backup_after_stop"]), (0, False))

    def test_schedule_set_and_clear(self) -> None:
        """Set a daily restart; clear keeps the time and turns the action off."""
        code, stdout, stderr = self.cli("schedule", "set", "04:30", "restart", *PROFILE)
        self.assertEqual(code, 0, stderr)
        self.assertIn("Restart daily at 04:30", stdout)
        code, stdout, stderr = self.cli("schedule", "clear", *PROFILE, "--json")
        result = json.loads(stdout)["value"]["result"]
        self.assertEqual((code, result["action"], result["hour"], result["minute"]), (0, None, 4, 30))

    def test_confirmation_rules(self) -> None:
        """Criteria 5 and 6: a declined question and a missing --yes change nothing and exit 4."""
        before = saved_state(self.manager, self.dayz)
        code, stdout, stderr = self.cli("server", "start", *PROFILE, stdin=FakeTerminal("n\n"))
        self.assertEqual(code, 4)
        self.assertIn("Start DayZ server?", stdout)
        self.assertTrue(stderr.rstrip().endswith("Nothing was changed."))
        code, stdout, _stderr = self.cli("server", "start", *PROFILE, "--json", stdin=FakeTerminal("y\n"))
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (4, "CONFIRMATION_REQUIRED"))
        code, _stdout, _stderr = self.cli("server", "start", *PROFILE, stdin=FakeTerminal("y\n", terminal=False))
        self.assertEqual(code, 4)
        self.assertEqual((self.table.launches, saved_state(self.manager, self.dayz)), ([], before))
        code, _stdout, _stderr = self.cli("server", "start", *PROFILE, stdin=FakeTerminal("yes\n"))
        self.assertEqual((code, self.table.launches), (0, [700]))

    def test_unowned_server_is_refused_with_exit_3(self) -> None:
        """Criterion 15: stop and restart of a server this manager did not start; nothing changed."""
        executable = str(self.dayz / "Bin" / "DayZ Server_x64.exe")
        self.table.snapshot = InventorySnapshot((ProcessObservation(999, executable, 1),))
        before = saved_state(self.manager, self.dayz)
        for verb in ("stop", "restart"):
            for mode in ((), ("--json",)):
                with self.subTest(verb=verb, mode=mode):
                    code, stdout, stderr = self.cli("server", verb, *PROFILE, "--yes", *mode)
                    self.assertEqual(code, 3, stderr)
                    if mode:
                        self.assertEqual(json.loads(stdout)["error"]["code"], "EXTERNAL_PROCESS")
                    else:
                        # Criterion 28: the window's reason, before any question
                        self.assertIn("The server runs outside DayZ-ServerMan. Stop it there.", stderr)
        self.assertEqual((self.table.stops, saved_state(self.manager, self.dayz)), (0, before))

    def test_restart_refused_after_the_stop_exits_1(self) -> None:
        """Criterion 25: the start after the stop is refused (EXTERNAL_PROCESS, CONTROL_CONFLICT): exit 1."""
        executable = str(self.dayz / "Bin" / "DayZ Server_x64.exe")
        external = InventorySnapshot((ProcessObservation(999, executable, 1),))

        def outsider_appears() -> None:
            """After the stop is confirmed, a server outside the manager is running."""
            self.table.queue.append(InventorySnapshot(()))
            self.table.snapshot = external

        cases = (("EXTERNAL_PROCESS", (), None), ("CONTROL_CONFLICT", (), "launch"),
                 ("EXTERNAL_PROCESS", ("--backup-after-stop",), None))
        for code_name, flags, how in cases:
            with self.subTest(code=code_name, flags=flags):
                self.table.snapshot, self.table.queue, self.table.launch_failure = InventorySnapshot(()), [], None
                self.table.after_stop = None
                self.started()
                stops = self.table.stops
                if how == "launch":
                    self.table.launch_failure = LifecycleFailure("CONTROL_CONFLICT", "synthetic competing manager")
                else:
                    self.table.after_stop = outsider_appears
                code, stdout, stderr = self.cli("server", "restart", *PROFILE, "--yes", *flags, "--json")
                error = json.loads(stdout)["error"]
                self.assertEqual((code, error["code"]), (1, code_name), stderr)
                self.assertEqual(error["details"]["operation"]["kind"], "RESTART_SERVER")
                self.assertEqual(self.table.stops, stops + 1)
                self.table.launch_failure, self.table.after_stop = None, None
                self.table.snapshot = InventorySnapshot(())

    def test_another_profile_than_the_running_one_exits_3_also_after_adoption(self) -> None:
        """Criterion 26: stop and restart naming the other profile, in a session that adopted the server."""
        self.started()
        before = saved_state(self.manager, self.dayz)
        for verb in ("stop", "restart"):
            with self.subTest(verb=verb):
                code, _stdout, stderr = self.cli("server", verb, "--profile", OTHER_ID, "--yes")
                self.assertEqual(code, 3)
                self.assertIn("The running server was started with another profile.", stderr)
                self.assertIn("The server runs with Livonia", stderr)
                code, stdout, _stderr = self.cli("server", verb, "--profile", OTHER_ID, "--yes", "--json")
                error = json.loads(stdout)["error"]
                self.assertEqual((code, error["code"], error["details"]["running_profile_id"]),
                                 (3, "INVALID_REQUEST", PROFILE_ID))
        self.assertEqual((self.table.stops, saved_state(self.manager, self.dayz)), (0, before))

    def test_wait_ready(self) -> None:
        """--wait-ready: exit 0 once ready; exit 1 after the limit, and the server keeps running."""
        polls: list[float] = []
        now, answer_after = [0.0], [3]

        def sleep(seconds: float) -> None:
            """Move the fake clock; the server answers after the scripted number of polls."""
            polls.append(seconds)
            now[0] += seconds
            self.table.ready = len(polls) >= answer_after[0]

        with patch.object(lifecycle, "SLEEP", sleep), patch.object(lifecycle, "CLOCK", lambda: now[0]):
            code, stdout, stderr = self.cli("server", "start", *PROFILE, "--yes", "--wait-ready", "--json")
            self.assertEqual((code, json.loads(stdout)["value"]["result"]["readiness"]), (0, "READY"), stderr)
            self.assertEqual(polls, [2.0, 2.0, 2.0])
            self.cli("server", "stop", *PROFILE, "--yes")
            # The second start never answers
            self.table.ready, answer_after[0] = False, 10 ** 6
            code, _stdout, stderr = self.cli("server", "start", *PROFILE, "--yes", "--ready-timeout", "10")
            self.assertEqual(code, 1)
            self.assertIn("was not ready within 10 seconds. It keeps running.", stderr)
            self.assertIsNotNone(self.ownership()["server"])

    def test_instance_active_with_a_held_lock(self) -> None:
        """Criterion 13: with the window holding the lock, every lifecycle write exits 3 and changes nothing."""
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            before = saved_state(self.manager, self.dayz)
            for line in (["server", "start", *PROFILE, "--yes"], ["server", "stop", *PROFILE, "--yes"],
                         ["server", "restart", *PROFILE, "--yes"], ["schedule", "set", "04:30", "stop", *PROFILE],
                         ["schedule", "clear", *PROFILE], ["profile", "backup-after-stop", *PROFILE, "on"]):
                with self.subTest(line=line):
                    code, stdout, _stderr = self.cli(*line, "--json")
                    self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "INSTANCE_ACTIVE"))
            self.assertEqual((self.table.launches, saved_state(self.manager, self.dayz)), ([], before))
        finally:
            holder.close(drain_seconds=5)

    def test_expected_revision_pins_the_state(self) -> None:
        """--expect-profile-revision that differs from the stored one: exit 3 before any submit."""
        code, stdout, _stderr = self.cli("server", "start", *PROFILE, "--yes", "--expect-profile-revision", "99",
                                         "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"], self.table.launches),
                         (3, "REVISION_CONFLICT", []))


class CommandTextTests(unittest.TestCase):
    """Criterion 18 for the lifecycle sentences."""

    def test_sentences_follow_the_input_name_rules(self) -> None:
        """The readiness and D11 sentences hold input names only as text to type."""
        from test_cli_input_names import InputNameChecker, flags
        from dayz_serverman.cli import wording
        checker = InputNameChecker(self, flags() | {PROFILE_ID, OTHER_ID}, [])
        for line in (wording.not_ready(300), wording.server_left_running("Stopped"), wording.ready_wait_stopped(),
                     wording.other_profile_running("Livonia Main")):
            checker.blocks([line])


if __name__ == "__main__":
    unittest.main()
