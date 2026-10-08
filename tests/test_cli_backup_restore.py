"""Task 4.2: `backup restore ID` (10.2, criteria 4, 5, 6, 8, 13, 27 and rule 5; A13 writer side).

The preview is the review and needs a stopped server, so a running server is refused before
the question. Real compositions over the fake process table; each run is its own session.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from folder_lock_fixtures import held_reader  # noqa: E402
from session_fixtures import PROFILE_ID  # noqa: E402
from test_cli_backup_commands import BackupRoot  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402
from lifecycle_cli_fixtures import saved_state  # noqa: E402
from dayz_serverman.application import folder_writer_scope, review_wording  # noqa: E402
from dayz_serverman.cli import presteps  # noqa: E402
from dayz_serverman.cli.exit_codes import PRE_CHANGE_PHASES  # noqa: E402
from dayz_serverman.session import open_owner_session  # noqa: E402

PROFILE = ["--profile", PROFILE_ID]
FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"
GLOBALS = Path("mpmissions") / "dayzOffline.enoch" / "db" / "globals.xml"


class BackupRestoreTests(BackupRoot):
    """`backup restore` against the backup that the fixture created."""

    def setUp(self) -> None:
        """Find the fixture's backup and change one of its targets, so a restore has work to do."""
        super().setUp()
        self.archive = next((self.manager / "backups").rglob("*.zip"))
        self.backup_id = self.archive.stem
        self.original = (self.dayz / GLOBALS).read_bytes()
        (self.dayz / GLOBALS).write_bytes(b"changed after the backup")

    def restore(self, *arguments: str, stdin=None) -> tuple[int, str, str]:
        """Run `backup restore` of the fixture's backup."""
        return self.cli("backup", "restore", self.backup_id, *PROFILE, *arguments, stdin=stdin)

    def test_review_and_restore_with_yes(self) -> None:
        """--yes prints the Backups page's review and dialog, restores, and the target holds the backup's bytes."""
        code, stdout, stderr = self.restore("--yes")
        self.assertEqual(code, 0, stderr)
        for text in ("Profile: Livonia Közösségi", "Review backup from ", "3 replacements; 0 creations.",
                     "DayZ configuration — replace: mpmissions/dayzOffline.enoch/db/globals.xml",
                     "Restore this backup?", "DayZ configuration files will be replaced. Verified recovery copies "
                     "are created first.", "Restore completed and every published target verified."):
            self.assertIn(text, stdout)
        self.assertIn("Backup restored.", stderr)
        self.assertEqual((self.dayz / GLOBALS).read_bytes(), self.original)
        (self.dayz / GLOBALS).write_bytes(b"changed again")
        code, stdout, stderr = self.restore("--yes", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual(code, 0, stderr)
        self.assertEqual((value["operations"][0]["kind"], value["operations"][0]["state"]),
                         ("RESTORE_BACKUP", "SUCCEEDED"))
        self.assertEqual((value["review"]["backup_id"], value["review"]["replacement_count"]), (self.backup_id, 3))
        self.assertEqual((self.dayz / GLOBALS).read_bytes(), self.original)

    def test_decline_and_missing_yes_change_nothing(self) -> None:
        """Criteria 5, 6: decline, JSON without --yes and no terminal without --yes exit 4; nothing changed."""
        before = saved_state(self.manager, self.dayz)
        stdin = FakeTerminal("no\n")
        code, stdout, stderr = self.restore(stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 1))
        self.assertIn("Restore this backup?", stdout)
        self.assertTrue(stderr.rstrip().endswith("Nothing was changed."))
        stdin = FakeTerminal("y\n")
        code, stdout, _stderr = self.restore("--json", stdin=stdin)
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["review"]["backup_id"], stdin.reads),
                         (4, "CONFIRMATION_REQUIRED", self.backup_id, 0))
        self.assertIn("fingerprint", error["details"]["review"])
        stdin = FakeTerminal("y\n", terminal=False)
        code, _stdout, stderr = self.restore(stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 0))
        self.assertIn("Run the command again with --yes.", stderr)
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_running_server_and_recovery_block_refuse_before_the_question(self) -> None:
        """Rule 5: one status read (the preview) shows the server running: exit 3; a recovery block: exit 6."""
        self.started()
        before = saved_state(self.manager, self.dayz)
        for mode in ((), ("--json", "--yes")):
            with self.subTest(mode=mode):
                stdin = FakeTerminal("y\n", terminal=False)
                code, stdout, stderr = self.restore(*mode, stdin=stdin)
                self.assertEqual((code, stdin.reads), (3, 0), stderr)
                self.assertNotIn("Restore this backup?", stdout)
                if mode:
                    self.assertEqual(json.loads(stdout)["error"]["code"], "CONTROL_CONFLICT")
                else:
                    self.assertIn("This cannot be done while the server is running.", stderr)
        self.assertEqual((self.table.stops, saved_state(self.manager, self.dayz)), (0, before))
        self.broken_journal()
        stdin = FakeTerminal("y\n", terminal=False)
        code, stdout, _stderr = self.restore("--json", stdin=stdin)
        self.assertEqual((code, json.loads(stdout)["error"]["code"], stdin.reads), (6, "RECOVERY_REQUIRED", 0))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_names_data_and_the_instance_lock(self) -> None:
        """Unknown ID: 2. The archive goes away after the ID check: 1 (criterion 27). Held lock: 3."""
        code, stdout, _stderr = self.cli("backup", "restore", "no-such-backup", *PROFILE, "--yes", "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (2, "USAGE"))
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            before = saved_state(self.manager, self.dayz)
            code, stdout, _stderr = self.restore("--yes", "--json")
            self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "INSTANCE_ACTIVE"))
            self.assertEqual(saved_state(self.manager, self.dayz), before)
        finally:
            holder.close(drain_seconds=5)
        checked = presteps.backup_id

        def then_removed(*arguments):
            """Confirm the ID, then lose the archive before the owner session reads it."""
            resolved = checked(*arguments)
            self.archive.unlink()
            return resolved

        with patch.object(presteps, "backup_id", then_removed):
            code, stdout, _stderr = self.restore("--yes", "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (1, "NOT_FOUND"))
        self.assertEqual((self.dayz / GLOBALS).read_bytes(), b"changed after the backup")

    def test_a_reader_inside_the_server_files_refuses_with_nothing_changed(self) -> None:
        """A13 writer side: a held observer read keeps the restore out; exit 3 and the files stay as they were."""
        before = saved_state(self.manager, self.dayz)
        with held_reader(self.manager / "data" / "server-folders.lock"), \
                patch.object(folder_writer_scope, "OWNER_WRITER_WAIT_SECONDS", 0.3):
            code, stdout, stderr = self.restore("--yes", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"]), (3, "CONTROL_CONFLICT"), stderr)
        # The refusal leaves the record at the last staging phase, before the first change (6.5)
        self.assertIn(error["details"]["operation"]["last_working_phase"], PRE_CHANGE_PHASES["RESTORE_BACKUP"])
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        self.assertEqual(list((self.manager / "data" / "operations" / "restore-journals").glob("*.json")), [])


class RestoreWordingTests(unittest.TestCase):
    """The copied texts appear unchanged in the frontend; the review follows criterion 18."""

    def test_copies_match_the_frontend(self) -> None:
        """restore.js and diagnostic_labels.js hold every copied text."""
        restore = (FRONTEND / "restore.js").read_text(encoding="utf-8")
        title, warning = review_wording.RESTORE_DIALOG
        for text in (title, review_wording.RESTORE_VERIFIED, review_wording.RESTORE_TARGET_FALLBACK,
                     *review_wording.RESTORE_TARGET_KINDS.values()):
            self.assertIn(f'"{text}"', restore)
        self.assertIn(f'"{warning}"', restore.replace('",\n    "', ""))
        self.assertIn(f"`{review_wording.RESTORE_REVIEW_TITLE}$" + "{", restore)
        labels = (FRONTEND / "diagnostic_labels.js").read_text(encoding="utf-8")
        for key, text in review_wording.RESTORE_ACTIONS.items():
            self.assertIn(f'{key}: "{text}"', labels)
        self.assertIn(f'restoreActionLabels, action, "{review_wording.RESTORE_ACTION_FALLBACK}"', labels)

    def test_review_follows_the_input_name_rules(self) -> None:
        """Targets and dates are stored data; the rest is operator prose."""
        from test_cli_input_names import InputNameChecker, flags
        from dayz_serverman.cli.commands.common import local_time
        from dayz_serverman.cli.review import restore_review
        preview = {"created_at": "2026-10-07T20:00:00Z", "replacement_count": 1, "creation_count": 1,
                   "recovery_plan": "Existing targets receive verified recovery copies.",
                   "targets": [{"target_kind": "RUNTIME_PROFILE", "action": "CREATE", "target_relative": "a/b.txt"},
                               {"target_kind": "DAYZ_CONFIGURATION", "action": "REPLACE",
                                "target_relative": "serverman/livonia-main/serverDZ.cfg"}]}
        InputNameChecker(self, flags() | {PROFILE_ID}, []).blocks(restore_review(preview, local_time))


if __name__ == "__main__":
    unittest.main()
