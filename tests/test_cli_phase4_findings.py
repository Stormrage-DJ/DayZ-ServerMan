"""QA findings QF-42 to QF-45 of phase 4: CLI way-out sentences and help texts, with nothing changed.

The shared GUI catalogue is not touched: each sentence is CLI-only (`cli/wording.py`), and the
host's own text stays where it already names the cause.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lifecycle_cli_fixtures import saved_state  # noqa: E402
from test_cli_backup_commands import BackupRoot  # noqa: E402
from test_cli_input_names import InputNameChecker, flags  # noqa: E402
from dayz_serverman.cli import help_wording, wording  # noqa: E402
from dayz_serverman.cli.command_table import COMMANDS  # noqa: E402
from dayz_serverman.cli.parser import leaf_help  # noqa: E402
from dayz_serverman.cli.registry import Confirm  # noqa: E402


class FindingTests(BackupRoot):
    """The four findings on the populated root; every case exits 2 or 3 and changes nothing."""

    def test_qf42_occupied_original_world_names_the_cli_choices(self) -> None:
        """`--storage preserve` with an occupied original mission: exit 2 with the CLI way out, no window words."""
        archive = next((self.manager / "backups").rglob("*.zip"))
        before = saved_state(self.manager, self.dayz)
        code, _stdout, stderr = self.cli("profile", "restore", "--archive", str(archive), "--storage", "preserve")
        self.assertEqual(code, 2, stderr)
        self.assertIn("The original mission folder is in use by another profile. Use --storage new, or "
                      "--storage replace with --overwrite.", stderr)
        self.assertNotIn("select isolation", stderr)
        code, stdout, _stderr = self.cli("profile", "restore", "--archive", str(archive), "--storage", "preserve",
                                         "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (2, "INVALID_REQUEST"))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_qf43_missing_archive_says_no_file(self) -> None:
        """A path with no file says so; a file that exists but is no backup keeps the host's text."""
        missing = self.dayz / "missing.zip"
        code, _stdout, stderr = self.cli("profile", "restore", "--archive", str(missing))
        self.assertEqual(code, 2)
        self.assertIn(f"No file at {missing}.", stderr)
        code, stdout, _stderr = self.cli("profile", "restore", "--archive", str(missing), "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (2, "INVALID_REQUEST"))
        not_a_zip = self.dayz / "notes.txt"
        not_a_zip.write_text("text", encoding="utf-8")
        code, _stdout, stderr = self.cli("profile", "restore", "--archive", str(not_a_zip))
        self.assertEqual(code, 2)
        self.assertIn("Select a regular backup ZIP without links.", stderr)

    def test_qf45_pinned_revision_names_the_revision_and_the_read(self) -> None:
        """A pin that differs names the stored revision and the read to repeat, in text and JSON; exit 3."""
        before = saved_state(self.manager, self.dayz)
        code, _stdout, stderr = self.cli("backup", "create", "--profile", "livonia-main", "--yes",
                                         "--expect-profile-revision", "7")
        self.assertEqual(code, 3)
        self.assertIn("The profile revision is now 0, not 7. Read it again with profile show --json, then pin the "
                      "new number.", stderr)
        self.assertNotIn("Run the command again", stderr)
        code, stdout, _stderr = self.cli("backup", "create", "--profile", "livonia-main", "--yes",
                                         "--expect-settings-revision", "99", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["revision"], error["details"]["expected"]),
                         (3, "REVISION_CONFLICT", "settings", 99))
        self.assertIn("settings show --json", error["message"])
        self.assertEqual(saved_state(self.manager, self.dayz), before)


class HelpTests(unittest.TestCase):
    """QF-44: the `--storage` help says "world", and `--yes` is listed only where a question exists."""

    def test_storage_help_and_yes_only_where_asked(self) -> None:
        """Every command that never asks hides `--yes`; every confirming command lists it."""
        self.assertIn("replace an existing world", help_wording.OPTION_TEXTS["storage"])
        self.assertNotIn("profile", help_wording.OPTION_TEXTS["storage"])
        for command in COMMANDS:
            with self.subTest(command=command.name):
                page = leaf_help(command)
                self.assertEqual("--yes" in page, command.confirm is not Confirm.NONE)
                self.assertIn("--json", page)
        self.assertNotIn("--yes", leaf_help(next(item for item in COMMANDS if item.name == "backup recover")))

    def test_sentences_follow_the_input_name_rules(self) -> None:
        """The new sentences hold input names only as text to type or as an echo."""
        checker = InputNameChecker(self, flags(), ["x.zip"])
        for line in (wording.mission_occupied(), wording.no_archive_file("x.zip"),
                     wording.revision_pinned("profile", 3, 7), wording.revision_pinned("settings", 3, 7)):
            with self.subTest(line=line):
                checker.blocks([line])


if __name__ == "__main__":
    unittest.main()
