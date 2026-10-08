"""Task 5.3: `mods update` after the download through the scripted bridge (10.3; criterion 24; QF-16).

Refusals after the download, the rows without a review (everything current, no Workshop mods) and
downloads that were not all verified. The apply paths and the gate before the download are in
`tests/test_cli_mods_update.py`.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mods_update_fixtures import ScriptedBridge, current_result, item, run_update  # noqa: E402
from dayz_serverman.cli.wording import MODS_DOWNLOADED  # noqa: E402

RUNNING = {"state": "RUNNING_MANAGED", "profile_id": "alpha"}
STOPPED = {"state": "STOPPED", "profile_id": None}


class RefusalAfterDownloadTests(unittest.TestCase):
    """QF-16: every refusal after the download ends with the download sentence and reports the update."""

    def test_d11_at_the_apply_names_the_running_profile(self) -> None:
        """The server changed hands between the review and the apply: exit 3, the D11 reason, nothing applied."""
        bridge = ScriptedBridge()
        bridge.statuses = [RUNNING, RUNNING, {"state": "RUNNING_MANAGED", "profile_id": "other"}]
        code, stdout, _stderr = run_update(bridge, "--restart", "--yes", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["running_profile_id"]), (3, "INVALID_REQUEST", "other"))
        self.assertIn("The server runs with Other World.", error["message"])
        self.assertTrue(error["message"].endswith(MODS_DOWNLOADED))
        self.assertEqual(error["details"]["operation"]["kind"], "UPDATE_WORKSHOP_ITEMS")

    def test_refusals_at_dispatch(self) -> None:
        """A stale review at the preview or at the apply exits 3 with the download sentence."""
        for method, code_name in (("preview_mod_publication", "REVISION_CONFLICT"),
                                  ("publish_mods_and_keys", "PUBLICATION_PREVIEW_STALE")):
            with self.subTest(method=method):
                bridge = ScriptedBridge()
                bridge.refuse[method] = {"code": code_name, "message": "changed", "retryable": True}
                code, stdout, _stderr = run_update(bridge, "--yes", "--json")
                error = json.loads(stdout)["error"]
                self.assertEqual((code, error["code"]), (3, code_name))
                self.assertTrue(error["message"].endswith(MODS_DOWNLOADED))
                self.assertEqual(error["details"]["operation"]["kind"], "UPDATE_WORKSHOP_ITEMS")

    def test_an_apply_that_ends_before_or_after_its_first_change(self) -> None:
        """Before the stop (A13 refusal at preflight): exit 3 with the sentence; after a change: exit 1 without it."""
        bridge = ScriptedBridge()
        bridge.statuses = [RUNNING]
        bridge.apply_end = ("FAILED", {"code": "CONTROL_CONFLICT", "message": "busy"}, "preflight")
        bridge.apply_percent = 3
        code, stdout, _stderr = run_update(bridge, "--restart", "--yes", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"]), (3, "CONTROL_CONFLICT"))
        self.assertTrue(error["message"].endswith(MODS_DOWNLOADED))
        self.assertEqual((error["details"]["operation"]["kind"], error["details"]["update_operation"]["kind"]),
                         ("APPLY_MODS_AND_RESTART", "UPDATE_WORKSHOP_ITEMS"))
        bridge = ScriptedBridge()
        bridge.apply_end = ("FAILED", {"code": "PUBLICATION_FAILED", "message": "copy"}, "COPY_FILE")
        code, stdout, _stderr = run_update(bridge, "--yes", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"]), (1, "PUBLICATION_FAILED"))
        self.assertNotIn(MODS_DOWNLOADED, error["message"])


class NothingToApplyTests(unittest.TestCase):
    """The window's rows without a review: everything current (no SteamCMD run), no Workshop mods."""

    def test_everything_current_runs_no_apply(self) -> None:
        """No download and every mod applied: the page's sentence, exit 0; with a restart the server keeps running."""
        bridge = ScriptedBridge()
        bridge.update = current_result()
        code, stdout, stderr = run_update(bridge)
        self.assertEqual((code, bridge.applied()), (0, []), stderr)
        self.assertIn("All mods are current. Nothing to download or apply.", stdout)
        bridge = ScriptedBridge()
        bridge.update, bridge.statuses = current_result(), [RUNNING]
        code, stdout, _stderr = run_update(bridge, "--restart")
        self.assertEqual((code, bridge.applied()), (0, []))
        self.assertIn("All mods are current. The server was not restarted. To restart it anyway, run server restart.",
                      stdout)
        bridge = ScriptedBridge()
        bridge.update = current_result()
        bridge.preview = {**bridge.preview, "missing_key_count": 1,
                          "targets": [{"workshop_id": "111", "target_relative": "@Alpha", "current": True}]}
        code, stdout, _stderr = run_update(bridge, "--yes")
        self.assertEqual((code, bridge.applied()), (0, ["publish_mods_and_keys"]))
        self.assertIn("No mod folder is copied. Missing key files are added.", stdout)

    def test_a_profile_without_workshop_mods(self) -> None:
        """EMPTY: the sentence without a start; with a start on a stopped server a short review confirms it."""
        for flag, statuses, text in (((), [STOPPED], "This profile has no Workshop mods."),
                                     (("--restart",), [RUNNING],
                                      "This profile has no Workshop mods. The server was not restarted.")):
            with self.subTest(flag=flag):
                bridge = ScriptedBridge()
                bridge.update, bridge.statuses = {**bridge.update, "download_state": "EMPTY", "items": []}, statuses
                code, stdout, _stderr = run_update(bridge, *flag)
                self.assertEqual((code, bridge.applied()), (0, []))
                self.assertIn(text, stdout)
        bridge = ScriptedBridge()
        bridge.update = {**bridge.update, "download_state": "EMPTY", "items": []}
        bridge.preview = {**bridge.preview, "targets": [], "key_count": 0}
        code, stdout, _stderr = run_update(bridge, "--start", "--yes")
        self.assertEqual((code, bridge.applied()), (0, ["publish_mods_and_keys"]))
        self.assertIn("Nothing is written to the server folder", stdout)


class DownloadEndTests(unittest.TestCase):
    """An update that did not verify every download: no review; exit 1, or 5 when cancelled."""

    def test_unverified_downloads(self) -> None:
        """Failed sign-in, an unconfirmed run and a cancelled run, each with the per-mod lines on stderr."""
        cases = (
            ({"download_state": "FAILED", "items": [item("111", "AUTHENTICATION_FAILED", None, "AUTHENTICATION_FAILED")]},
             1, "AUTHENTICATION_FAILED", "Some mods could not be updated. The server was not started. Run steam login, "
             "then try again."),
            ({"download_state": "UNKNOWN", "steamcmd_exit_code": 7, "items": [item("111", "UNKNOWN_FAILED", None)]}, 1,
             "UPDATE_RESULT_UNKNOWN", "SteamCMD exited without a verifiable update result (exit code 7)."),
            ({"download_state": "CANCELLED", "items": [item("111", "CANCELLED", None)]}, 5, "CANCELLED",
             "Mod update cancelled."),
        )
        for changes, expected, json_code, text in cases:
            with self.subTest(state=changes["download_state"]):
                bridge = ScriptedBridge()
                bridge.update = {**bridge.update, **changes}
                code, _stdout, stderr = run_update(bridge, "--start", "--yes")
                self.assertEqual((code, bridge.applied()), (expected, []))
                self.assertNotIn("preview_mod_publication", bridge.methods())
                self.assertIn(text, stderr)
                self.assertIn("111  ", stderr)
                bridge = ScriptedBridge()
                bridge.update = {**bridge.update, **changes}
                code, stdout, _stderr = run_update(bridge, "--json")
                self.assertEqual((code, json.loads(stdout)["error"]["code"]), (expected, json_code))

    def test_a_failed_update_operation_and_checks_before_it(self) -> None:
        """A sign-in refusal before the download (3), no sign-in chosen (3), a recovery block (6), pins (3),
        a backup choice without --restart (2): nothing is downloaded or applied."""
        bridge = ScriptedBridge()
        bridge.update_end = ("FAILED", {"code": "AUTHENTICATION_REQUIRED", "message": "x"}, "preflight")
        code, stdout, _stderr = run_update(bridge, "--yes", "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"], bridge.applied()), (3, "AUTHENTICATION_REQUIRED", []))
        for change, arguments, expected, json_code in (
                (lambda b: b.settings.update(steam_authentication_mode=None), (), 3, "AUTHENTICATION_REQUIRED"),
                (lambda b: setattr(b, "mutation_block", "RESTORE_BACKUP"), (), 6, "RECOVERY_REQUIRED"),
                (lambda b: None, ("--expect-settings-revision", "9"), 3, "REVISION_CONFLICT"),
                (lambda b: None, ("--backup-after-stop",), 2, "USAGE")):
            with self.subTest(code=json_code, arguments=arguments):
                bridge = ScriptedBridge()
                change(bridge)
                code, stdout, _stderr = run_update(bridge, *arguments, "--yes", "--json")
                self.assertEqual((code, json.loads(stdout)["error"]["code"]), (expected, json_code))
                self.assertNotIn("update_workshop_items", bridge.methods())


class PhaseFiveFindingTests(unittest.TestCase):
    """QF-52 (no download claim when nothing was downloaded) and QF-53 (reads after the download keep criterion 24)."""

    def test_everything_current_claims_no_download(self) -> None:
        """No SteamCMD run: neither stream says "Mods are downloaded and checked.", also before a start review."""
        claim = "Mods are downloaded and checked."
        for arguments in ((), ("--start", "--yes")):
            with self.subTest(arguments=arguments):
                bridge = ScriptedBridge()
                bridge.update = current_result()
                code, stdout, stderr = run_update(bridge, *arguments)
                self.assertEqual(code, 0, stderr)
                self.assertNotIn(claim, stdout + stderr)
        # A real download keeps the waiter's end line
        code, _stdout, stderr = run_update(ScriptedBridge(), "--yes")
        self.assertEqual(code, 0)
        self.assertIn(claim, stderr)

    def refused_after_download(self, bridge: ScriptedBridge, *arguments: str) -> tuple[dict, str]:
        """Run the command in JSON and in text; return the JSON error and the text stderr."""
        code, stdout, _stderr = run_update(bridge(), *arguments, "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual(code, 3)
        self.assertTrue(error["message"].endswith(MODS_DOWNLOADED), error["message"])
        self.assertEqual(error["details"]["operation"]["kind"], "UPDATE_WORKSHOP_ITEMS")
        text_bridge = bridge()
        code, _stdout, stderr = run_update(text_bridge, *arguments)
        # Nothing applied: no apply operation was submitted (a D11 refusal answers the call itself)
        self.assertEqual((code, "op-2" in text_bridge.operations), (3, False))
        self.assertIn(MODS_DOWNLOADED, stderr)
        return error, stderr

    def test_a_failed_backup_choice_read_after_the_download(self) -> None:
        """`get_ui_preferences` refused after the download: exit 3 with the download sentence and the update."""
        def bridge() -> ScriptedBridge:
            scripted = ScriptedBridge()
            scripted.statuses = [RUNNING]
            scripted.hooks["update_workshop_items"] = lambda: scripted.refuse.update(
                get_ui_preferences={"code": "CONTROL_CONFLICT", "message": "busy", "retryable": True})
            return scripted
        error, _stderr = self.refused_after_download(bridge, "--restart", "--yes")
        self.assertEqual(error["code"], "CONTROL_CONFLICT")

    def test_failed_name_reads_after_a_d11_refusal(self) -> None:
        """D11 at the apply, then a failed status read (the host's refusal) or name read (the running ID)."""
        for method, expected in (("get_server_status", None), ("list_profiles", "The server runs with other.")):
            with self.subTest(method=method):
                def bridge() -> ScriptedBridge:
                    scripted = ScriptedBridge()
                    scripted.statuses = [RUNNING, RUNNING, {"state": "RUNNING_MANAGED", "profile_id": "other"}]
                    scripted.hooks["apply_mods_and_restart"] = lambda: scripted.refuse.update(
                        {method: {"code": "INTERNAL_FAILURE", "message": "read", "retryable": False}})
                    return scripted
                error, _stderr = self.refused_after_download(bridge, "--restart", "--yes")
                self.assertEqual(error["code"], "INVALID_REQUEST")
                if expected:
                    self.assertIn(expected, error["message"])
                    self.assertEqual(error["details"]["running_profile_id"], "other")

if __name__ == "__main__":
    unittest.main()
