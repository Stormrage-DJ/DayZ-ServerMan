"""Task 2.6: every read command of 10.1 against a temporary composition, in text and JSON (criteria 2, 7, 13, 16)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from session_fixtures import PROFILE_ID, populate  # noqa: E402
from dayz_serverman import session_observer  # noqa: E402
from dayz_serverman.adapters.steam_player_query import SteamPlayerQuery  # noqa: E402
from dayz_serverman.adapters.windows.server_folder_lock import FolderLockFile, FolderWriter  # noqa: E402
from dayz_serverman.application.schedules import ScheduleCoordinator  # noqa: E402
from dayz_serverman.application.server_readiness import ReadinessLifecycleService  # noqa: E402
from dayz_serverman.application.update_check_scheduler import UpdateCheckScheduler  # noqa: E402
from dayz_serverman.bridge.facade import BridgeFacade  # noqa: E402
from dayz_serverman.cli import main as cli_main  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.cli.parser import parse  # noqa: E402
from dayz_serverman.cli.runner import run  # noqa: E402
from dayz_serverman.domain.online_players import OnlinePlayer  # noqa: E402
from dayz_serverman.session import FOLDER_LOCK_FILE  # noqa: E402

PYTHON_ROOT = Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"
# Every read command of phase 2 with the exit code on the fixture (the gameplay file does not exist there);
# a data file missing behind valid names exits 1 since criterion 27 (QF-21 ruling of 2026-10-07 14:14:23)
READS: dict[str, tuple[list[str], int]] = {
    "status": (["status"], 0), "server status": (["server", "status"], 0),
    "server players": (["server", "players"], 0), "schedule show": (["schedule", "show"], 0),
    "backup list": (["backup", "list"], 0), "backup list --all": (["backup", "list", "--all"], 0),
    "updates status": (["updates", "status"], 0), "mods list": (["mods", "list"], 0),
    "steam show": (["steam", "show"], 0), "profile list": (["profile", "list"], 0),
    "profile show": (["profile", "show"], 0), "profile command": (["profile", "command"], 0),
    "profile missions": (["profile", "missions"], 0),
    "config show": (["config", "show", "--target", "server"], 0),
    "config show gameplay": (["config", "show", "--target", "gameplay"], 1),
    "tweaks show": (["tweaks", "show", "--target", "economy"], 0),
    "tweaks medical show": (["tweaks", "medical", "show"], 1),
    "logs": (["logs", "--lines", "5"], 0), "logs diagnostics": (["logs", "--source", "diagnostics"], 0),
    "settings show": (["settings", "show"], 0),
}
# A player name that must never reach a file under the manager root (criterion 16)
PLAYER_NAME = "Zed Fixtureman"
# A child process that holds the instance lock of a manager root, as a window would
HOLDER = (
    "import sys\nfrom pathlib import Path\nfrom dayz_serverman.session import resolve_paths, INSTANCE_LOCK_FILE\n"
    "from dayz_serverman.adapters.windows.instance_lock import InstanceLock, root_mutex_name\n"
    "paths = resolve_paths(Path(sys.argv[1]))\n"
    "lock = InstanceLock(paths.data / INSTANCE_LOCK_FILE, root_mutex_name(paths.root)).acquire(True)\n"
    "print('held', flush=True)\nsys.stdin.read()\n"
)


class ReadCommandTests(unittest.TestCase):
    """Every R row of 10.1 on a populated manager root and DayZ root."""

    @classmethod
    def setUpClass(cls) -> None:
        """Build the roots once."""
        cls.temporary = tempfile.TemporaryDirectory(prefix="serverman_cli_reads_")
        cls.manager, cls.dayz = populate(Path(cls.temporary.name))

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove the roots."""
        cls.temporary.cleanup()

    def cli(self, arguments: list[str]) -> tuple[int, str, str]:
        """Run one command line against the fixture and return its code and streams."""
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run(arguments, self.manager, stdout=stdout, stderr=stderr, interrupts=Interrupts())
        return code, stdout.getvalue(), stderr.getvalue()

    def test_every_read_in_text_and_json(self) -> None:
        """Text goes to stdout with the profile first where there is one; JSON is one document per run."""
        for name, (arguments, expected) in READS.items():
            with self.subTest(command=name):
                code, stdout, stderr = self.cli(arguments)
                self.assertEqual(code, expected, stderr)
                self.assertEqual(stdout == "", expected != 0)
                code, stdout, _stderr = self.cli([*arguments, "--json"])
                document = json.loads(stdout)
                self.assertEqual((code, document["cli_version"], document["success"]), (expected, 1, expected == 0))
                self.assertEqual(document["command"], parse(arguments).spec.name)

    def test_values_follow_6_3_and_6_6(self) -> None:
        """One-call reads give the raw value with the profile used; `status` has one member per call."""
        _code, stdout, _stderr = self.cli(["status", "--json"])
        value = json.loads(stdout)["value"]
        self.assertEqual(set(value), {"snapshot", "server", "updates", "pending_recoveries", "profile_id"})
        self.assertEqual((value["profile_id"], value["pending_recoveries"]), (PROFILE_ID, []))
        _code, stdout, _stderr = self.cli(["profile", "show", "--json"])
        self.assertEqual(json.loads(stdout)["value"]["display_name"], "Livonia Közösségi")
        _code, stdout, _stderr = self.cli(["mods", "list", "--json"])
        self.assertEqual(json.loads(stdout)["value"]["profile_id"], PROFILE_ID)
        _code, stdout, _stderr = self.cli(["profile", "command", "--json"])
        self.assertEqual(json.loads(stdout)["value"]["profile_id"], PROFILE_ID)

    def test_text_shows_labels_and_keys_in_their_columns(self) -> None:
        """The profile line comes first; a key or ID stands in its own column after the label."""
        _code, stdout, _stderr = self.cli(["config", "show", "--target", "server"])
        self.assertTrue(stdout.startswith("Profile: Livonia Közösségi\n"))
        self.assertRegex(stdout, r"Setting +Value +Key\n")
        self.assertRegex(stdout, r"Server name +Fixture +hostname\n")
        _code, stdout, _stderr = self.cli(["profile", "list"])
        self.assertRegex(stdout, r"Profile +Selected +Backup after stop +ID\nLivonia Közösségi +Yes +No +livonia-main")

    def test_profile_rule_of_reads(self) -> None:
        """`--profile` by name works; an unknown name exits 2 and changes nothing."""
        code, stdout, _stderr = self.cli(["profile", "show", "--profile", "LIVONIA közösségi", "--json"])
        self.assertEqual((code, json.loads(stdout)["value"]["profile_id"]), (0, PROFILE_ID))
        code, _stdout, stderr = self.cli(["mods", "list", "--profile", "Chernarus"])
        self.assertEqual((code, stderr), (2, "No profile is named Chernarus.\n"))

    def test_reader_timeout_exits_3(self) -> None:
        """With an owner's writer side held, a read under the reader side refuses with exit 3 (3.4)."""
        lock_file = FolderLockFile.create(self.manager / "data" / FOLDER_LOCK_FILE)
        writer, entered, leave = FolderWriter(lock_file), threading.Event(), threading.Event()

        def hold() -> None:
            """Hold the writer side until the test ends."""
            with writer.exclusive(5):
                entered.set()
                leave.wait(10)
        holder = threading.Thread(target=hold)
        holder.start()
        try:
            self.assertTrue(entered.wait(5))
            with patch.object(session_observer, "READER_WAIT_SECONDS", 0.3):
                code, stdout, _stderr = self.cli(["server", "status", "--json"])
                self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "CONTROL_CONFLICT"))
                # An exempt read runs while the writer side is held
                self.assertEqual(self.cli(["profile", "list"])[0], 0)
        finally:
            leave.set()
            holder.join(10)
            lock_file.close()
            (self.manager / "data" / FOLDER_LOCK_FILE).unlink()

    def test_an_error_answer_is_not_retried(self) -> None:
        """RECOVERY_REQUIRED exits 6 and STORAGE_FAILURE exits 1, each after exactly one dispatch."""
        original = BridgeFacade.dispatch
        for code, expected in (("RECOVERY_REQUIRED", 6), ("STORAGE_FAILURE", 1)):
            calls: list[str] = []

            def dispatch(facade, request, code=code, calls=calls):
                """Answer the server status with the error; dispatch every other call."""
                if request["method"] == "get_server_status":
                    calls.append(request["method"])
                    return {"success": False, "error": {"code": code, "message": "x", "retryable": True}}
                return original(facade, request)
            with self.subTest(code=code), patch.object(BridgeFacade, "dispatch", dispatch):
                self.assertEqual(self.cli(["server", "status"])[0], expected)
                self.assertEqual(calls, ["get_server_status"])


@unittest.skipUnless(os.name == "nt", "the instance lock is a Windows lock")
class ReadsWithTheLockHeldTests(unittest.TestCase):
    """Criterion 13, read half: a child process holds the instance lock while every read runs."""

    def test_reads_run_while_a_window_holds_the_lock(self) -> None:
        """Every read gives its fixture result although another process holds the lock."""
        with tempfile.TemporaryDirectory(prefix="serverman_cli_held_") as temporary:
            manager, _dayz = populate(Path(temporary))
            environment = {**os.environ, "PYTHONPATH": str(PYTHON_ROOT)}
            child = subprocess.Popen([sys.executable, "-c", HOLDER, str(manager)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, env=environment)
            try:
                self.assertEqual(child.stdout.readline().strip(), "held")
                for name, (arguments, expected) in READS.items():
                    with self.subTest(command=name):
                        stdout, stderr = io.StringIO(), io.StringIO()
                        code = run(arguments, manager, stdout=stdout, stderr=stderr, interrupts=Interrupts())
                        self.assertEqual(code, expected, stderr.getvalue())
                # A write refuses with exit 3 at the same time (D2)
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch("dayz_serverman.cli.runner.load_handler", return_value=lambda context: None):
                    self.assertEqual(run(["backup", "recover"], manager, stdout=stdout, stderr=stderr,
                                         interrupts=Interrupts()), 3)
            finally:
                child.stdin.close()
                child.wait(10)
                child.stdout.close()


class ProcessHygieneTests(unittest.TestCase):
    """Criteria 2 and 16: no scheduler, no thread left, and no player name written anywhere."""

    def test_no_scheduler_and_no_thread_left(self) -> None:
        """After `cli.main` returns only daemon threads remain besides the main thread; no scheduler started."""
        with tempfile.TemporaryDirectory(prefix="serverman_cli_threads_") as temporary:
            manager, _dayz = populate(Path(temporary))
            before = set(threading.enumerate())
            with patch.object(ScheduleCoordinator, "start") as schedules, \
                    patch.object(UpdateCheckScheduler, "start") as checks, \
                    patch.object(sys, "stdout", io.StringIO()), patch.object(sys, "stderr", io.StringIO()):
                for arguments, _expected in READS.values():
                    cli_main(arguments, manager)
            left = [thread for thread in threading.enumerate()
                    if thread not in before and thread.is_alive() and not thread.daemon]
            self.assertEqual(left, [])
            schedules.assert_not_called()
            checks.assert_not_called()

    def test_player_names_are_never_written(self) -> None:
        """`server players --names` and `status` with a known name leave no file under the root holding it."""
        with tempfile.TemporaryDirectory(prefix="serverman_cli_names_") as temporary:
            manager, _dayz = populate(Path(temporary))
            answer = (OnlinePlayer(0, PLAYER_NAME, 0, 125.0), OnlinePlayer(1, "", 0, 30.0))
            with patch.object(ReadinessLifecycleService, "running_query_port", return_value=27016), \
                    patch.object(SteamPlayerQuery, "read_players", return_value=answer):
                outputs = []
                for arguments in (["server", "players", "--names"], ["server", "players", "--names", "--json"],
                                  ["server", "players"], ["server", "players", "--json"], ["status"],
                                  ["status", "--json"]):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    self.assertEqual(run(arguments, manager, stdout=stdout, stderr=stderr,
                                         interrupts=Interrupts()), 0)
                    outputs.append((arguments, stdout.getvalue()))
            # The name is shown only where P3 allows it
            self.assertIn(PLAYER_NAME, outputs[0][1])
            self.assertIn(PLAYER_NAME, json.loads(outputs[1][1])["value"]["players"][0]["name"])
            self.assertNotIn(PLAYER_NAME, outputs[2][1])
            self.assertNotIn("name", json.dumps(json.loads(outputs[3][1])["value"]["players"]))
            self.assertIn("Player 1", outputs[2][1])
            for path in Path(temporary).rglob("*"):
                if path.is_file():
                    with self.subTest(path=path.name):
                        content = path.read_bytes()
                        self.assertNotIn(PLAYER_NAME.encode("utf-8"), content)
                        self.assertNotIn(PLAYER_NAME.encode("utf-16-le"), content)


if __name__ == "__main__":
    unittest.main()
