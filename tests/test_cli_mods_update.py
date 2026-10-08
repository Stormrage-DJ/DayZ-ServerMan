"""Task 5.3: `mods update` through the scripted bridge (10.3, 8.1, 8.2; criteria 5, 6, 8, 15, 24, 26; QF-16).

Each case runs the CLI runner over `mods_update_fixtures.ScriptedBridge`, which checks the exact
parameter sets of the host handlers. "Nothing applied" = no publish or apply-and-restart call.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mods_update_fixtures import FINGERPRINT, ScriptedBridge, run_update  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402
from test_cli_input_names import InputNameChecker, RecordingOutput, flags  # noqa: E402
from dayz_serverman.cli import runner  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.cli.wording import MODS_DOWNLOADED  # noqa: E402

RUNNING = {"state": "RUNNING_MANAGED", "profile_id": "alpha"}
STOPPED = {"state": "STOPPED", "profile_id": None}


class ApplyTests(unittest.TestCase):
    """The download, the review, the question and the apply in one process (criterion 19)."""

    def test_plain_apply_with_yes_in_text_and_json(self) -> None:
        """A stopped server: the per-mod results, the window's review, then the plain apply."""
        bridge = ScriptedBridge()
        code, stdout, stderr = run_update(bridge, "--yes")
        self.assertEqual(code, 0, stderr)
        self.assertEqual([m for m in bridge.methods() if m.endswith(("items", "publication", "keys", "restart"))],
                         ["update_workshop_items", "preview_mod_publication", "publish_mods_and_keys"])
        update = bridge.parameters("update_workshop_items")
        self.assertEqual((update["update_all_and_start"], update["authentication_mode"], update["account_name"]),
                         (False, "ANONYMOUS", None))
        self.assertEqual(bridge.parameters("publish_mods_and_keys")["publication_fingerprint"], FINGERPRINT)
        self.assertRegex(stdout, r"111 +Updated\n222 +Already current")
        self.assertEqual(stdout.splitlines()[0], "Profile: Alpha")
        for text in ("Apply downloaded mods and keys?",
                     "Review the server folders that will be updated.", "No server start was requested.",
                     "Workshop 111: @Alpha", "2 verified key file(s), all already in place",
                     "Mods and keys applied. Server start was not requested."):
            self.assertIn(text, stdout)
        self.assertNotIn("Workshop 222", stdout)
        bridge = ScriptedBridge()
        code, stdout, _stderr = run_update(bridge, "--yes", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual((code, [record["kind"] for record in value["operations"]]),
                         (0, ["UPDATE_WORKSHOP_ITEMS", "PUBLISH_MODS_AND_KEYS"]))
        self.assertEqual(value["review"]["publication_fingerprint"], FINGERPRINT)

    def test_start_and_restart(self) -> None:
        """`--start` on a stopped server publishes with the start; `--restart` stops, backs up when chosen, starts."""
        bridge = ScriptedBridge()
        bridge.apply_result = {**bridge.apply_result, "start_state": "STARTED"}
        code, stdout, stderr = run_update(bridge, "--start", "--yes")
        self.assertEqual(code, 0, stderr)
        self.assertTrue(bridge.parameters("update_workshop_items")["update_all_and_start"])
        self.assertIn("After the mods are verified in the server folder, DayZ-ServerMan will start this server.", stdout)
        self.assertIn("Mods and keys applied. The server was started.", stdout)
        for arguments, saved, backup in (((), [], False), (("--backup-after-stop",), [], True),
                                         ((), ["alpha"], True), (("--no-backup-after-stop",), ["alpha"], False)):
            with self.subTest(arguments=arguments, saved=saved):
                bridge = ScriptedBridge()
                bridge.statuses = [RUNNING]
                bridge.preferences = {**bridge.preferences, "backup_after_stop_profiles": saved}
                bridge.apply_result = {"profile_id": "alpha", "start_state": "STARTED", "backup": None}
                code, stdout, stderr = run_update(bridge, "--restart", *arguments, "--yes")
                self.assertEqual(code, 0, stderr)
                self.assertEqual(bridge.applied(), ["apply_mods_and_restart"])
                self.assertEqual(bridge.parameters("apply_mods_and_restart")["backup_after_stop"], backup)
                self.assertEqual("create a verified backup" in stdout, backup)
                self.assertIn("Mods applied and server restarted.", stdout)

    def test_a_start_that_fails_after_the_apply_exits_1(self) -> None:
        """The mods are applied but the requested start failed: the work did not succeed."""
        bridge = ScriptedBridge()
        bridge.apply_result = {**bridge.apply_result, "start_state": "FAILED", "start_error": "LAUNCH_FAILED"}
        code, stdout, _stderr = run_update(bridge, "--start", "--yes", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["operation"]["kind"]),
                         (1, "LAUNCH_FAILED", "PUBLISH_MODS_AND_KEYS"))
        self.assertIn("The server could not be started", error["message"])


class NotConfirmedTests(unittest.TestCase):
    """Criterion 24: after the download, a missing confirmation exits 4 and ends with the download sentence."""

    def assert_downloaded_only(self, bridge: ScriptedBridge, code: int, expected: int) -> None:
        """The download ran, nothing was applied, and the exit code is the expected one."""
        self.assertEqual((code, bridge.applied()), (expected, []))
        self.assertIn("update_workshop_items", bridge.methods())

    def test_decline_no_terminal_ctrl_c_and_json(self) -> None:
        """Each refusal of 8.1 ends with "The mods are downloaded; nothing was applied."; JSON has both records."""
        for stdin in (FakeTerminal("n\n"), FakeTerminal(None), FakeTerminal("y\n", terminal=False)):
            with self.subTest(terminal=stdin.terminal, answer=stdin.answer):
                bridge = ScriptedBridge()
                code, stdout, stderr = run_update(bridge, stdin=stdin)
                self.assert_downloaded_only(bridge, code, 4)
                self.assertTrue(stderr.splitlines()[-1].endswith(MODS_DOWNLOADED), stderr)
                self.assertNotIn("Nothing was changed.", stderr)
                self.assertIn("Apply downloaded mods and keys?", stdout)
                self.assertEqual(stdin.reads, 1 if stdin.terminal else 0)
        bridge = ScriptedBridge()
        stdin = FakeTerminal("y\n")
        code, stdout, _stderr = run_update(bridge, "--json", stdin=stdin)
        self.assert_downloaded_only(bridge, code, 4)
        error = json.loads(stdout)["error"]
        self.assertEqual((error["code"], error["details"]["review"]["publication_fingerprint"],
                          error["details"]["operation"]["kind"], stdin.reads),
                         ("CONFIRMATION_REQUIRED", FINGERPRINT, "UPDATE_WORKSHOP_ITEMS", 0))
        self.assertTrue(error["message"].endswith(MODS_DOWNLOADED))

    def test_ctrl_c_during_the_download_stops_before_the_review(self) -> None:
        """The download ends; the next step is not started: exit 5 with the download sentence."""
        bridge = ScriptedBridge()
        interrupts = Interrupts()
        bridge.hooks["update_workshop_items"] = lambda: interrupts.handle(2, None)
        code, stdout, _stderr = run_update(bridge, "--yes", "--json", interrupts=interrupts)
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], bridge.applied()), (5, "CANCELLED", []))
        self.assertNotIn("preview_mod_publication", bridge.methods())
        self.assertTrue(error["message"].endswith(MODS_DOWNLOADED))


class StateTests(unittest.TestCase):
    """Rule 5 and criterion 26 before the download; criterion 24 for a state that changed after it."""

    def test_state_gate_before_the_download(self) -> None:
        """`--start` while running, `--restart` while stopped or outside DayZ-ServerMan: exit 3, no download."""
        cases = (("--start", RUNNING, "CONTROL_CONFLICT", "This cannot be done while the server is running"),
                 ("--restart", STOPPED, "CONTROL_CONFLICT", "This cannot be done while the server is stopped"),
                 ("--restart", {"state": "RUNNING_EXTERNAL"}, "EXTERNAL_PROCESS", "outside DayZ-ServerMan"))
        for flag, status, json_code, text in cases:
            for mode in ((), ("--json", "--yes")):
                with self.subTest(flag=flag, state=status["state"], mode=mode):
                    bridge = ScriptedBridge()
                    bridge.statuses = [status]
                    code, stdout, stderr = run_update(bridge, flag, *mode, stdin=FakeTerminal("y\n", terminal=False))
                    self.assertEqual(code, 3)
                    self.assertNotIn("update_workshop_items", bridge.methods())
                    message = json.loads(stdout)["error"] if mode else {"code": json_code, "message": stderr}
                    self.assertEqual(message["code"], json_code)
                    self.assertIn(text, message["message"])

    def test_restart_of_another_profile_refuses_before_the_download(self) -> None:
        """Criterion 26: the D11 reason names the running profile, exit 3, in text and JSON."""
        for mode in ((), ("--json",)):
            with self.subTest(mode=mode):
                bridge = ScriptedBridge()
                bridge.statuses = [{"state": "RUNNING_MANAGED", "profile_id": "other"}]
                code, stdout, stderr = run_update(bridge, "--restart", "--yes", *mode)
                self.assertEqual((code, bridge.applied()), (3, []))
                self.assertNotIn("update_workshop_items", bridge.methods())
                text = json.loads(stdout)["error"]["message"] if mode else stderr
                self.assertIn("The running server was started with another profile.", text)
                self.assertIn("The server runs with Other World.", text)
                if mode:
                    self.assertEqual(json.loads(stdout)["error"]["details"]["running_profile_id"], "other")

    def test_a_state_that_changed_after_the_download(self) -> None:
        """The "changed" row (exit 3) and the "refused" row (exit 3): reason, then the download sentence."""
        bridge = ScriptedBridge()
        bridge.statuses = [STOPPED, RUNNING]
        code, stdout, stderr = run_update(bridge, "--start", "--yes")
        self.assertEqual((code, bridge.applied()), (3, []))
        self.assertIn("Mods were not applied", stdout)
        self.assertEqual(stderr.splitlines()[-1], "The server state changed. The mods are downloaded; nothing was "
                                                  "applied.")
        bridge = ScriptedBridge()
        bridge.statuses = [RUNNING]
        code, stdout, _stderr = run_update(bridge, "--yes", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], bridge.applied()), (3, "CONTROL_CONFLICT", []))
        self.assertEqual(error["message"], "The server is running. Mods cannot be applied to the server folder now. "
                                           "Use mods update --restart, or stop the server first. " + MODS_DOWNLOADED)
        self.assertEqual((error["details"]["review"]["publication_fingerprint"], error["details"]["operation"]["kind"]),
                         (FINGERPRINT, "UPDATE_WORKSHOP_ITEMS"))

    def test_a_running_server_with_an_unguarded_plain_apply_is_asked(self) -> None:
        """The host's policy decides (D10): an unguarded plain apply is reviewed with its risk and applied."""
        bridge = ScriptedBridge()
        bridge.statuses = [RUNNING]
        bridge.preview = {**bridge.preview, "plain_apply_guarded": False}
        code, stdout, stderr = run_update(bridge, "--yes")
        self.assertEqual((code, bridge.applied()), (0, ["publish_mods_and_keys"]), stderr)
        self.assertIn("A mod folder that is in use cannot be replaced", stdout)
        self.assertIn("Use mods update --restart instead.", stdout)

class TextRuleTests(unittest.TestCase):
    """Criterion 18: the review, the results and the refusals of `mods update` hold no identifier in prose."""

    def test_every_written_block(self) -> None:
        """A plain apply, a refused row, a restart review and a D11 refusal: every part in its allowed place."""
        runs = ((STOPPED, ("--yes",)), (RUNNING, ()), (RUNNING, ("--restart", "--yes")),
                ({"state": "RUNNING_MANAGED", "profile_id": "other"}, ("--restart",)))
        for status, arguments in runs:
            with self.subTest(state=status, arguments=arguments):
                bridge = ScriptedBridge()
                bridge.statuses = [status]
                RecordingOutput.made = []
                with patch.object(runner, "Output", RecordingOutput):
                    run_update(bridge, *arguments, stdin=FakeTerminal("", terminal=False))
                written = [block for output in RecordingOutput.made for block in output.written]
                self.assertTrue(written)
                InputNameChecker(self, flags() | {"alpha"}, ["alpha"]).blocks(written)


if __name__ == "__main__":
    unittest.main()
