"""Task 7.1: `settings check-path` and `settings set` (10.4, 6.4.1; criteria 8, 13, 14, 18 and 27).

Real compositions over the lifecycle fixture; each command run is its own session. The check
is a read and changes nothing; the save sends all three folders, keeps the ones not named, and
explains the restart that a save through the "no DayZ server folder" block asks for (QF-069).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lifecycle_cli_fixtures import LifecycleRoot, saved_state  # noqa: E402
from test_cli_input_names import InputNameChecker, RecordingOutput, flags  # noqa: E402
from dayz_serverman.cli import runner  # noqa: E402
from dayz_serverman.cli import settings_wording  # noqa: E402
from dayz_serverman.cli.read_wording import PATH_STATUS_TEXTS  # noqa: E402
from dayz_serverman.session import open_owner_session  # noqa: E402

CHECK = ["settings", "check-path", "--role"]
SET = ["settings", "set"]
# A block reason of a startup recovery that has no DayZ server folder
NO_FOLDER_BLOCK = "Mutations are blocked by unresolved mod publication."


class SettingsRoot(LifecycleRoot):
    """The lifecycle root with helpers for folders and the stored settings."""

    def folder(self, name: str, *, program: bool = False) -> Path:
        """Create a folder beside the roots; with `program` it holds the DayZ server program."""
        path = self.manager.parent / name
        path.mkdir(parents=True, exist_ok=True)
        if program:
            (path / "DayZServer_x64.exe").write_bytes(b"fixture")
        # The long form of the path, as the settings service stores it (no 8.3 short names)
        return path.resolve()

    def settings(self) -> dict:
        """Return the stored settings fields."""
        document = json.loads((self.manager / "config" / "manager.json").read_text(encoding="utf-8"))
        return document.get("fields", document)

    def checked_names(self, *arguments: str) -> tuple[int, str, str]:
        """Run one command line and check its text against the input-name rules of 11.3 (criterion 18)."""
        RecordingOutput.made = []
        with patch.object(runner, "Output", RecordingOutput):
            result = self.cli(*arguments)
        checker = InputNameChecker(self, flags(), list(arguments))
        for output in RecordingOutput.made:
            checker.blocks(output.written)
        return result


class CheckPathTests(SettingsRoot):
    """`settings check-path --role ROLE PATH`: the Settings page's folder check, read-only."""

    def test_check_in_text_and_json(self) -> None:
        """The role's label, the absolute location, the check and the derived program path; JSON is raw."""
        before = saved_state(self.manager, self.dayz)
        code, stdout, stderr = self.checked_names(*CHECK, "dayz-root", str(self.dayz))
        self.assertEqual(code, 0, stderr)
        dayz = self.dayz.resolve()
        for text in ("Setting: DayZ server folder", f"Location: {dayz}", "Check: ",
                     f"DayZ server program: {dayz / 'DayZServer_x64.exe'}"):
            self.assertIn(text, stdout)
        code, stdout, stderr = self.cli(*CHECK, "steamcmd-root", str(self.dayz), "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual(code, 0, stderr)
        self.assertEqual((value["role"], value["path"]), ("steamcmd_root", str(dayz)))
        self.assertIn("workshop_content_root", value["resolved_paths"])
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_relative_and_missing_folders(self) -> None:
        """A relative path is made absolute; a folder that does not exist is a check result, exit 0."""
        code, stdout, stderr = self.cli(*CHECK, "backup-root", "No Such Folder", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual(code, 0, stderr)
        self.assertEqual((value["role"], value["status"]), ("custom_backup_root", "MISSING"))
        self.assertEqual(value["path"], str(Path("No Such Folder").absolute()))
        code, stdout, _stderr = self.cli(*CHECK, "backup-root", "No Such Folder")
        self.assertIn(f"Check: {PATH_STATUS_TEXTS['MISSING'][0]}.", stdout)

    def test_usage_errors_exit_2(self) -> None:
        """An empty path and an unknown role are argument errors."""
        for arguments in ([*CHECK, "dayz-root", ""], [*CHECK, "dayz_root", str(self.dayz)]):
            with self.subTest(arguments=arguments):
                code, stdout, _stderr = self.cli(*arguments, "--json")
                self.assertEqual((code, json.loads(stdout)["error"]["code"]), (2, "USAGE"))

    def test_runs_while_another_instance_holds_the_lock(self) -> None:
        """Criterion 13 (read half): the check is a read and runs while the window holds the lock."""
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            self.assertEqual(self.cli(*CHECK, "dayz-root", str(self.dayz))[0], 0)
        finally:
            holder.close(drain_seconds=5)


class SettingsSetTests(SettingsRoot):
    """`settings set`: one save of all three folders, the stored values kept where none is named."""

    def test_named_folders_replace_and_others_stay(self) -> None:
        """--steamcmd-root keeps the DayZ server folder; --backup-root then --default-backup-root round-trip."""
        stored = self.settings()
        steam = self.folder("Steam CMD")
        code, stdout, stderr = self.checked_names(*SET, "--steamcmd-root", str(steam))
        self.assertEqual(code, 0, stderr)
        self.assertIn("Application locations saved.", stderr)
        for text in ("SteamCMD folder", str(steam), "Setting", "Location", "Check"):
            self.assertIn(text, stdout)
        saved = self.settings()
        self.assertEqual((saved["steamcmd_root"], saved["dayz_root"], saved["custom_backup_root"]),
                         (str(steam), stored["dayz_root"], stored["custom_backup_root"]))
        backups = self.folder("Own Backups")
        code, stdout, stderr = self.cli(*SET, "--backup-root", str(backups), "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual(code, 0, stderr)
        self.assertEqual((value["operations"][0]["kind"], value["review"], value["result"]["custom_backup_root"],
                          value["result"]["steamcmd_root"]), ("SAVE_SETTINGS", None, str(backups), str(steam)))
        self.assertNotIn("restart_required", value["result"])
        code, stdout, stderr = self.cli(*SET, "--default-backup-root")
        self.assertEqual(code, 0, stderr)
        self.assertIn(settings_wording.BACKUP_PORTABLE, stdout)
        self.assertEqual((self.settings()["custom_backup_root"], self.settings()["steamcmd_root"]), (None, str(steam)))

    def test_refusals_change_nothing(self) -> None:
        """No folder named, no folder at the path, a file, both backup options, a pin: exit 2 or 3, nothing changed."""
        before = saved_state(self.manager, self.dayz)
        program = self.dayz / "Bin" / "DayZ Server_x64.exe"
        cases = (([], 2, "Name at least one folder to change with --dayz-root, --steamcmd-root, --backup-root "
                  "or --default-backup-root."),
                 (["--dayz-root", "No Such Folder"], 2, "The folder No Such Folder cannot be used. The DayZ server "
                  "folder does not exist."),
                 (["--steamcmd-root", str(program)], 2, "This location is not a folder."),
                 (["--backup-root", str(self.dayz), "--default-backup-root"], 2, "not allowed with"),
                 (["--steamcmd-root", str(self.dayz), "--expect-settings-revision", "99"], 3, "settings"))
        for arguments, expected, text in cases:
            with self.subTest(arguments=arguments):
                code, _stdout, stderr = self.cli(*SET, *arguments)
                self.assertEqual(code, expected, stderr)
                self.assertIn(text, stderr)
                code, stdout, _stderr = self.cli(*SET, *arguments, "--json")
                self.assertEqual(code, expected)
                self.assertIn(json.loads(stdout)["error"]["code"], ("USAGE", "REVISION_CONFLICT"))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_another_instance_refuses(self) -> None:
        """D2 (criterion 13): with the window holding the lock the save exits 3, nothing changed."""
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            before = saved_state(self.manager, self.dayz)
            code, stdout, _stderr = self.cli(*SET, "--steamcmd-root", str(self.dayz), "--json")
            self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "INSTANCE_ACTIVE"))
            self.assertEqual(saved_state(self.manager, self.dayz), before)
        finally:
            holder.close(drain_seconds=5)


class RestartRequiredTests(SettingsRoot):
    """QF-069 through the CLI: a save that only sets the DayZ server folder passes the "no folder" block."""

    def setUp(self) -> None:
        """Every owner session of the test starts with a "no DayZ server folder" block."""
        super().setUp()
        real_open = runner.owner_sessions.open_owner_session

        def blocked_session(*arguments, **keywords):
            """Open the session, then block its lane as a startup recovery without a DayZ folder does."""
            session = real_open(*arguments, **keywords)
            session.composition.operations.block_for_missing_dayz_root(NO_FOLDER_BLOCK)
            return session

        patcher = patch.object(runner.owner_sessions, "open_owner_session", blocked_session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_repair_save_explains_the_restart(self) -> None:
        """The answer asks for the restart; text says what it means for commands; JSON keeps the flag."""
        server = self.folder("DayZ Repaired", program=True)
        code, stdout, stderr = self.cli(*SET, "--dayz-root", str(server), "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual(code, 0, stderr)
        self.assertIs(value["result"]["restart_required"], True)
        self.assertEqual(self.settings()["dayz_root"], str(server))
        code, stdout, stderr = self.checked_names(*SET, "--dayz-root", str(server))
        self.assertEqual(code, 0, stderr)
        self.assertIn(settings_wording.SETTINGS_RESTART, stdout)
        self.assertIn(settings_wording.RESTART_FOR_COMMANDS, stdout)

    def test_another_save_is_refused_by_the_block(self) -> None:
        """A save that changes another folder stays blocked: exit 6 at dispatch, nothing changed."""
        before = saved_state(self.manager, self.dayz)
        code, stdout, stderr = self.cli(*SET, "--steamcmd-root", str(self.folder("Steam CMD")), "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (6, "MUTATION_CONFLICT"), stderr)
        self.assertEqual(saved_state(self.manager, self.dayz), before)


class SettingsWordingTests(unittest.TestCase):
    """The restart notice of `settings set` is the window's own literal (11.2)."""

    def test_restart_notice_is_the_window_literal(self) -> None:
        """`SETTINGS_RESTART` appears unchanged in `frontend/settings.js`."""
        source = (Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend" / "settings.js")
        self.assertIn(f'"{settings_wording.SETTINGS_RESTART}"', source.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
