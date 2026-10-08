"""Task 5.3: `mods update` through real compositions (10.3; criteria 4, 8, 15, 24, 26; A13 with the QF-2 ruling).

Every bridge handler, the lane, the D11 wrapper, the apply-and-restart coordinator with its A13
writer side and the lifecycle over the fake process table are real. Two cases run the real
update and review services on the fixture's Workshop cache: the "everything current" decision
sends nothing to SteamCMD. The other cases replace the download and the copy with recorders.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folder_lock_fixtures import held_reader  # noqa: E402
from lifecycle_cli_fixtures import OTHER_ID, saved_state  # noqa: E402
from steam_cli_fixtures import WORKSHOP_ID, SteamRoot  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402
from dayz_serverman.application import folder_writer_scope  # noqa: E402
from dayz_serverman.application.mod_publication import ModPublicationService  # noqa: E402
from dayz_serverman.application.workshop_update_result import update_result  # noqa: E402
from dayz_serverman.application.workshop_updates import WorkshopUpdateService  # noqa: E402
from dayz_serverman.cli.wording import MODS_DOWNLOADED  # noqa: E402
from dayz_serverman.session import open_owner_session  # noqa: E402

PROFILE = ("--profile", "livonia-main")
FINGERPRINT = "c" * 64


class RealReviewTests(SteamRoot):
    """The real update and review services on the fixture's cache: no SteamCMD run, nothing applied."""

    def test_everything_current_sends_nothing_to_steamcmd(self) -> None:
        """The remote time equals the cache: no download; the real review follows; no `--yes` exits 4."""
        self.workshop_cache()
        self.catalog.times[WORKSHOP_ID] = 100
        before = saved_state(self.manager, self.dayz)
        code, stdout, stderr = self.cli("mods", "update", *PROFILE, "--json")
        self.assertEqual(self.steamcmd.updates, [])
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"]), (4, "CONFIRMATION_REQUIRED"), stderr)
        update = error["details"]["operation"]
        self.assertEqual((update["kind"], update["result"]["download_state"], update["result"]["process_id"]),
                         ("UPDATE_WORKSHOP_ITEMS", "VERIFIED", None))
        self.assertEqual(error["details"]["review"]["targets"][0]["workshop_id"], WORKSHOP_ID)
        self.assertTrue(error["message"].endswith(MODS_DOWNLOADED))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_a_newer_remote_item_runs_steamcmd_once(self) -> None:
        """A newer remote time sends the item to the SteamCMD fake; its run downloads nothing, so it is unverified."""
        self.workshop_cache()
        code, stdout, _stderr = self.cli("mods", "update", *PROFILE, "--json")
        self.assertEqual(len(self.steamcmd.updates), 1)
        self.assertIn(WORKSHOP_ID, " ".join(self.steamcmd.updates[0]))
        error = json.loads(stdout)["error"]
        self.assertIn(code, (1, 4))
        self.assertEqual(error["details"]["operation"]["kind"], "UPDATE_WORKSHOP_ITEMS")


class ApplyThroughCompositionsTests(SteamRoot):
    """The apply and the apply-and-restart through the real handlers; the download and the copy are recorders."""

    def setUp(self) -> None:
        """Replace the download, the review plan and the copy with recorders."""
        super().setUp()
        self.publishes: list[dict] = []
        test = self

        def update(_service, request, context):
            """A verified download of the fixture's mod."""
            context.checkpoint("preflight", 5)
            result = update_result(request, (), "VERIFIED", process_id=9, steamcmd_exit_code=0)
            result["items"] = [{"item": {"workshop_id": WORKSHOP_ID}, "outcome": "UPDATED_VERIFIED",
                                "cache_proof": {"verification_kind": "FULL_CONTENT"}, "error_code": None}]
            return result

        def preview(_service, request):
            """A plan that writes the fixture's mod folder."""
            return {"profile_id": request.profile_id, "profile_revision": request.profile_revision,
                    "semantic_profile_digest": request.semantic_profile_digest,
                    "settings_revision": request.settings_revision,
                    "update_operation_id": request.update_operation_id, "publication_fingerprint": FINGERPRINT,
                    "targets": [{"workshop_id": WORKSHOP_ID, "target_relative": "@Community Framework",
                                 "current": False}], "key_count": 1, "missing_key_count": 0,
                    "plain_apply_guarded": True}

        def publish(_service, request, fingerprint, context, *, writer_scope=None):
            """Record the reviewed apply; the restart passes its writer side."""
            context.checkpoint("PUBLICATION_PREFLIGHT", 5)
            test.publishes.append({"fingerprint": fingerprint, "scope": writer_scope is not None,
                                   "running": bool(test.table.snapshot.processes)})
            return {"profile_id": request.profile_id, "publication_state": "PUBLISHED", "start_state": "NOT_REQUESTED"}

        for owner, name, function in ((WorkshopUpdateService, "update", update), (ModPublicationService, "preview", preview),
                                      (ModPublicationService, "publish", publish),
                                      (ModPublicationService, "confirm_restart_plan", lambda *_args: True)):
            patcher = patch.object(owner, name, function)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_plain_apply_on_a_stopped_server(self) -> None:
        """The real handlers take the CLI's parameter sets; the reviewed fingerprint reaches the copy."""
        code, stdout, stderr = self.cli("mods", "update", *PROFILE, "--yes", "--json")
        self.assertEqual(code, 0, stderr)
        value = json.loads(stdout)["value"]
        self.assertEqual([record["kind"] for record in value["operations"]],
                         ["UPDATE_WORKSHOP_ITEMS", "PUBLISH_MODS_AND_KEYS"])
        self.assertEqual(self.publishes, [{"fingerprint": FINGERPRINT, "scope": False, "running": False}])

    def test_apply_and_restart_stops_then_copies(self) -> None:
        """`--restart`: the server is stopped before the copy, under the writer side taken before the stop."""
        self.started()
        code, _stdout, stderr = self.cli("mods", "update", *PROFILE, "--restart", "--no-backup-after-stop", "--yes")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.table.stops, 1)
        self.assertEqual(self.publishes, [{"fingerprint": FINGERPRINT, "scope": True, "running": False}])

    def test_a_held_reader_refuses_before_the_stop(self) -> None:
        """A13 (QF-2): a held observer read refuses the restart at preflight: exit 3, server running, nothing applied."""
        self.started()
        before = saved_state(self.manager, self.dayz)
        with held_reader(self.manager / "data" / "server-folders.lock"), \
                patch.object(folder_writer_scope, "OWNER_WRITER_WAIT_SECONDS", 0.3):
            code, stdout, stderr = self.cli("mods", "update", *PROFILE, "--restart", "--yes", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["operation"]["last_working_phase"]),
                         (3, "CONTROL_CONFLICT", "preflight"), stderr)
        self.assertTrue(error["message"].endswith(MODS_DOWNLOADED))
        self.assertEqual((self.table.stops, self.publishes), (0, []))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_restart_of_another_profile_refuses_before_the_download(self) -> None:
        """Criterion 26 in another session after adoption: exit 3 with the D11 reason; no download, no stop."""
        code, _stdout, stderr = self.cli("server", "start", "--profile", OTHER_ID, "--yes")
        self.assertEqual(code, 0, stderr)
        before = saved_state(self.manager, self.dayz)
        for mode in ((), ("--json",)):
            with self.subTest(mode=mode):
                code, stdout, stderr = self.cli("mods", "update", *PROFILE, "--restart", "--yes", *mode)
                self.assertEqual(code, 3)
                text = json.loads(stdout)["error"]["message"] if mode else stderr
                self.assertIn("The server runs with Chernarus PvE.", text)
        operations = list((self.manager / "data" / "operations").glob("*.json"))
        self.assertFalse(any("UPDATE_WORKSHOP_ITEMS" in path.read_text(encoding="utf-8") for path in operations))
        self.assertEqual((self.table.stops, self.publishes, saved_state(self.manager, self.dayz)), (0, [], before))

    def test_another_instance_and_a_decline_change_nothing(self) -> None:
        """D2 exits 3 before the download; a decline after the download exits 4 with nothing applied."""
        before = saved_state(self.manager, self.dayz)
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            code, stdout, _stderr = self.cli("mods", "update", *PROFILE, "--yes", "--json")
            self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "INSTANCE_ACTIVE"))
        finally:
            holder.close(drain_seconds=5)
        code, _stdout, stderr = self.cli("mods", "update", *PROFILE, stdin=FakeTerminal("n\n"))
        self.assertEqual(code, 4)
        self.assertTrue(stderr.splitlines()[-1].endswith(MODS_DOWNLOADED), stderr)
        self.assertEqual((self.publishes, saved_state(self.manager, self.dayz)), ([], before))
