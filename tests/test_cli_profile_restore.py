"""Task 4.3: `profile restore --archive ZIP` (10.2, 8.1, 8.2; R-5 ruling; criteria 5, 6, 8, 13 and rule 5).

The fixture's backup archive is restored as a new profile, once per storage choice. Real
compositions over the fake process table; each run is its own session.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folder_lock_fixtures import held_reader  # noqa: E402
from lifecycle_cli_fixtures import OTHER_ID, saved_state  # noqa: E402
from session_fixtures import PROFILE_ID  # noqa: E402
from test_cli_backup_commands import BackupRoot  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402
from dayz_serverman.application import folder_writer_scope, review_wording  # noqa: E402
from dayz_serverman.cli import bridge_client  # noqa: E402
from dayz_serverman.session import open_owner_session  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"
# The ID that the preview suggests for the fixture's profile, which already exists
RESTORED_ID = "livonia-main-restored"


class ProfileRestoreTests(BackupRoot):
    """`profile restore` of the fixture's backup archive."""

    def setUp(self) -> None:
        """Find the fixture's backup archive."""
        super().setUp()
        self.archive = next((self.manager / "backups").rglob("*.zip"))

    def restore(self, *arguments: str, stdin=None) -> tuple[int, str, str]:
        """Run `profile restore` of the fixture's archive."""
        return self.cli("profile", "restore", "--archive", str(self.archive), *arguments, stdin=stdin)

    def profiles(self) -> set[str]:
        """Return the IDs of the saved profile records."""
        return {path.stem for path in (self.manager / "data" / "profiles").glob("*.json")}

    def test_new_world_with_yes_in_text_and_json(self) -> None:
        """The automatic choice isolates the world; a second restore takes the given ID, name, ports and choice."""
        code, stdout, stderr = self.restore("--yes")
        self.assertEqual(code, 0, stderr)
        for text in ("Restore profile", f"Archive: {self.archive.name}", RESTORED_ID,
                     "Destination policy: New isolated mission and storage ID",
                     "Restore creates the profile. Start it separately after checking readiness.",
                     "Profile restored. Check readiness before starting."):
            self.assertIn(text, stdout)
        self.assertIn("Profile restored.", stderr)
        self.assertIn(RESTORED_ID, self.profiles())
        self.assertTrue((self.dayz / "serverman" / RESTORED_ID / "serverDZ.cfg").is_file())
        code, stdout, stderr = self.restore("--profile-id", "second", "--name", "Second World", "--game-port", "2700",
                                            "--query-port", "2705", "--storage", "new", "--yes", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual(code, 0, stderr)
        self.assertEqual((value["operations"][0]["kind"], value["operations"][0]["state"]),
                         ("RESTORE_PROFILE_FROM_BACKUP", "SUCCEEDED"))
        review = value["review"]
        self.assertEqual((review["profile"]["profile_id"], review["profile"]["display_name"], review["game_port"],
                          review["steam_query_port"], review["storage_policy"]),
                         ("second", "Second World", 2700, 2705, "allocate_new"))
        self.assertEqual(value["result"]["profile"]["profile_id"], "second")

    def test_owner_session_inspects_the_archive_again(self) -> None:
        """R-5: the owner session sends its own inspection; the preview uses that token, not the pre-step's."""
        calls: list[tuple[str, dict]] = []
        original = bridge_client.BridgeClient.call

        def recording(client, method, **parameters):
            """Record each owner call with its parameters."""
            calls.append((method, parameters))
            return original(client, method, **parameters)

        with patch.object(bridge_client.BridgeClient, "call", recording):
            code, _stdout, stderr = self.restore("--yes")
        self.assertEqual(code, 0, stderr)
        methods = [method for method, _parameters in calls]
        self.assertLess(methods.index("inspect_backup_archive"), methods.index("preview_profile_restore"))
        token = next(value for method, value in calls if method == "preview_profile_restore")["backup_id"]
        self.assertTrue(token.startswith("selected-"))
        self.assertEqual(next(value for method, value in calls if method == "restore_profile_from_backup")["backup_id"],
                         token)

    def test_replace_needs_overwrite_then_replaces_the_shared_world(self) -> None:
        """Without --overwrite: the review, exit 4, nothing changed, also with --yes. With it: the world is replaced."""
        before = saved_state(self.manager, self.dayz)
        stdin = FakeTerminal("y\n")
        code, stdout, stderr = self.restore("--storage", "replace", "--yes", stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 0))
        self.assertIn("Replace the selected world used by:", stdout)
        self.assertIn("Chernarus PvE", stdout)
        self.assertIn("A verified recovery copy will be retained.", stdout)
        self.assertIn("This restore replaces an existing world. Run the command again with --overwrite.", stderr)
        code, stdout, _stderr = self.restore("--storage", "replace", "--json", "--yes")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["review"]["affected_profile_ids"]),
                         (4, "CONFIRMATION_REQUIRED", [OTHER_ID, PROFILE_ID]))
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        players = self.dayz / "mpmissions" / "dayzOffline.enoch" / "storage_17" / "players.db"
        players.write_bytes(b"newer world")
        code, stdout, stderr = self.restore("--storage", "replace", "--overwrite", "--yes")
        self.assertEqual(code, 0, stderr)
        self.assertIn("World recovery copy: ", stdout)
        self.assertEqual(players.read_bytes(), b"world")

    def test_decline_and_missing_yes_change_nothing(self) -> None:
        """Criteria 5, 6: decline, JSON without --yes and no terminal without --yes exit 4; nothing changed."""
        before = saved_state(self.manager, self.dayz)
        stdin = FakeTerminal("\n")
        code, stdout, stderr = self.restore(stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 1))
        self.assertIn("Restore profile", stdout)
        self.assertTrue(stderr.rstrip().endswith("Nothing was changed."))
        stdin = FakeTerminal("y\n")
        code, stdout, _stderr = self.restore("--json", stdin=stdin)
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["review"]["profile"]["profile_id"], stdin.reads),
                         (4, "CONFIRMATION_REQUIRED", RESTORED_ID, 0))
        stdin = FakeTerminal("y\n", terminal=False)
        code, _stdout, _stderr = self.restore(stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 0))
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        self.assertNotIn(RESTORED_ID, self.profiles())

    def test_refusals_before_the_question(self) -> None:
        """Rule 5: an occupied original world (2), a recovery block (6) and a running server (3) ask nothing."""
        before = saved_state(self.manager, self.dayz)
        stdin = FakeTerminal("y\n", terminal=False)
        code, stdout, _stderr = self.restore("--storage", "preserve", "--json", stdin=stdin)
        self.assertEqual((code, json.loads(stdout)["error"]["code"], stdin.reads), (2, "INVALID_REQUEST", 0))
        self.broken_journal()
        stdin = FakeTerminal("y\n", terminal=False)
        code, stdout, _stderr = self.restore("--json", stdin=stdin)
        self.assertEqual((code, json.loads(stdout)["error"]["code"], stdin.reads), (6, "RECOVERY_REQUIRED", 0))
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        (self.manager / "data" / "operations" / "restore-journals" / "broken.json").unlink()
        self.started()
        before = saved_state(self.manager, self.dayz)
        for mode in ((), ("--json", "--yes")):
            with self.subTest(mode=mode):
                stdin = FakeTerminal("y\n", terminal=False)
                code, stdout, stderr = self.restore(*mode, stdin=stdin)
                self.assertEqual((code, stdin.reads), (3, 0), stderr)
                self.assertNotIn("Restore profile", stdout)
                # Criterion 31 (QF-40): the state sentence of backup restore, not the second-manager text
                running = "This cannot be done while the server is running."
                if mode:
                    error = json.loads(stdout)["error"]
                    self.assertEqual((error["code"], error["message"]), ("CONTROL_CONFLICT", running))
                else:
                    self.assertIn(running, stderr)
                    self.assertNotIn("Another DayZ-ServerMan", stderr)
        self.assertEqual((self.table.stops, saved_state(self.manager, self.dayz)), (0, before))

    def test_arguments_and_the_instance_lock(self) -> None:
        """A missing archive and --overwrite without replace exit 2; a held instance lock exits 3."""
        before = saved_state(self.manager, self.dayz)
        code, stdout, _stderr = self.cli("profile", "restore", "--archive", str(self.dayz / "missing.zip"), "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (2, "INVALID_REQUEST"))
        code, stdout, _stderr = self.restore("--overwrite", "--yes", "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (2, "USAGE"))
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            code, stdout, _stderr = self.restore("--yes", "--json")
            self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "INSTANCE_ACTIVE"))
        finally:
            holder.close(drain_seconds=5)
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_a_reader_inside_the_server_files_refuses_with_nothing_changed(self) -> None:
        """A13 (R-5): a held observer read refuses before staging: exit 3, no profile, no generated folder."""
        before = saved_state(self.manager, self.dayz)
        with held_reader(self.manager / "data" / "server-folders.lock"), \
                patch.object(folder_writer_scope, "OWNER_WRITER_WAIT_SECONDS", 0.3):
            code, stdout, stderr = self.restore("--yes", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["operation"]["last_working_phase"]),
                         (3, "CONTROL_CONFLICT", None), stderr)
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        self.assertFalse((self.dayz / "serverman" / RESTORED_ID).exists())


class ProfileRestoreWordingTests(unittest.TestCase):
    """The copied texts appear unchanged in the frontend; the review and the result follow criterion 18."""

    def test_copies_match_the_frontend(self) -> None:
        """profile_restore.js and diagnostic_labels.js hold every copied text."""
        source = (FRONTEND / "profile_restore.js").read_text(encoding="utf-8")
        for text in (review_wording.PROFILE_RESTORE_TITLE, review_wording.PROFILE_RESTORE_SENTENCE,
                     review_wording.EXECUTABLE_MISSING, review_wording.PROFILE_RESTORED_CLEANUP,
                     review_wording.PROFILE_RESTORED_CHECK, *review_wording.PROFILE_RESTORE_LABELS.values()):
            self.assertIn(f'"{text}', source)
        for text in (review_wording.REPLACE_WORLD_LEAD, review_wording.REPLACE_WORLD_TAIL,
                     f"`{review_wording.PROFILE_RESTORED} $", f" {review_wording.WORLD_RECOVERY_COPY}: "):
            self.assertIn(text, source)
        labels = (FRONTEND / "diagnostic_labels.js").read_text(encoding="utf-8")
        for key, text in review_wording.STORAGE_POLICIES.items():
            self.assertIn(f'{key}: "{text}"', labels)
        self.assertIn(f'storagePolicyLabels, policy, "{review_wording.STORAGE_POLICY_FALLBACK}"', labels)

    def test_review_and_result_follow_the_input_name_rules(self) -> None:
        """IDs only in the ID column; paths, names and ports are stored data; the overwrite sentence types a flag."""
        from test_cli_input_names import InputNameChecker, flags
        from dayz_serverman.cli import wording
        from dayz_serverman.cli.commands.profile_restore import result_lines
        from dayz_serverman.cli.review import profile_restore_review
        preview = {"profile": {"profile_id": RESTORED_ID, "display_name": "Livonia", "server_config": "a\\b.cfg",
                               "runtime_profile": "a\\profile"}, "mission_root": "mpmissions\\x.enoch",
                   "instance_id": 17, "game_port": 2502, "steam_query_port": 2505, "storage_policy": "replace_existing",
                   "affected_profile_ids": [OTHER_ID, PROFILE_ID], "missing_mods": ["@Mod"],
                   "server_executable_present": False, "warnings": ["Missing mods must be installed before starting."]}
        blocks = profile_restore_review(preview, "x.zip", {OTHER_ID: "Chernarus PvE"})
        blocks += result_lines({"profile": preview["profile"], "recovery_copy": "C:\\copy", "cleanup_pending": True})
        blocks += [wording.overwrite_needed()]
        InputNameChecker(self, flags() | {PROFILE_ID, OTHER_ID, RESTORED_ID}, []).blocks(blocks)


if __name__ == "__main__":
    unittest.main()
