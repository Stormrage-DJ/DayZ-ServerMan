"""A fake process table behind real CLI compositions: launcher, inventory, graceful stop and readiness probes.

Every owner or observer session that a CLI run opens builds its own lifecycle services; the
patched factories give them all one process table, so a later session sees the server that an
earlier one started, as two real manager processes would. No DayZ, SteamCMD or network is used.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from typing import Callable
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from session_fixtures import PROFILE_ID, populate, run_lane  # noqa: E402
from profile_fixtures import profile_payload  # noqa: E402
from dayz_serverman import lifecycle_composition  # noqa: E402
from dayz_serverman.application.lifecycle_ports import LaunchReceipt  # noqa: E402
from dayz_serverman.cli import runner  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.lifecycle import InventorySnapshot, LifecycleFailure, ProcessObservation  # noqa: E402
from dayz_serverman.domain.online_players import InformationAnswer  # noqa: E402

# The second profile of the fixture, for the D11 cases
OTHER_ID, OTHER_NAME = "chernarus-main", "Chernarus PvE"
# Saved manager data and server files whose change would break "changed nothing" (criterion 8, Q1)
SAVED_PARTS = ("config", "data/profiles", "data/schedules.json", "data/ui-preferences.json", "data/state.json",
               "data/server-ownership.json", "backups")


class ProcessTable:
    """The processes of the fake machine and the scripted behaviour of launch, stop and readiness."""

    def __init__(self) -> None:
        """Start without a server process."""
        self.snapshot = InventorySnapshot(())
        # Snapshots that the next inventory reads return first, in order
        self.queue: list[InventorySnapshot] = []
        self.next_pid = 700
        self.launches: list[int] = []
        self.stops = 0
        self.launch_failure: LifecycleFailure | None = None
        self.after_stop: Callable[[], None] | None = None
        self.on_launch: Callable[[], None] | None = None
        self.ready = False


class FakeInventory:
    """Inventory port over the table."""

    def __init__(self, table: ProcessTable) -> None:
        """Bind the table."""
        self.table = table

    def candidates(self, expected_executable: str) -> InventorySnapshot:
        """Return a queued snapshot first, else the current one."""
        del expected_executable
        return self.table.queue.pop(0) if self.table.queue else self.table.snapshot


class FakeLauncher:
    """Launcher port: a launch adds one process with a creation time; no handle is kept (as after a CLI exit)."""

    def __init__(self, table: ProcessTable) -> None:
        """Bind the table."""
        self.table = table

    def launch(self, request) -> LaunchReceipt:
        """Start one fake process, or raise the scripted failure."""
        if self.table.launch_failure is not None:
            raise self.table.launch_failure
        if self.table.on_launch is not None:
            self.table.on_launch()
        pid = self.table.next_pid
        self.table.next_pid += 1
        self.table.launches.append(pid)
        self.table.snapshot = InventorySnapshot((ProcessObservation(pid, request.argv[0], pid * 100),))
        return LaunchReceipt(pid, pid * 100, None)

    def retains_handle(self, handle_token: str, pid: int) -> bool:
        """No handle is ever held."""
        return False

    def release_handle(self, handle_token: str) -> None:
        """Nothing to release."""


class FakeStopper:
    """Graceful-stop port: the process ends, then the scripted hook may change the table."""

    def __init__(self, table: ProcessTable) -> None:
        """Bind the table."""
        self.table = table

    def request_stop(self, evidence) -> None:
        """Count the stop and empty the table."""
        del evidence
        self.table.stops += 1
        self.table.snapshot = InventorySnapshot(())
        if self.table.after_stop is not None:
            self.table.after_stop()


class FakeProbe:
    """Steam query probe: answers once the table says ready."""

    def __init__(self, table: ProcessTable) -> None:
        """Bind the table."""
        self.table = table

    def information(self, port: int) -> InformationAnswer | None:
        """Return an answer without a count when ready."""
        return InformationAnswer(None) if self.table.ready else None


class FakeMission:
    """Mission probe: the mission is loaded."""

    def is_ready(self, directory, started_after_ns: int) -> bool:
        """Report a loaded mission."""
        return True


def patched_lifecycle(table: ProcessTable) -> ExitStack:
    """Return the patches that give every composition the fake ports over one table."""
    stack = ExitStack()
    for name, factory in (
        ("WindowsProcessInventory", lambda: FakeInventory(table)),
        ("WindowsProcessLauncher", lambda: FakeLauncher(table)),
        ("WindowsGracefulStop", lambda _inventory: FakeStopper(table)),
        ("SteamQueryProbe", lambda *_args: FakeProbe(table)),
        ("RptMissionReadinessProbe", FakeMission),
    ):
        stack.enter_context(patch.object(lifecycle_composition, name, factory))
    return stack


def add_other_profile(manager: Path) -> None:
    """Save the second profile of the fixture through an owner composition."""
    composition = build_composition(manager)
    try:
        saved = run_lane(composition, "save_profile", {"profile": profile_payload(
            profile_id=OTHER_ID, display_name=OTHER_NAME, game_port=2402,
            server_config=rf"serverman\{PROFILE_ID}\serverDZ.cfg"), "expected_revision": None})
        assert saved.state.value == "SUCCEEDED", saved
    finally:
        composition.shutdown.request_shutdown()
        composition.shutdown.wait_for_close(5)


def run_cli(manager: Path, arguments: list[str], stdin=None, interrupts: Interrupts | None = None
            ) -> tuple[int, str, str]:
    """Run one command line against the manager root and return its exit code, stdout and stderr."""
    stdout, stderr = io.StringIO(), io.StringIO()
    code = runner.run(arguments, manager, stdout=stdout, stderr=stderr, interrupts=interrupts or Interrupts(),
                      stdin=stdin)
    return code, stdout.getvalue(), stderr.getvalue()


def saved_state(manager: Path, dayz: Path) -> dict[str, str | None]:
    """Hash the saved manager data and the DayZ root; operation records, logs and lock files are not in it."""
    result: dict[str, str | None] = {}
    roots = [manager / part for part in SAVED_PARTS] + [dayz]
    for root in roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*")) if root.exists() else []
        for path in paths:
            result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return result


@unittest.skipUnless(os.name == "nt", "the instance lock is a Windows lock")
class LifecycleRoot(unittest.TestCase):
    """A populated root with two profiles and a fake process table behind every composition of the test."""

    def setUp(self) -> None:
        """Populate the roots, add the second profile, and patch the lifecycle ports."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_cli_lifecycle_")
        self.addCleanup(temporary.cleanup)
        self.manager, self.dayz = populate(Path(temporary.name))
        add_other_profile(self.manager)
        self.table = ProcessTable()
        stack = patched_lifecycle(self.table)
        stack.__enter__()
        self.addCleanup(stack.close)

    def cli(self, *arguments: str, stdin=None, interrupts: Interrupts | None = None) -> tuple[int, str, str]:
        """Run one command line."""
        return run_cli(self.manager, list(arguments), stdin=stdin, interrupts=interrupts)

    def started(self) -> None:
        """Start the server in its own session."""
        code, _stdout, stderr = self.cli("server", "start", "--profile", PROFILE_ID, "--yes")
        self.assertEqual(code, 0, stderr)

    def ownership(self) -> dict:
        """Return the stored ownership record."""
        return json.loads((self.manager / "data" / "server-ownership.json").read_text(encoding="utf-8"))
