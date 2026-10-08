"""Task 5.1: `updates check`, `updates auto`, `steam set` and `steam login` (10.3, 10.5, 7, 8.1; criteria 6, 8, 11, 13).

Real compositions over the Steam Web API and SteamCMD fakes of `steam_cli_fixtures.py`; each CLI
run is its own session. No network, no SteamCMD process.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lifecycle_cli_fixtures import saved_state  # noqa: E402
from steam_cli_fixtures import SteamRoot  # noqa: E402
from test_cli_confirm import FakeTerminal  # noqa: E402
from test_cli_input_names import InputNameChecker, flags  # noqa: E402
from dayz_serverman.adapters.windows import steamcmd as steamcmd_adapter  # noqa: E402
from dayz_serverman.adapters.windows.steamcmd import SteamCmdConsole, SteamCmdPaths  # noqa: E402
from dayz_serverman.cli import mods_wording  # noqa: E402
from dayz_serverman.cli.commands import updates_write  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.session import open_owner_session  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"


class UpdatesCheckTests(SteamRoot):
    """`updates check` requests a check, waits for its result and prints the update status (criterion 11)."""

    def test_forced_mod_check_waits_and_prints_the_status(self) -> None:
        """A forced check runs once; a second call without --force starts nothing and says so."""
        code, stdout, stderr = self.cli("updates", "check", "--scope", "mods", "--force")
        self.assertEqual(code, 0, stderr)
        self.assertEqual([ids for ids, _deadline in self.catalog.calls], [("1559212036",)])
        for text in ("Profile: Livonia Közösségi", "Mods: No mod updates available", "Mods last checked:",
                     "Automatic update checks: on."):
            self.assertIn(text, stdout)
        self.assertNotIn("No new check was started", stdout)
        code, stdout, _stderr = self.cli("updates", "check", "--scope", "mods")
        self.assertEqual((code, len(self.catalog.calls)), (0, 1))
        self.assertIn("No new check was started; the status below is from the last check. Use --force to check "
                      "now.", stdout)
        code, stdout, _stderr = self.cli("updates", "check", "--scope", "mods", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual((code, value["operations"], value["review"], value["request"]["accepted"]),
                         (0, [], None, False))
        self.assertEqual((value["result"]["profile_id"], value["result"]["mods"]["check_state"]),
                         ("livonia-main", "OK"))

    def test_server_build_check_runs_through_the_app_information_fake(self) -> None:
        """--scope server-build --force waits for the build check and prints the build on Steam."""
        code, stdout, stderr = self.cli("updates", "check", "--scope", "server-build", "--force")
        self.assertEqual(code, 0, stderr)
        self.assertEqual((self.app_info.calls, self.catalog.calls), (1, []))
        self.assertIn("Build on Steam: 24570360", stdout)
        code, stdout, _stderr = self.cli("updates", "check", "--scope", "server-build", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual((code, value["request"]["accepted"], self.app_info.calls), (0, False, 1))
        self.assertFalse(value["result"]["server_build"]["checking"])

    def test_ctrl_c_during_the_wait_keeps_waiting_for_the_result(self) -> None:
        """The check has no safe point: Ctrl+C says so once, the command waits and exits 0 with the result."""
        release = self.catalog.block()
        self.addCleanup(release.set)
        interrupts = Interrupts()
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            """Press Ctrl+C after the first poll and twice more later; let the check finish at the third sleep."""
            sleeps.append(seconds)
            if len(sleeps) <= 3:
                interrupts.handle(2, None)
            if len(sleeps) == 3:
                release.set()

        with patch.object(updates_write, "SLEEP", sleep):
            code, stdout, stderr = self.cli("updates", "check", "--scope", "mods", "--force", interrupts=interrupts)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr.count(mods_wording.CHECK_NOT_CANCELLABLE), 1)
        self.assertIn("Mods last checked:", stdout)
        self.assertGreaterEqual(len(sleeps), 3)

    def test_a_check_that_does_not_finish_exits_1(self) -> None:
        """After the limit the command exits 1 with the status in the JSON details."""
        release = self.catalog.block()
        self.addCleanup(release.set)
        ticks = iter((0.0, *([10_000.0] * 50)))
        before = set(threading.enumerate())
        with patch.object(updates_write, "CLOCK", lambda: next(ticks)), patch.object(updates_write, "SLEEP",
                                                                                     lambda _seconds: None):
            code, stdout, _stderr = self.cli("updates", "check", "--scope", "mods", "--force", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["message"]), (1, "CHECK_NOT_FINISHED", mods_wording.CHECK_TIMEOUT))
        self.assertTrue(error["details"]["status"]["checking"])
        release.set()
        # The check worker writes its cache after the release; let it finish before the root is removed
        for worker in set(threading.enumerate()) - before:
            worker.join(10)

    def test_another_instance_refuses_with_nothing_changed(self) -> None:
        """D2: a held instance lock exits 3 before any request; reads still run (criterion 13)."""
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            before = saved_state(self.manager, self.dayz)
            for arguments in (("updates", "check", "--force"), ("updates", "auto", "off"),
                              ("steam", "set", "--mode", "anonymous")):
                with self.subTest(arguments=arguments):
                    code, stdout, _stderr = self.cli(*arguments, "--json")
                    self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "INSTANCE_ACTIVE"))
            self.assertEqual((saved_state(self.manager, self.dayz), self.catalog.calls), (before, []))
            self.assertEqual(self.cli("updates", "status")[0], 0)
        finally:
            holder.close(drain_seconds=5)


class UpdatesAutoTests(SteamRoot):
    """`updates auto on/off` saves the switch of Settings."""

    def test_off_and_on_in_text_and_json(self) -> None:
        """The switch is saved and read back; JSON holds the bridge answer."""
        code, stdout, stderr = self.cli("updates", "auto", "off")
        self.assertEqual((code, stdout.strip()), (0, "Automatic update checks: off."), stderr)
        preferences = json.loads((self.manager / "data" / "ui-preferences.json").read_text(encoding="utf-8"))
        self.assertIn('"automatic_update_checks": false', json.dumps(preferences))
        self.assertIn("Automatic update checks: off.", self.cli("updates", "status")[1])
        code, stdout, _stderr = self.cli("updates", "auto", "on", "--json")
        self.assertEqual((code, json.loads(stdout)["value"]["result"]), (0, {"automatic_update_checks": True}))
        code, _stdout, _stderr = self.cli("updates", "auto", "maybe")
        self.assertEqual(code, 2)


class SteamSetTests(SteamRoot):
    """`steam set` saves the sign-in choice as one operation under the settings revision."""

    def test_modes_in_text_and_json(self) -> None:
        """Anonymous, then account sign-in with a name; `steam show` reads them back."""
        code, stdout, stderr = self.cli("steam", "set", "--mode", "anonymous")
        self.assertEqual((code, stdout.strip()), (0, "Steam sign-in: Anonymous"), stderr)
        self.assertIn("Steam sign-in settings saved.", stderr)
        code, stdout, _stderr = self.cli("steam", "set", "--mode", "account", "--account", "server_admin", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual((code, value["operations"][0]["kind"], value["result"]["account_name"]),
                         (0, "SAVE_STEAM_SETTINGS", "server_admin"))
        self.assertIn("Steam account name: server_admin", self.cli("steam", "show")[1])

    def test_argument_and_state_refusals_change_nothing(self) -> None:
        """A missing or forbidden account exits 2; a pinned revision that differs exits 3; nothing is saved."""
        before = saved_state(self.manager, self.dayz)
        cases = (
            (("--mode", "account"), 2, "USAGE", "Name the Steam account with --account."),
            (("--mode", "anonymous", "--account", "x"), 2, "USAGE", "Anonymous sign-in uses no account."),
            (("--mode", "anonymous", "--expect-settings-revision", "99"), 3, "REVISION_CONFLICT",
             "The settings revision is now"),
        )
        for arguments, expected, json_code, text in cases:
            with self.subTest(arguments=arguments):
                code, _stdout, stderr = self.cli("steam", "set", *arguments)
                self.assertEqual(code, expected)
                self.assertIn(text, stderr)
                code, stdout, _stderr = self.cli("steam", "set", *arguments, "--json")
                self.assertEqual((code, json.loads(stdout)["error"]["code"]), (expected, json_code))
        self.assertEqual(saved_state(self.manager, self.dayz), before)


class SteamLoginTests(SteamRoot):
    """`steam login` shares this console with SteamCMD; it needs a real console and text mode (D3, QF-30)."""

    def test_sign_in_in_this_console(self) -> None:
        """With a terminal the shared-console adapter signs in the saved account; the GUI keeps its own window."""
        code, stdout, stderr = self.cli("steam", "login", stdin=FakeTerminal(""))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.steamcmd.sign_ins, ["operator"])
        self.assertEqual(self.steamcmd.consoles[-1], SteamCmdConsole.SHARED_CONSOLE)
        self.assertIn("Steam sign-in: answer the SteamCMD questions below.", stderr)
        self.assertIn("Steam sign-in completed.", stderr)
        self.assertIn("Steam account name: operator", stdout)
        # The window's composition keeps the new console window (GUI unchanged)
        composition = build_composition(self.manager)
        try:
            self.assertEqual(self.steamcmd.consoles[-1], SteamCmdConsole.NEW_CONSOLE)
        finally:
            composition.shutdown.request_shutdown()
            composition.shutdown.wait_for_close(5)

    def test_no_console_or_json_refuses_with_exit_4(self) -> None:
        """No terminal, or --json also on a terminal, exits 4 NOT_INTERACTIVE whatever --yes says; stdin unread."""
        before = saved_state(self.manager, self.dayz)
        for arguments, stdin in ((("--yes",), FakeTerminal("", terminal=False)), (("--json",), FakeTerminal("")),
                                 (("--json", "--yes"), FakeTerminal(""))):
            with self.subTest(arguments=arguments):
                code, stdout, stderr = self.cli("steam", "login", *arguments, stdin=stdin)
                self.assertEqual((code, stdin.reads), (4, 0))
                if "--json" in arguments:
                    self.assertEqual(json.loads(stdout)["error"]["code"], "NOT_INTERACTIVE")
                else:
                    self.assertIn("Steam sign-in needs a terminal. Run it in a terminal without --json.", stderr)
        self.assertEqual((self.steamcmd.sign_ins, saved_state(self.manager, self.dayz)), ([], before))

    def test_without_account_sign_in_refuses_before_the_console_check(self) -> None:
        """Rule 5: anonymous sign-in exits 3 with the way out, also without a console; no SteamCMD run."""
        self.steam_settings("ANONYMOUS", None)
        for arguments in ((), ("--json",)):
            with self.subTest(arguments=arguments):
                code, stdout, stderr = self.cli("steam", "login", *arguments, stdin=FakeTerminal("", terminal=False))
                self.assertEqual(code, 3)
                text = json.loads(stdout)["error"]["message"] if arguments else stderr
                self.assertIn("Choose Steam account sign-in first with steam set --mode account --account NAME.",
                              text)
        self.assertEqual(self.steamcmd.sign_ins, [])


class SharedConsoleTests(unittest.TestCase):
    """Design 10.5: the shared console differs from the window's sign-in only by the creation flags."""

    def test_creation_flags_per_console(self) -> None:
        """Shared: a new process group only; the window: a new console and a new process group."""
        group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        new_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        self.assertEqual(steamcmd_adapter.sign_in_flags(SteamCmdConsole.SHARED_CONSOLE), group)
        self.assertEqual(steamcmd_adapter.sign_in_flags(SteamCmdConsole.NEW_CONSOLE), new_console | group)

    def test_the_adapter_launches_with_the_flags_of_its_console(self) -> None:
        """Same argument list and environment; only the creation flags follow the console choice."""
        launches: list[dict] = []

        class Preflight:
            """Accepts every path."""

            def revalidate(self, _paths) -> None:
                """Accept."""

        def popen(argv, **options):
            """Record the launch and stop before any process exists."""
            launches.append({"argv": argv, **options})
            raise OSError("no process in tests")

        paths = SteamCmdPaths(Path("C:/SteamCMD"), Path("C:/SteamCMD/steamcmd.exe"), Path("C:/SteamCMD/w"))
        for console in (SteamCmdConsole.NEW_CONSOLE, SteamCmdConsole.SHARED_CONSOLE):
            adapter = steamcmd_adapter.WindowsSteamCmdAdapter(Preflight(), console=console)
            with patch.object(steamcmd_adapter.subprocess, "Popen", popen), self.assertRaises(OSError):
                adapter.authenticate_interactive(paths, "operator", lambda: False, lambda _evidence: None,
                                                 lambda: None)
        self.assertEqual(launches[0]["argv"], launches[1]["argv"])
        self.assertEqual(launches[0]["creationflags"], steamcmd_adapter.sign_in_flags(SteamCmdConsole.NEW_CONSOLE))
        self.assertEqual(launches[1]["creationflags"],
                         steamcmd_adapter.sign_in_flags(SteamCmdConsole.SHARED_CONSOLE))
        self.assertEqual({key for key in launches[0]} ^ {key for key in launches[1]}, set())
        self.assertEqual(steamcmd_adapter.WindowsSteamCmdAdapter()._console, SteamCmdConsole.NEW_CONSOLE)


class SentenceTests(unittest.TestCase):
    """Criterion 18 for the sentences of 5.1."""

    def test_sentences_follow_the_input_name_rules(self) -> None:
        """Input names appear only as text to type."""
        checker = InputNameChecker(self, flags(), [])
        for line in (mods_wording.account_needed(), mods_wording.account_not_allowed(),
                     mods_wording.choose_account_sign_in(), mods_wording.check_not_started(False),
                     mods_wording.check_not_started(True), mods_wording.automatic_checks(True)):
            with self.subTest(line=line):
                checker.blocks([line])


if __name__ == "__main__":
    unittest.main()
