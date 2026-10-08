"""Task 6.1: `config set` (10.4, 10.6; criteria 5, 6, 8, 13 and 19) over real compositions.

The review is the preview; `--yes`, a decline, `--json` and no terminal follow 8.1; the typing
of `--set` comes from the loaded field kinds and `--from-file` is the bridge `updates` object.
"Changed nothing" compares the saved manager data and the DayZ root (criterion 8, Q1).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from edit_cli_fixtures import PROFILE, ChangingTerminal, EditRoot, FakeTerminal  # noqa: E402
from lifecycle_cli_fixtures import saved_state  # noqa: E402
from dayz_serverman.cli.commands import config_write  # noqa: E402
from dayz_serverman.session import open_owner_session  # noqa: E402

SET = ["config", "set", *PROFILE, "--target", "server"]


class ConfigSetTests(EditRoot):
    """`config set --target server` with the preview as the review."""

    def test_set_with_yes_in_text_and_json(self) -> None:
        """--yes prints the review table and writes the typed values; JSON holds the preview and the record."""
        code, stdout, stderr = self.cli(*SET, "--set", "hostname=Edited Name", "--set", "maxPlayers=42", "--yes")
        self.assertEqual(code, 0, stderr)
        for text in ("Profile: Livonia Közösségi", "2 validated change(s).", "Setting", "Key", "Now", "New",
                     "Server name", "hostname", "Fixture", "Edited Name", "Maximum players", "maxPlayers", "42"):
            self.assertIn(text, stdout)
        self.assertIn("Configuration changes applied.", stderr)
        written = self.server_config.read_text(encoding="utf-8")
        self.assertIn('hostname = "Edited Name";', written)
        self.assertIn("maxPlayers = 42;", written)
        code, stdout, stderr = self.cli(*SET, "--set", "disableVoN=on", "--yes", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual(code, 0, stderr)
        self.assertEqual(value["review"]["changed_fields"], ["disableVoN"])
        self.assertEqual((value["operations"][0]["kind"], value["operations"][0]["state"]),
                         ("APPLY_CONFIGURATION", "SUCCEEDED"))
        self.assertIn("disableVoN = 1;", self.server_config.read_text(encoding="utf-8"))

    def test_decline_json_and_no_terminal_change_nothing(self) -> None:
        """Criteria 5, 6: decline, --json without --yes and no terminal exit 4; nothing changed."""
        before = saved_state(self.manager, self.dayz)
        stdin = FakeTerminal("n\n")
        code, stdout, stderr = self.cli(*SET, "--set", "maxPlayers=7", stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 1))
        self.assertIn("maxPlayers", stdout)
        self.assertIn("Continue? [y/N]", stderr)
        self.assertTrue(stderr.rstrip().endswith("Nothing was changed."))
        stdin = FakeTerminal("y\n")
        code, stdout, _stderr = self.cli(*SET, "--set", "maxPlayers=7", "--json", stdin=stdin)
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["review"]["changed_fields"], stdin.reads),
                         (4, "CONFIRMATION_REQUIRED", ["maxPlayers"], 0))
        stdin = FakeTerminal("y\n", terminal=False)
        code, _stdout, stderr = self.cli(*SET, "--set", "maxPlayers=7", stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 0))
        self.assertIn("Run the command again with --yes.", stderr)
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        code, _stdout, stderr = self.cli(*SET, "--set", "maxPlayers=7", stdin=FakeTerminal("yes\n"))
        self.assertEqual(code, 0, stderr)

    def test_stale_review_refuses_with_nothing_changed(self) -> None:
        """The file changes while the question waits: the reviewed apply refuses with exit 3 (REVISION_CONFLICT)."""
        changed = 'hostname = "Changed elsewhere";\ninstanceId = 17;\n'

        def change() -> None:
            """Edit the file as another editor would."""
            self.server_config.write_text(changed, encoding="utf-8")

        code, _stdout, stderr = self.cli(*SET, "--set", "maxPlayers=9", stdin=ChangingTerminal(change))
        self.assertEqual(code, 3, stderr)
        self.assertEqual(self.server_config.read_text(encoding="utf-8"), changed)
        # JSON with --yes: the change lands between the review and the apply
        original_ask = config_write.ask

        def ask_then_change(*arguments: object) -> None:
            """Pass the question, then change the file."""
            original_ask(*arguments)
            self.server_config.write_text(changed.replace("elsewhere", "again"), encoding="utf-8")

        with patch.object(config_write, "ask", ask_then_change):
            code, stdout, stderr = self.cli(*SET, "--set", "maxPlayers=9", "--yes", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"]), (3, "REVISION_CONFLICT"), stderr)
        self.assertIn(error["details"]["operation"]["last_working_phase"], (None, "loaded"))
        self.assertNotIn("maxPlayers", self.server_config.read_text(encoding="utf-8"))

    def test_typing_errors_and_unknown_names_exit_2(self) -> None:
        """A value that does not convert, an unknown key or target and a missing change: exit 2, nothing changed."""
        before = saved_state(self.manager, self.dayz)
        cases = (
            (["--set", "maxPlayers=many"], "The value many of maxPlayers must be a whole number."),
            (["--set", "disableVoN=maybe"], "The value maybe of disableVoN must be on or off"),
            (["--set", "maxPlayrs=1"], "Unknown key maxPlayrs. Run config show --target server for the keys."),
            ([], "Name at least one change with --set or --from-file."),
        )
        for arguments, text in cases:
            for mode in ((), ("--json",)):
                with self.subTest(arguments=arguments, mode=mode):
                    code, stdout, stderr = self.cli(*SET, *arguments, "--yes", *mode)
                    self.assertEqual(code, 2, stderr)
                    if mode:
                        self.assertEqual(json.loads(stdout)["error"]["code"], "USAGE")
                    else:
                        self.assertIn(text, stderr)
        code, _stdout, stderr = self.cli("config", "set", *PROFILE, "--target", "nope", "--set", "a=1")
        self.assertEqual(code, 2, stderr)
        # A value the bridge refuses (below the supported minimum) is an argument error too
        code, stdout, _stderr = self.cli(*SET, "--set", "maxPlayers=0", "--yes", "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (2, "INVALID_REQUEST"))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_from_file_shapes(self) -> None:
        """The file is the bridge updates object; --set replaces its key; other shapes exit 2."""
        with tempfile.TemporaryDirectory(prefix="serverman_edit_file_") as temporary:
            folder = Path(temporary)
            (folder / "updates.json").write_text('{"hostname": "From File", "maxPlayers": 12}', encoding="utf-8")
            (folder / "list.json").write_text('["hostname"]', encoding="utf-8")
            (folder / "unknown.json").write_text('{"maxPlayrs": 1}', encoding="utf-8")
            before = saved_state(self.manager, self.dayz)
            for name in ("list.json", "unknown.json", "missing.json"):
                with self.subTest(name=name):
                    code, _stdout, stderr = self.cli(*SET, "--from-file", str(folder / name), "--yes")
                    self.assertEqual(code, 2, stderr)
            self.assertEqual(saved_state(self.manager, self.dayz), before)
            code, stdout, stderr = self.cli(*SET, "--from-file", str(folder / "updates.json"),
                                            "--set", "hostname=Option Wins", "--yes", "--json")
            self.assertEqual(code, 0, stderr)
            self.assertEqual(sorted(json.loads(stdout)["value"]["review"]["changed_fields"]),
                             ["hostname", "maxPlayers"])
        written = self.server_config.read_text(encoding="utf-8")
        self.assertIn('hostname = "Option Wins";', written)
        self.assertIn("maxPlayers = 12;", written)

    def test_state_refusals_before_the_question(self) -> None:
        """Rule 5 and criterion 27: no gameplay file exits 1, gameplay not enabled exits 3, a recovery block 6."""
        gameplay = ["config", "set", *PROFILE, "--target", "gameplay", "--set", "GeneralData.disableBaseDamage=on",
                    "--json"]
        stdin = FakeTerminal("y\n", terminal=False)
        code, stdout, _stderr = self.cli(*gameplay, stdin=stdin)
        self.assertEqual((code, json.loads(stdout)["error"]["code"], stdin.reads), (1, "NOT_FOUND", 0))
        (self.mission / "cfgGameplay.json").write_text('{"version": 123}', encoding="utf-8")
        code, stdout, _stderr = self.cli(*gameplay, stdin=stdin)
        self.assertEqual((code, json.loads(stdout)["error"]["code"], stdin.reads), (3, "GAMEPLAY_NOT_ENABLED", 0))
        self.broken_journal()
        before = saved_state(self.manager, self.dayz)
        for mode in ((), ("--json",)):
            with self.subTest(mode=mode):
                stdin = FakeTerminal("y\n", terminal=False)
                code, stdout, stderr = self.cli(*SET, "--set", "maxPlayers=8", *mode, stdin=stdin)
                self.assertEqual((code, stdin.reads), (6, 0), stderr)
                self.assertNotIn("Setting", stdout)
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_another_instance_refuses_every_edit(self) -> None:
        """D2 (criterion 13): with the window holding the lock, each command of 6.1 exits 3, nothing changed."""
        self.add_medical_files()
        self.add_legacy_starter()
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            before = saved_state(self.manager, self.dayz)
            for arguments in (SET + ["--set", "maxPlayers=8"],
                              ["tweaks", "set", *PROFILE, "--target", "economy", "--set", "ZombieMaxCount=400"],
                              ["tweaks", "convert-loadout", *PROFILE],
                              ["tweaks", "medical", "set", *PROFILE, "medical_item_spawns", "on"]):
                with self.subTest(command=arguments[:2]):
                    code, stdout, _stderr = self.cli(*arguments, "--yes", "--json")
                    self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "INSTANCE_ACTIVE"))
            self.assertEqual(saved_state(self.manager, self.dayz), before)
        finally:
            holder.close(drain_seconds=5)


if __name__ == "__main__":
    unittest.main()
