"""Task 2.5, criteria 8, 13, 26 and QF-3: argument checks come before the instance lock; refusals change nothing."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from session_fixtures import PROFILE_ID, populate  # noqa: E402
from dayz_serverman.application.lifecycle_coordinator import LifecycleCoordinator  # noqa: E402
from dayz_serverman.bridge.facade import BridgeFacade  # noqa: E402
from dayz_serverman.cli import runner  # noqa: E402
from dayz_serverman.cli.bridge_client import BridgeClient, CliBridgeError  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.cli.output import CommandResult  # noqa: E402
from dayz_serverman.composition_model import SessionMode  # noqa: E402
from dayz_serverman.domain.lifecycle import LifecycleSnapshot, ServerState  # noqa: E402
from dayz_serverman.session import open_owner_session  # noqa: E402


# Lock files that a holder keeps locked: Q1 counts them as allowed side effects, and they cannot be read
LOCK_FILES = frozenset(("instance.lock", "server-folders.lock"))


def tree_hashes(*roots: Path) -> dict[str, str | None]:
    """Return every path below the roots with the SHA-256 of each file (None for a folder), lock files aside."""
    result: dict[str, str | None] = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.name in LOCK_FILES:
                result[str(path)] = "lock file"
            else:
                result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return result


class RecordingHandler:
    """Stands in for a command of a later phase; records the session it ran in."""

    def __init__(self) -> None:
        """Start without calls."""
        self.contexts: list[runner.CommandContext] = []

    def __call__(self, context: runner.CommandContext) -> CommandResult:
        """Record the context and succeed."""
        self.contexts.append(context)
        return CommandResult(value={"ran": context.spec.name})


@unittest.skipUnless(os.name == "nt", "the instance lock is a Windows lock")
class CheckOrderTests(unittest.TestCase):
    """Run order of R1 rule 5: arguments (2), D2 (3), then the command."""

    @classmethod
    def setUpClass(cls) -> None:
        """Build one populated manager root and DayZ root for the class."""
        cls.temporary = tempfile.TemporaryDirectory(prefix="serverman_cli_order_")
        cls.manager, cls.dayz = populate(Path(cls.temporary.name))
        cls.archive_missing = Path(cls.temporary.name) / "missing.zip"

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove the roots."""
        cls.temporary.cleanup()

    def setUp(self) -> None:
        """Every command of the table gets a recording handler, so later-phase commands reach the lock."""
        self.handler = RecordingHandler()
        patcher = patch.object(runner, "load_handler", return_value=self.handler)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, arguments: list[str]) -> tuple[int, str, str]:
        """Run one command line against the populated manager root."""
        stdout, stderr = io.StringIO(), io.StringIO()
        code = runner.run(arguments, self.manager, stdout=stdout, stderr=stderr, interrupts=Interrupts())
        return code, stdout.getvalue(), stderr.getvalue()

    def test_argument_errors_win_over_another_holder(self) -> None:
        """With the window holding the lock, data-dependent argument errors exit 2; a correct call exits 3."""
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            before = tree_hashes(self.manager, self.dayz)
            # Case: command line, exit code, JSON code (the bridge code of a refused archive stays)
            refusals = {
                "wrong --profile": (["server", "start", "--profile", "Nobody"], 2, "USAGE"),
                "unknown backup ID": (["backup", "restore", "no-such-backup"], 2, "USAGE"),
                "unknown --set key": (["config", "set", "--target", "server", "--set", "maxPlayrs=10"], 2, "USAGE"),
                "missing archive": (["profile", "restore", "--archive", str(self.archive_missing)], 2,
                                    "INVALID_REQUEST"),
                "overwrite without replace": (["profile", "restore", "--archive", str(self.archive_missing),
                                               "--overwrite"], 2, "USAGE"),
                "correct call": (["server", "start"], 3, "INSTANCE_ACTIVE"),
                "correct call by name": (["server", "start", "--profile", "livonia KÖZÖSSÉGI"], 3, "INSTANCE_ACTIVE"),
            }
            for case, (arguments, expected, json_code) in refusals.items():
                for mode in ("text", "json"):
                    line = arguments + (["--json"] if mode == "json" else [])
                    with self.subTest(case=case, mode=mode):
                        code, stdout, stderr = self.run_cli(line)
                        self.assertEqual(code, expected, stderr or stdout)
                        if mode == "json":
                            self.assertEqual(json.loads(stdout)["error"]["code"], json_code)
                        else:
                            self.assertEqual(stdout, "")
            # Criterion 8: nothing changed in either root, and no handler ran
            self.assertEqual(tree_hashes(self.manager, self.dayz), before)
            self.assertEqual(self.handler.contexts, [])
        finally:
            holder.close(drain_seconds=5)

    def test_instance_active_names_the_window(self) -> None:
        """The D2 refusal names the holder in text and gives its details in JSON."""
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            code, _stdout, stderr = self.run_cli(["backup", "recover"])
            self.assertEqual(code, 3)
            self.assertIn("Another DayZ-ServerMan is active for this manager folder: the window. Nothing was "
                          "changed.", stderr)
            code, stdout, _stderr = self.run_cli(["backup", "recover", "--json"])
            details = json.loads(stdout)["error"]["details"]
            self.assertEqual((code, details["holder"], details["pid"]), (3, "window", os.getpid()))
        finally:
            holder.close(drain_seconds=5)

    def test_a_free_folder_runs_the_handler_in_an_owner_session(self) -> None:
        """Without another holder the command gets the profile of the pre-step and an owner session."""
        code, stdout, _stderr = self.run_cli(["server", "start", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), {"cli_version": 1, "command": "server start", "success": True,
                                              "value": {"ran": "server start"}})
        context = self.handler.contexts[0]
        self.assertEqual(context.session.composition.mode, SessionMode.OWNER)
        self.assertEqual(context.profile["profile_id"], PROFILE_ID)

    def test_a_read_runs_in_an_observer_session(self) -> None:
        """A read never takes the lock: it runs while the window holds it (criterion 13, read half)."""
        holder = open_owner_session(self.manager, "window", require_byte_range_lock=False)
        try:
            code, _stdout, _stderr = self.run_cli(["profile", "show"])
        finally:
            holder.close(drain_seconds=5)
        self.assertEqual(code, 0)
        context = self.handler.contexts[0]
        self.assertEqual(context.session.composition.mode, SessionMode.OBSERVER)
        self.assertEqual(context.profile["profile_id"], PROFILE_ID)


class PendingCommandTests(unittest.TestCase):
    """A command of a later phase refuses with exit 2 before any session opens."""

    def test_pending_command_opens_no_session(self) -> None:
        """No observer and no owner session is opened for a PENDING command."""
        real_parse = runner.parse

        def pending_parse(arguments: list[str]):
            """Parse the line, then drop the handler: the state of a command before its phase builds it."""
            parsed = real_parse(arguments)
            return replace(parsed, spec=replace(parsed.spec, handler=None))

        with patch.object(runner.observer_sessions, "open_observer_session") as observer, \
                patch.object(runner.owner_sessions, "open_owner_session") as owner, \
                patch.object(runner, "parse", pending_parse):
            stdout, stderr = io.StringIO(), io.StringIO()
            # Every command is built since phase 7, so the test drops the handler of one, a write with a
            # pre-step (earlier it ran server start, backup create, mods verify, profile delete, settings set)
            code = runner.run(["settings", "set", "--json"], None, stdout=stdout, stderr=stderr,
                              interrupts=Interrupts())
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], "USAGE")
        observer.assert_not_called()
        owner.assert_not_called()


class _RunningOther:
    """Lifecycle whose server runs with another profile."""

    def status(self) -> LifecycleSnapshot:
        """Report a managed server of the profile "other"."""
        return LifecycleSnapshot(ServerState.RUNNING_MANAGED, 700, profile_id="other")


class D11ExitTests(unittest.TestCase):
    """Criterion 26 through the CLI's own call path: the D11 refusal exits 3, a plain invalid request 2."""

    def test_d11_and_plain_invalid_request(self) -> None:
        """`stop_server` for another profile than the running one is refused at dispatch with exit 3."""
        client = BridgeClient(BridgeFacade(LifecycleCoordinator(_RunningOther(), None).handlers()).dispatch)
        parameters = {"profile_id": "main", "expected_profile_revision": 1, "expected_settings_revision": 2,
                      "backup_after_stop": False}
        with self.assertRaises(CliBridgeError) as refused:
            client.call("stop_server", **parameters)
        self.assertEqual(refused.exception.failure().exit_code, 3)
        self.assertIn("another profile", str(refused.exception.failure()))
        with self.assertRaises(CliBridgeError) as invalid:
            client.call("start_server", profile_id="main")
        self.assertEqual((invalid.exception.code, invalid.exception.failure().exit_code), ("INVALID_REQUEST", 2))


if __name__ == "__main__":
    unittest.main()
