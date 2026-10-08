"""Task 6.2: `profile create`, `profile edit` and `profile delete` (10.4, 10.6; criteria 5, 6, 8 and 13).

Real compositions over the edit fixture with the fake process table; each command run is its
own session. Create and edit act without a question, as the window's form does; delete
confirms like the "Delete profile?" dialog and refuses by state before the question (rule 5).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from edit_cli_fixtures import PROFILE, PROFILE_ID, EditRoot, FakeTerminal  # noqa: E402
from folder_lock_fixtures import held_reader  # noqa: E402
from lifecycle_cli_fixtures import OTHER_ID, saved_state  # noqa: E402
from dayz_serverman.application import folder_writer_scope  # noqa: E402
from dayz_serverman.session import open_owner_session  # noqa: E402

NEW_ID = "pripyat-test"
CREATE = ["profile", "create", "--set", f"profile_id={NEW_ID}", "--set", "display_name=Pripyat Test",
          "--set", r"mission_root=mpmissions\dayzOffline.enoch", "--set", "game_port=2502"]
EDIT = ["profile", "edit", *PROFILE]
DELETE = ["profile", "delete", *PROFILE]


class ProfileCreateTests(EditRoot):
    """`profile create` provisions a profile with its generated server files."""

    def test_create_in_text_and_json(self) -> None:
        """The new profile is listed with its ID and the window's end sentence; JSON holds the record."""
        code, stdout, stderr = self.cli(*CREATE)
        self.assertEqual(code, 0, stderr)
        self.assertIn("Pripyat Test", stdout)
        self.assertIn(NEW_ID, stdout)
        self.assertIn("Profile created", stdout)
        self.assertIn("Profile created.", stderr)
        self.assertTrue((self.dayz / "serverman" / NEW_ID / "serverDZ.cfg").is_file())
        code, stdout, _stderr = self.cli("profile", "show", "--profile", NEW_ID, "--json")
        self.assertEqual((code, json.loads(stdout)["value"]["game_port"]), (0, 2502))
        code, stdout, stderr = self.cli("profile", "create", "--set", "profile_id=second-test", "--set",
                                        "display_name=Second", "--set", r"mission_root=mpmissions\dayzOffline.enoch",
                                        "--set", "game_port=2602", "--set", 'extra_arguments=["-doLogs"]', "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual(code, 0, stderr)
        self.assertEqual((value["operations"][0]["kind"], value["result"]["profile"]["extra_arguments"]),
                         ("PROVISION_PROFILE", ["-doLogs"]))

    def test_argument_errors_and_a_pin_change_nothing(self) -> None:
        """Missing values, typing errors, unknown keys and file shapes exit 2; a differing pin exits 3."""
        with tempfile.TemporaryDirectory(prefix="serverman_profile_file_") as temporary:
            folder = Path(temporary)
            (folder / "list.json").write_text("[]", encoding="utf-8")
            (folder / "unknown.json").write_text('{"profile_id": "x", "port": 1}', encoding="utf-8")
            before = saved_state(self.manager, self.dayz)
            cases = (
                (["profile", "create", "--set", "profile_id=only-id"],
                 "The new profile needs more values. Add --set display_name=VALUE, --set mission_root=VALUE, "
                 "--set game_port=VALUE."),
                ([*CREATE, "--set", "game_port=port"], None),
                ([*CREATE[:-2], "--set", "game_port=port"], "The value port of game_port must be a whole number."),
                ([*CREATE, "--set", "mods=none"], "The value none of mods must be a JSON array."),
                ([*CREATE, "--set", "runtime_profile=x"], "Unknown key runtime_profile. Run profile show for the keys."),
                (["profile", "create", "--from-file", str(folder / "list.json")], "is not a JSON object."),
                (["profile", "create", "--from-file", str(folder / "unknown.json")], "Unknown key port."),
            )
            for arguments, text in cases:
                with self.subTest(arguments=arguments[2:]):
                    code, _stdout, stderr = self.cli(*arguments)
                    self.assertEqual(code, 2, stderr)
                    if text:
                        self.assertIn(text, stderr)
            code, stdout, _stderr = self.cli(*CREATE, "--expect-settings-revision", "99", "--json")
            self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "REVISION_CONFLICT"))
            self.assertEqual(saved_state(self.manager, self.dayz), before)
            self.assertFalse((self.dayz / "serverman" / NEW_ID).exists())


class ProfileEditTests(EditRoot):
    """`profile edit` saves the profile with typed changes over its current values."""

    def test_edit_in_text_and_json(self) -> None:
        """Typed changes are saved; an empty nullable text field sends null; JSON holds the saved record."""
        code, stdout, stderr = self.cli(*EDIT, "--set", "display_name=Livonia Renamed", "--set", "game_port=2310")
        self.assertEqual(code, 0, stderr)
        self.assertIn("Profile: Livonia Renamed", stdout)
        self.assertIn("Profile saved.", stderr)
        code, stdout, stderr = self.cli(*EDIT, "--set", "mission_root=", "--set",
                                        'extra_arguments=["-doLogs"]', "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual(code, 0, stderr)
        self.assertEqual((value["operations"][0]["kind"], value["result"]["mission_root"],
                          value["result"]["extra_arguments"], value["result"]["game_port"]),
                         ("SAVE_PROFILE", None, ["-doLogs"], 2310))

    def test_from_file_and_refusals(self) -> None:
        """The file is the bridge profile object; the ID cannot change; typing errors exit 2; a pin exits 3."""
        with tempfile.TemporaryDirectory(prefix="serverman_profile_file_") as temporary:
            folder = Path(temporary)
            (folder / "same.json").write_text(json.dumps({"profile_id": PROFILE_ID, "game_port": 2320}),
                                              encoding="utf-8")
            (folder / "other.json").write_text(json.dumps({"profile_id": "another"}), encoding="utf-8")
            before = saved_state(self.manager, self.dayz)
            for arguments, text in (
                    (["--set", "profile_id=renamed"], "A profile edit keeps the profile ID."),
                    (["--from-file", str(folder / "other.json")], "A profile edit keeps the profile ID."),
                    (["--set", "game_port=many"], "The value many of game_port must be a whole number."),
                    (["--set", "mods=x"], "must be a JSON array."),
                    (["--set", "revision=4"], "Unknown key revision. Run profile show for the keys."),
                    ([], "Name at least one change with --set or --from-file.")):
                with self.subTest(arguments=arguments):
                    code, _stdout, stderr = self.cli(*EDIT, *arguments)
                    self.assertEqual(code, 2, stderr)
                    self.assertIn(text, stderr)
            code, stdout, _stderr = self.cli(*EDIT, "--set", "game_port=2330", "--expect-profile-revision", "9",
                                             "--json")
            self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "REVISION_CONFLICT"))
            self.assertEqual(saved_state(self.manager, self.dayz), before)
            code, stdout, stderr = self.cli(*EDIT, "--from-file", str(folder / "same.json"), "--set",
                                            "display_name=From Both", "--json")
            self.assertEqual(code, 0, stderr)
            self.assertEqual((json.loads(stdout)["value"]["result"]["game_port"],
                              json.loads(stdout)["value"]["result"]["display_name"]), (2320, "From Both"))


class ProfileDeleteTests(EditRoot):
    """`profile delete` confirms like the window's dialog; the deletion service holds the writer side (A13)."""

    def test_delete_with_yes(self) -> None:
        """The review is the dialog; the profile and its generated folder go, its backups stay."""
        backups = sorted((self.manager / "backups").rglob("*.zip"))
        code, stdout, stderr = self.cli(*DELETE, "--yes")
        self.assertEqual(code, 0, stderr)
        self.assertIn("Delete profile?", stdout)
        self.assertIn("Permanently delete Livonia Közösségi, including its generated configuration", stdout)
        self.assertIn("Profile deleted.", stderr)
        self.assertFalse((self.dayz / "serverman" / PROFILE_ID).exists())
        self.assertEqual(sorted((self.manager / "backups").rglob("*.zip")), backups)
        code, stdout, _stderr = self.cli("profile", "list", "--json")
        self.assertEqual([item["profile_id"] for item in json.loads(stdout)["value"]["profiles"]], [OTHER_ID])

    def test_decline_json_and_no_terminal_change_nothing(self) -> None:
        """Criteria 5, 6: decline, --json without --yes and no terminal exit 4; nothing changed."""
        before = saved_state(self.manager, self.dayz)
        stdin = FakeTerminal("n\n")
        code, _stdout, stderr = self.cli(*DELETE, stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 1))
        self.assertTrue(stderr.rstrip().endswith("Nothing was changed."))
        code, stdout, _stderr = self.cli(*DELETE, "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["review"]["profile_id"]),
                         (4, "CONFIRMATION_REQUIRED", PROFILE_ID))
        stdin = FakeTerminal("y\n", terminal=False)
        code, _stdout, stderr = self.cli(*DELETE, stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 0))
        self.assertIn("Run the command again with --yes.", stderr)
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_refusals_change_nothing(self) -> None:
        """Running server and recovery block before the question; the deletion's own refusal, a held
        observer read (A13) and a pin after it: exit 3 or 6, nothing changed."""
        self.assertEqual(self.cli("server", "start", *PROFILE, "--yes")[0], 0)
        before = saved_state(self.manager, self.dayz)
        for mode in ((), ("--json",), ("--yes",)):
            with self.subTest(mode=mode):
                stdin = FakeTerminal("y\n", terminal=False)
                code, stdout, stderr = self.cli(*DELETE, *mode, stdin=stdin)
                self.assertEqual((code, stdin.reads), (3, 0), stderr)
                self.assertNotIn("Delete profile?", stdout)
                if mode == ("--json",):
                    self.assertEqual(json.loads(stdout)["error"]["code"], "DELETION_BLOCKED")
                else:
                    self.assertIn("Stop the DayZ server before deleting a profile.", stderr)
        # QF-63: the refusals while the server runs changed nothing
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        self.assertEqual(self.cli("server", "stop", *PROFILE, "--yes")[0], 0)
        before = saved_state(self.manager, self.dayz)
        # The other profile does not own a generated folder: the deletion refuses before its first change
        code, stdout, _stderr = self.cli("profile", "delete", "--profile", OTHER_ID, "--yes", "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "DELETION_BLOCKED"))
        with held_reader(self.manager / "data" / "server-folders.lock"), \
                patch.object(folder_writer_scope, "OWNER_WRITER_WAIT_SECONDS", 0.3):
            code, stdout, stderr = self.cli(*DELETE, "--yes", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"]), (3, "CONTROL_CONFLICT"), stderr)
        self.assertEqual(error["details"]["operation"]["progress_percent"], 0)
        code, stdout, _stderr = self.cli(*DELETE, "--expect-profile-revision", "9", "--yes", "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "REVISION_CONFLICT"))
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        self.broken_journal()
        stdin = FakeTerminal("y\n", terminal=False)
        code, stdout, _stderr = self.cli(*DELETE, "--json", stdin=stdin)
        self.assertEqual((code, json.loads(stdout)["error"]["code"], stdin.reads), (6, "RECOVERY_REQUIRED", 0))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_another_instance_refuses(self) -> None:
        """D2 (criterion 13): with the window holding the lock, create, edit and delete exit 3, nothing changed."""
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            before = saved_state(self.manager, self.dayz)
            for arguments in (CREATE, [*EDIT, "--set", "game_port=2340"], [*DELETE, "--yes"]):
                with self.subTest(command=arguments[:2]):
                    code, stdout, _stderr = self.cli(*arguments, "--json")
                    self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "INSTANCE_ACTIVE"))
            self.assertEqual(saved_state(self.manager, self.dayz), before)
        finally:
            holder.close(drain_seconds=5)


if __name__ == "__main__":
    unittest.main()
