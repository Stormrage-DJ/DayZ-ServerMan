"""Task 7.2: `migrate preview` and `migrate apply` with the legacy fixtures (10.4; criteria 5, 6, 8, 13, 14, 19, 27).

Real compositions over the lifecycle fixture and a legacy folder of `migration_test_support`;
each command run is its own session, and each selects, previews and (for apply) imports in that
one process. The fixture's DayZ server folder differs from the legacy one, so the settings item
needs review; the one legacy profile can be imported.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from edit_cli_fixtures import ChangingTerminal, EditRoot, FakeTerminal  # noqa: E402
from folder_lock_fixtures import tree_hashes  # noqa: E402
from lifecycle_cli_fixtures import saved_state  # noqa: E402
from migration_test_support import MigrationTestCase  # noqa: E402
from test_cli_input_names import InputNameChecker, RecordingOutput, flags  # noqa: E402
from dayz_serverman.application import review_wording  # noqa: E402
from dayz_serverman.cli import migrate_review, runner  # noqa: E402
from dayz_serverman.session import open_owner_session  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"
SETTINGS_ITEM = "settings:dayz-installation"
PROFILE_ITEM = "profile:dayz_server_manager-profiles/Main.json"


class MigrateRoot(EditRoot):
    """The lifecycle root and a legacy folder with one importable profile."""

    def setUp(self) -> None:
        """Write the legacy folder beside the roots."""
        super().setUp()
        self.legacy = self.manager.parent / "Légacy Server"
        (self.legacy / "dayz_server_manager").mkdir(parents=True)
        (self.legacy / "dayz_server_manager-profiles").mkdir()
        (self.legacy / "DayZServer_x64.exe").write_bytes(b"synthetic")
        MigrationTestCase.create_launch_paths(self)  # type: ignore[arg-type]
        self.profile_file = self.legacy / "dayz_server_manager-profiles" / "Main.json"
        self.profile_file.write_text(json.dumps(MigrationTestCase.usable_profile(None)),  # type: ignore[arg-type]
                                     encoding="utf-8")

    def state(self) -> tuple[dict, dict]:
        """Return the saved manager data with the DayZ root, and the migration folder with the legacy folder."""
        return (saved_state(self.manager, self.dayz),
                tree_hashes(self.manager / "data" / "migrations", self.legacy))

    def checked_names(self, *arguments: str, stdin=None) -> tuple[int, str, str]:
        """Run one command line and check its text against the input-name rules of 11.3 (criterion 18)."""
        RecordingOutput.made = []
        with patch.object(runner, "Output", RecordingOutput):
            result = self.cli(*arguments, stdin=stdin)
        checker = InputNameChecker(self, flags() | {PROFILE_ITEM, SETTINGS_ITEM}, list(arguments))
        for output in RecordingOutput.made:
            checker.blocks(output.written)
        return result


class MigratePreviewTests(MigrateRoot):
    """`migrate preview ROOT`: an observer read that writes nothing (risk row "preview writes nothing")."""

    def test_preview_in_text_and_json_writes_nothing(self) -> None:
        """The items with their IDs, summaries and conflicts; the JSON value; the manager tree unchanged."""
        before = tree_hashes(self.manager, self.dayz, self.legacy)
        code, stdout, stderr = self.checked_names("migrate", "preview", str(self.legacy))
        self.assertEqual(code, 0, stderr)
        for text in ("Legacy folder:", "Item", "Name", "ID", "Can import", review_wording.MIGRATION_SETTINGS_TITLE,
                     "Máin Server", PROFILE_ITEM, SETTINGS_ITEM, "Máin Server: 3 ordered mods; runtime profile",
                     "Needs review: The current DayZ server folder is already configured differently.",
                     review_wording.MIGRATION_BACKUP_TITLE, review_wording.MIGRATION_SOURCE_NOTE):
            self.assertIn(text, stdout)
        self.assertNotIn(review_wording.MIGRATION_DIALOG, stdout)
        # Host warnings name each field by its operator words, never by its field name (QF-72)
        for text in ("Removed redundant legacy argument for the server config.",
                     "Removed redundant legacy argument for the client mods."):
            self.assertIn(text, stdout)
        self.assertNotIn(migrate_review.WARNING_FALLBACK, stdout)
        self.assertNotIn("config_path", stdout)
        code, stdout, stderr = self.cli("migrate", "preview", str(self.legacy), "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual(code, 0, stderr)
        self.assertEqual((value["selection"]["profile_count"], value["preview"]["profiles"][0]["item_id"],
                          value["preview"]["settings"]["selectable"]), (1, PROFILE_ITEM, False))
        self.assertEqual(tree_hashes(self.manager, self.dayz, self.legacy), before)

    def test_runs_while_another_instance_holds_the_lock(self) -> None:
        """Criterion 13 (read half): the preview runs while the window holds the lock."""
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            self.assertEqual(self.cli("migrate", "preview", str(self.legacy))[0], 0)
        finally:
            holder.close(drain_seconds=5)

    def test_a_folder_that_is_no_legacy_manager_exits_2(self) -> None:
        """Criterion 27: the folder is a name that the operator typed; none, or not a legacy one, exits 2."""
        for root in (str(self.manager.parent / "No Such Folder"), str(self.dayz), ""):
            with self.subTest(root=root):
                code, stdout, _stderr = self.cli("migrate", "preview", root, "--json")
                self.assertEqual(code, 2)
                self.assertIn(json.loads(stdout)["error"]["code"], ("PATH_INVALID", "USAGE"))
        code, _stdout, stderr = self.cli("migrate", "preview", str(self.dayz))
        self.assertIn("The selected folder does not contain a supported legacy manager.", stderr)


class MigrateApplyTests(MigrateRoot):
    """`migrate apply ROOT`: the review, the window's dialog, the question, then the import in one process."""

    def test_apply_with_yes_in_text(self) -> None:
        """--yes prints the review and the dialog, imports the profile and lists what was imported."""
        code, stdout, stderr = self.checked_names("migrate", "apply", str(self.legacy), "--yes")
        self.assertEqual(code, 0, stderr)
        for text in ("Import", review_wording.MIGRATION_DIALOG,
                     "1 selected item(s) will be converted or indexed.", "Imported", "Legacy profile"):
            self.assertIn(text, stdout)
        self.assertIn("Legacy data imported.", stderr)
        imported = json.loads(self.cli("profile", "list", "--json")[1])["value"]["profiles"]
        self.assertIn("Máin Server", {profile["display_name"] for profile in imported})

    def test_apply_with_yes_in_json(self) -> None:
        """JSON: the import record, the preview with the selected items as the review, the published items."""
        code, stdout, stderr = self.cli("migrate", "apply", str(self.legacy), "--all-items", "--yes", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual(code, 0, stderr)
        self.assertEqual((value["operations"][0]["kind"], value["review"]["selected_items"]),
                         ("IMPORT_LEGACY", [PROFILE_ITEM]))
        self.assertIn("PROFILE", {entry["kind"] for entry in value["result"]["published"]})

    def test_question_refusals_change_nothing(self) -> None:
        """Decline, --json and no terminal without --yes exit 4; nothing is imported."""
        before = self.state()
        stdin = FakeTerminal("n\n")
        code, stdout, stderr = self.cli("migrate", "apply", str(self.legacy), "--item", PROFILE_ITEM, stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 1), stderr)
        self.assertIn(review_wording.MIGRATION_DIALOG, stdout)
        self.assertTrue(stderr.rstrip().endswith("Nothing was changed."))
        code, stdout, _stderr = self.cli("migrate", "apply", str(self.legacy), "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["review"]["selected_items"]),
                         (4, "CONFIRMATION_REQUIRED", [PROFILE_ITEM]))
        stdin = FakeTerminal("y\n", terminal=False)
        code, _stdout, stderr = self.cli("migrate", "apply", str(self.legacy), stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 0))
        self.assertIn("Run the command again with --yes.", stderr)
        self.assertEqual(self.state(), before)

    def test_names_exit_2_and_blocked_items_exit_3(self) -> None:
        """Criterion 27: an unknown item and a folder that is no legacy one exit 2; criterion 32 (QF-71): a
        blocked named item, and a folder whose every item is blocked, exit 3. Nothing changed, no question."""
        before = self.state()
        cases = ((["--item", "profile:nope"], 2, "Unknown item profile:nope. Run migrate preview for the items."),
                 (["--item", SETTINGS_ITEM], 3, f"The item {SETTINGS_ITEM} conflicts with the current"),
                 (["--item", PROFILE_ITEM, "--all-items"], 2, "not allowed with"))
        for arguments, expected, text in cases:
            with self.subTest(arguments=arguments):
                stdin = FakeTerminal("y\n", terminal=False)
                code, _stdout, stderr = self.cli("migrate", "apply", str(self.legacy), *arguments, stdin=stdin)
                self.assertEqual((code, stdin.reads), (expected, 0), stderr)
                self.assertIn(text, stderr)
        code, stdout, _stderr = self.cli("migrate", "apply", str(self.dayz), "--yes", "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (2, "PATH_INVALID"))
        self.assertEqual(self.state(), before)
        # The same folder without its profile: its settings item is blocked, so the data is there (3, not 1)
        self.profile_file.unlink()
        before = self.state()
        for mode in ((), ("--json",)):
            with self.subTest(mode=mode):
                code, stdout, stderr = self.cli("migrate", "apply", str(self.legacy), "--yes", *mode)
                self.assertEqual(code, 3, stderr)
                if mode:
                    self.assertEqual(json.loads(stdout)["error"]["code"], "MIGRATION_CONFLICT")
                else:
                    self.assertIn("Nothing was imported. The legacy data of this folder conflicts", stderr)
        self.assertEqual(self.state(), before)

    def test_state_refusals(self) -> None:
        """D2 exits 3; a recovery block exits 6 before the question; nothing changed."""
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            before = self.state()
            code, stdout, _stderr = self.cli("migrate", "apply", str(self.legacy), "--yes", "--json")
            self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "INSTANCE_ACTIVE"))
            self.assertEqual(self.state(), before)
        finally:
            holder.close(drain_seconds=5)
        self.broken_journal()
        before = self.state()
        stdin = FakeTerminal("y\n", terminal=False)
        code, stdout, _stderr = self.cli("migrate", "apply", str(self.legacy), "--json", stdin=stdin)
        self.assertEqual((code, json.loads(stdout)["error"]["code"], stdin.reads), (6, "RECOVERY_REQUIRED", 0))
        self.assertEqual(self.state(), before)

    def test_a_source_change_after_the_review_imports_nothing(self) -> None:
        """The legacy profile changes while the question waits: the import refuses inside the operation (6.5,
        IMPORT_LEGACY: exit 1) and imports nothing."""
        profiles_before = saved_state(self.manager, self.dayz)

        def change() -> None:
            """Edit the legacy profile as another program would."""
            self.profile_file.write_text(self.profile_file.read_text(encoding="utf-8").replace("2402", "2403"),
                                         encoding="utf-8")

        code, _stdout, stderr = self.cli("migrate", "apply", str(self.legacy), stdin=ChangingTerminal(change))
        self.assertEqual(code, 1, stderr)
        self.assertIn("The legacy folder changed after the preview. Preview it again.", stderr)
        self.assertEqual(saved_state(self.manager, self.dayz), profiles_before)


class ReviewWordingTests(unittest.TestCase):
    """The review copies of the Settings page's legacy import are the window's own literals (11.2)."""

    def test_copies_appear_in_the_frontend(self) -> None:
        """Every migration text of `review_wording.py` is a literal of `frontend/migration.js`."""
        source = (FRONTEND / "migration.js").read_text(encoding="utf-8")
        for text in (review_wording.MIGRATION_SETTINGS_TITLE, review_wording.MIGRATION_SETTINGS_UNCHANGED,
                     review_wording.MIGRATION_BLOCKED_TITLE, review_wording.MIGRATION_BLOCKED_SUMMARY,
                     review_wording.MIGRATION_BACKUP_TITLE, review_wording.MIGRATION_SOURCE_NOTE,
                     review_wording.MIGRATION_DIALOG, review_wording.MIGRATION_NEEDS_REVIEW):
            self.assertIn(text, source)
        self.assertIn(review_wording.MIGRATION_DIALOG_BODY.replace("{count}", "${selected.length}"), source)
        self.assertIn(review_wording.MIGRATION_BACKUP_SUMMARY.replace("{count}", "${preview.backup_inventory.count}")
                      .replace("{size}", "${preview.backup_inventory.size}"), source)

    def test_nothing_importable_and_items(self) -> None:
        """The default selection is every conflict-free item; the table holds the IDs as an input column."""
        preview = {"settings": {"item_id": SETTINGS_ITEM, "selectable": False, "fields": [], "warnings": [],
                                "conflicts": [{"message": "bad_identifier here"}]},
                   "profiles": [], "backup_inventory": {"item_id": "backups:external-index", "selectable": True,
                                                         "count": 2, "size": 10, "warnings": [], "conflicts": []}}
        self.assertEqual(migrate_review.selectable_ids(preview), ["backups:external-index"])
        blocks = migrate_review.review_blocks(preview)
        self.assertEqual(blocks[1].input_columns, frozenset({2}))
        text = "\n".join(str(getattr(block, "parts", "")) for block in blocks)
        self.assertIn(migrate_review.REVIEW_FALLBACK, text)
        self.assertNotIn("bad_identifier", text)


if __name__ == "__main__":
    unittest.main()
