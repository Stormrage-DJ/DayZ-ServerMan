"""QF-71, criterion 32: `migrate apply` refuses before the question when it cannot import what it asks for.

Product Owner ruling of 2026-10-08 00:42:54: an item blocked by a conflict with the current
manager data exits 3 (`MIGRATION_CONFLICT`); a named item with nothing to import exits 1
(`NOT_FOUND`); named items are imported all or not at all; `migrate preview` exits 0.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from edit_cli_fixtures import FakeTerminal  # noqa: E402
from migration_test_support import MigrationTestCase  # noqa: E402
from test_cli_migrate import PROFILE_ITEM, SETTINGS_ITEM, MigrateRoot  # noqa: E402
from dayz_serverman.cli import migrate_review  # noqa: E402
from dayz_serverman.cli.output import Table, line_text  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.legacy_arguments import KNOWN_OPTIONS  # noqa: E402
from dayz_serverman.domain.models import SettingsInput  # noqa: E402

SECOND_ITEM = "profile:dayz_server_manager-profiles/Second.json"
BACKUP_ITEM = "backups:external-index"
# The catalogue text of MIGRATION_CONFLICT, which these refusals must not use
CHANGED_TEXT = "changed after the preview"


class BlockedImportTests(MigrateRoot):
    """A second import of the same folder: the imported profile and the settings item are blocked."""

    def setUp(self) -> None:
        """Add a second legacy profile, then import only the first one."""
        super().setUp()
        second = MigrationTestCase.usable_profile(None, name="Second Server")  # type: ignore[arg-type]
        (self.legacy / "dayz_server_manager-profiles" / "Second.json").write_text(json.dumps(second),
                                                                                    encoding="utf-8")
        code, _stdout, stderr = self.cli("migrate", "apply", str(self.legacy), "--item", PROFILE_ITEM, "--yes")
        self.assertEqual(code, 0, stderr)

    def refused(self, arguments: tuple[str, ...], expected: int, json_code: str, text: str) -> dict:
        """Run the case with --yes in text and JSON, and without --yes and a terminal; return the JSON error."""
        before = self.state()
        code, _stdout, stderr = self.checked_names("migrate", "apply", str(self.legacy), *arguments, "--yes")
        self.assertEqual(code, expected, stderr)
        self.assertIn(text, stderr)
        self.assertNotIn(CHANGED_TEXT, stderr)
        stdin = FakeTerminal("y\n", terminal=False)
        code, stdout, _stderr = self.cli("migrate", "apply", str(self.legacy), *arguments, "--json", stdin=stdin)
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], stdin.reads), (expected, json_code, 0))
        self.assertEqual(self.state(), before)
        return error

    def test_every_item_blocked_without_item_exits_3(self) -> None:
        """The QA probe of 07.03 section 7: without the second profile, every item with data is blocked."""
        (self.legacy / "dayz_server_manager-profiles" / "Second.json").unlink()
        error = self.refused((), 3, "MIGRATION_CONFLICT", "Nothing was imported. The legacy data of this folder")
        self.assertEqual(set(error["details"]["blocked_items"]), {SETTINGS_ITEM, PROFILE_ITEM})

    def test_a_named_blocked_profile_exits_3(self) -> None:
        """--item with the imported profile's ID."""
        error = self.refused(("--item", PROFILE_ITEM), 3, "MIGRATION_CONFLICT",
                             f"The item {PROFILE_ITEM} conflicts with the current DayZ-ServerMan data.")
        self.assertEqual(error["details"]["blocked_items"], [PROFILE_ITEM])

    def test_a_blocked_and_an_importable_item_import_nothing(self) -> None:
        """A mix exits 3 and imports nothing, also the importable item; no named item is dropped."""
        error = self.refused(("--item", SECOND_ITEM, "--item", PROFILE_ITEM), 3, "MIGRATION_CONFLICT",
                             f"The item {PROFILE_ITEM} conflicts")
        self.assertEqual(error["details"]["blocked_items"], [PROFILE_ITEM])
        names = {profile["display_name"] for profile in
                 json.loads(self.cli("profile", "list", "--json")[1])["value"]["profiles"]}
        self.assertNotIn("Second Server", names)

    def test_a_named_item_with_nothing_to_import_exits_1(self) -> None:
        """The backup references of a folder without legacy backups: listed, but no data (criterion 27)."""
        self.refused(("--item", BACKUP_ITEM), 1, "NOT_FOUND",
                     f"Nothing was imported. The item {BACKUP_ITEM} has nothing to import now.")

    def test_preview_of_the_blocked_folder_exits_0(self) -> None:
        """The preview is the answer, whatever it holds."""
        for mode in ((), ("--json",)):
            with self.subTest(mode=mode):
                self.assertEqual(self.cli("migrate", "preview", str(self.legacy), *mode)[0], 0)

    def test_a_recovery_block_comes_first(self) -> None:
        """Each refusal of criterion 32 comes after the recovery gate: exit 6, no question."""
        self.broken_journal()
        before = self.state()
        for arguments in ((), ("--item", PROFILE_ITEM), ("--item", BACKUP_ITEM)):
            with self.subTest(arguments=arguments):
                stdin = FakeTerminal("y\n", terminal=False)
                code, stdout, _stderr = self.cli("migrate", "apply", str(self.legacy), *arguments, "--json",
                                                 stdin=stdin)
                self.assertEqual((code, json.loads(stdout)["error"]["code"], stdin.reads),
                                 (6, "RECOVERY_REQUIRED", 0))
        self.assertEqual(self.state(), before)


class NothingImportableTests(MigrateRoot):
    """Rule 1: a folder with no importable and no blocked item exits 1 (NOT_FOUND), before the question."""

    def setUp(self) -> None:
        """Remove the legacy profile and make the current settings equal the legacy installation."""
        super().setUp()
        self.profile_file.unlink()
        composition = build_composition(self.manager)
        try:
            current = composition.settings.load()
            composition.settings.save(SettingsInput(
                dayz_root=str(self.legacy), dayz_executable=str(self.legacy / "DayZServer_x64.exe")),
                current.revision)
        finally:
            composition.shutdown.request_shutdown()
            composition.shutdown.wait_for_close(5)

    def test_nothing_to_import_exits_1(self) -> None:
        """Text and JSON, with --yes and without a terminal; nothing changed."""
        before = self.state()
        code, _stdout, stderr = self.cli("migrate", "apply", str(self.legacy), "--yes")
        self.assertEqual(code, 1, stderr)
        self.assertIn("This legacy folder has nothing that can be imported now.", stderr)
        stdin = FakeTerminal("y\n", terminal=False)
        code, stdout, _stderr = self.cli("migrate", "apply", str(self.legacy), "--json", stdin=stdin)
        self.assertEqual((code, json.loads(stdout)["error"]["code"], stdin.reads), (1, "NOT_FOUND", 0))
        self.assertEqual(self.cli("migrate", "preview", str(self.legacy))[0], 0)
        self.assertEqual(self.state(), before)


class FieldWordsTests(unittest.TestCase):
    """QF-72: a legacy argument field in a host warning or conflict reads as its operator words."""

    def test_every_legacy_field_has_words_and_no_field_name_is_shown(self) -> None:
        """Each field of `KNOWN_OPTIONS` is mapped; "for mods." and "for config_path." are treated alike."""
        self.assertEqual(set(migrate_review.LEGACY_FIELD_LABELS), set(KNOWN_OPTIONS.values()))
        messages = [f"Removed redundant legacy argument for {field}." for field in KNOWN_OPTIONS.values()]
        preview = {"profiles": [{"item_id": PROFILE_ITEM, "selectable": False, "profile": {"display_name": "M"},
                                 "warnings": messages,
                                 "conflicts": [{"message": "Legacy arguments repeat server_mods."}]}]}
        text = "\n".join(line_text(block) for block in migrate_review.review_blocks(preview)
                         if not isinstance(block, Table))
        for words in migrate_review.LEGACY_FIELD_LABELS.values():
            self.assertIn(f"Removed redundant legacy argument for {words}.", text)
        self.assertIn("Needs review: Legacy arguments repeat the server mods.", text)
        self.assertNotIn(migrate_review.WARNING_FALLBACK, text)
        for field in KNOWN_OPTIONS.values():
            self.assertNotRegex(text, rf"(for|repeat) {field}\.")


if __name__ == "__main__":
    unittest.main()
