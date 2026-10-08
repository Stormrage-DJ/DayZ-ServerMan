"""Task 4.1: `backup create` and `backup recover` (10.2, criteria 5, 6, 8, 10, 13, 27 and 30).

Real compositions over the fake process table of `lifecycle_cli_fixtures`; each command run is
its own session. "Changed nothing" compares the saved manager data and the DayZ root (Q1).
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lifecycle_cli_fixtures import LifecycleRoot, saved_state  # noqa: E402
from profile_fixtures import profile_payload  # noqa: E402
from session_fixtures import PROFILE_ID, run_lane  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402
from dayz_serverman.application import review_wording  # noqa: E402
from dayz_serverman.cli import read_wording  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.lifecycle import InventorySnapshot  # noqa: E402
from dayz_serverman.session import open_owner_session  # noqa: E402

PROFILE = ["--profile", PROFILE_ID]
FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"
# A profile without a runtime profile directory: the window turns Create backup off for it
NO_RUNTIME_ID = "no-runtime"


class BackupRoot(LifecycleRoot):
    """The lifecycle root with helpers for backups and restore journals."""

    def zips(self) -> int:
        """Count the backup archives."""
        return len(list((self.manager / "backups").rglob("*.zip")))

    def broken_journal(self) -> None:
        """Leave an unreadable restore journal, so the next owner session blocks changes for recovery."""
        journals = self.manager / "data" / "operations" / "restore-journals"
        journals.mkdir(parents=True, exist_ok=True)
        (journals / "broken.json").write_text("{}", encoding="utf-8")

    def add_profile_without_runtime(self) -> None:
        """Save a profile that has no runtime profile directory."""
        composition = build_composition(self.manager)
        try:
            saved = run_lane(composition, "save_profile", {"profile": profile_payload(
                profile_id=NO_RUNTIME_ID, display_name="No Runtime", game_port=2602, runtime_profile=None,
                server_config=rf"serverman\{PROFILE_ID}\serverDZ.cfg"), "expected_revision": None})
            self.assertEqual(saved.state.value, "SUCCEEDED", saved)
        finally:
            composition.shutdown.request_shutdown()
            composition.shutdown.wait_for_close(5)


class BackupCreateTests(BackupRoot):
    """`backup create` confirms like the window's "Create backup?" dialog (criterion 30)."""

    def test_create_with_yes_in_text_and_json(self) -> None:
        """--yes prints the review and creates one verified backup; JSON holds the request and the result."""
        before = self.zips()
        code, stdout, stderr = self.cli("backup", "create", *PROFILE, "--yes")
        self.assertEqual(code, 0, stderr)
        self.assertIn("Create backup?", stdout)
        self.assertIn("Server: Livonia Közösségi. Its configuration, runtime, complete mission and world persistence "
                      "will be copied and verified. Mod directories are not included.", stdout)
        self.assertIn("Backup created.", stderr)
        self.assertEqual(self.zips(), before + 1)
        code, stdout, stderr = self.cli("backup", "create", *PROFILE, "--yes", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual(code, 0, stderr)
        self.assertEqual((value["operations"][0]["kind"], value["operations"][0]["state"]),
                         ("CREATE_BACKUP", "SUCCEEDED"))
        self.assertEqual(value["review"]["profile_id"], PROFILE_ID)
        self.assertEqual(value["result"]["restore_compatibility"], "COMPATIBLE")
        self.assertEqual(self.zips(), before + 2)

    def test_question_decline_and_missing_yes_change_nothing(self) -> None:
        """Criteria 5, 6, 30: decline, JSON without --yes, no terminal without --yes: exit 4, nothing changed."""
        before = saved_state(self.manager, self.dayz)
        stdin = FakeTerminal("n\n")
        code, stdout, stderr = self.cli("backup", "create", *PROFILE, stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 1))
        self.assertIn("Create backup?", stdout)
        self.assertIn("Continue? [y/N]", stderr)
        self.assertTrue(stderr.rstrip().endswith("Nothing was changed."))
        stdin = FakeTerminal("y\n")
        code, stdout, _stderr = self.cli("backup", "create", *PROFILE, "--json", stdin=stdin)
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["review"]["profile_id"], stdin.reads),
                         (4, "CONFIRMATION_REQUIRED", PROFILE_ID, 0))
        stdin = FakeTerminal("y\n", terminal=False)
        code, stdout, stderr = self.cli("backup", "create", *PROFILE, stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 0))
        self.assertIn("Run the command again with --yes.", stderr)
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        stdin = FakeTerminal("yes\n")
        code, _stdout, stderr = self.cli("backup", "create", *PROFILE, stdin=stdin)
        self.assertEqual(code, 0, stderr)

    def test_state_refusals_come_before_the_question(self) -> None:
        """Rule 5: a recovery block exits 6 and a profile without a runtime profile exits 3; no question asked."""
        self.add_profile_without_runtime()
        before = saved_state(self.manager, self.dayz)
        for mode in ((), ("--json",), ("--yes",)):
            with self.subTest(mode=mode):
                stdin = FakeTerminal("y\n", terminal=False)
                code, stdout, stderr = self.cli("backup", "create", "--profile", NO_RUNTIME_ID, *mode, stdin=stdin)
                self.assertEqual((code, stdin.reads), (3, 0))
                self.assertNotIn("Create backup?", stdout)
                if mode == ("--json",):
                    self.assertEqual(json.loads(stdout)["error"]["code"], "RUNTIME_PROFILE_UNRESOLVED")
                else:
                    self.assertIn("Set a runtime profile directory with profile edit before creating a backup.", stderr)
        self.broken_journal()
        for mode in ((), ("--json",)):
            with self.subTest(mode=mode, block=True):
                stdin = FakeTerminal("y\n", terminal=False)
                code, stdout, stderr = self.cli("backup", "create", *PROFILE, *mode, stdin=stdin)
                self.assertEqual((code, stdin.reads), (6, 0))
                if mode:
                    self.assertEqual(json.loads(stdout)["error"]["code"], "RECOVERY_REQUIRED")
                else:
                    self.assertNotIn("Continue?", stderr)
                    self.assertIn("backup recover", stderr)
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_missing_data_behind_valid_names_exits_1(self) -> None:
        """Criterion 27: the profile resolves, but its mission folder is gone: the operation fails (1)."""
        shutil.rmtree(self.dayz / "mpmissions" / "dayzOffline.enoch")
        before = self.zips()
        code, stdout, _stderr = self.cli("backup", "create", *PROFILE, "--yes", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["operation"]["kind"]),
                         (1, "STORAGE_FAILURE", "CREATE_BACKUP"))
        self.assertEqual(self.zips(), before)

    def test_names_and_the_instance_lock(self) -> None:
        """An unknown --profile exits 2; with the window holding the lock both writes exit 3; nothing changed."""
        before = saved_state(self.manager, self.dayz)
        code, stdout, _stderr = self.cli("backup", "create", "--profile", "nobody", "--yes", "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (2, "USAGE"))
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            for line in (["backup", "create", *PROFILE, "--yes"], ["backup", "recover"]):
                with self.subTest(line=line):
                    code, stdout, _stderr = self.cli(*line, "--json")
                    self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "INSTANCE_ACTIVE"))
        finally:
            holder.close(drain_seconds=5)
        self.assertEqual(saved_state(self.manager, self.dayz), before)


class BackupRecoverTests(BackupRoot):
    """`backup recover` runs the Backups page's recovery inspection as a writing command."""

    def test_nothing_to_recover(self) -> None:
        """No journal: exit 0 in text and JSON."""
        code, stdout, stderr = self.cli("backup", "recover")
        self.assertEqual(code, 0, stderr)
        self.assertIn("No unfinished restore blocks changes.", stdout)
        code, stdout, _stderr = self.cli("backup", "recover", "--json")
        self.assertEqual((code, json.loads(stdout)["value"]["result"]["blocked"]), (0, False))

    def test_server_not_stopped_exits_3_and_unsafe_journal_exits_6(self) -> None:
        """A running server refuses the recovery (3, nothing changed); an unreadable journal stays blocked (6)."""
        self.started()
        self.broken_journal()
        before = saved_state(self.manager, self.dayz)
        code, stdout, stderr = self.cli("backup", "recover")
        self.assertEqual(code, 3, stderr)
        self.assertIn("Stop the server, then run backup recover or restart DayZ-ServerMan.", stderr)
        code, stdout, _stderr = self.cli("backup", "recover", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["recovery"]["blocked"]), (3, "CONTROL_CONFLICT", True))
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        # The block also refuses `server stop`; the server ends on its own
        self.table.snapshot = InventorySnapshot(())
        code, stdout, stderr = self.cli("backup", "recover")
        self.assertEqual(code, 6, stderr)
        self.assertIn("Recovery required. Changes are blocked until an unfinished restore is resolved.", stderr)
        self.assertIn("Details: run logs --source diagnostics.", stderr)
        code, stdout, _stderr = self.cli("backup", "recover", "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (6, "RECOVERY_REQUIRED"))


class BackupWordingTests(unittest.TestCase):
    """Each copied text appears unchanged in the frontend file that the module names."""

    def test_copies_match_the_frontend(self) -> None:
        """backups.js holds the dialog title and body; restore.js the two recovery notices."""
        backups = (FRONTEND / "backups.js").read_text(encoding="utf-8")
        self.assertIn(f'"{review_wording.BACKUP_CREATE_TITLE}"', backups)
        self.assertIn("`Server: ${profile.display_name}. " + review_wording.BACKUP_CREATE_BODY + "`", backups)
        restore = (FRONTEND / "restore.js").read_text(encoding="utf-8")
        self.assertIn('"' + read_wording.RESTORE_RECOVERY_TEXT.replace("resolved. ", 'resolved. "\n  + "'), restore)
        self.assertIn(f'"{read_wording.RESTORE_DEFERRED_TEXT}"', restore)

    def test_sentences_follow_the_input_name_rules(self) -> None:
        """Criterion 18: the review and the sentences of 4.1 hold input names only as text to type."""
        from test_cli_input_names import InputNameChecker, flags
        from dayz_serverman.cli import wording
        from dayz_serverman.cli.review import backup_create_review
        checker = InputNameChecker(self, flags() | {PROFILE_ID}, [])
        checker.blocks([*backup_create_review({"profile_id": PROFILE_ID, "display_name": "Livonia Main"}),
                        wording.no_runtime_profile(), wording.recovery_clear(),
                        wording.way_out(read_wording.RESTORE_RECOVERY_TEXT)])


if __name__ == "__main__":
    unittest.main()
