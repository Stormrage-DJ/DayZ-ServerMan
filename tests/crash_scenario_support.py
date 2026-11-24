"""Harness of the crash scenario tests: the real lifecycle, ownership and operation lane over one session."""

from __future__ import annotations

import time
from pathlib import Path

from tests.crash_restart_support import RecordingLogger
from tests.test_launch_facts import _SessionCase
from tests.test_lifecycle_service import ProfilesStub, SettingsStub
from tests.test_server_ownership import Session

from dayz_serverman.application.crash_restart_flow import CrashRestartFlow  # noqa: E402
from dayz_serverman.application.crash_restart_lane import CrashRestartLane  # noqa: E402
from dayz_serverman.application.lifecycle_coordinator import LifecycleCoordinator  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.domain.lifecycle import InventorySnapshot  # noqa: E402


class Chain:
    """The crash restart chain over one session: real coordinator, lane and flow, and a clock set by hand."""

    def __init__(self, case: "ScenarioCase", session: Session, backups=None) -> None:
        """Build the operation lane, the coordinator, the lane adapter and the flow."""
        self._case = case
        self.operations = OperationManager(OperationStore(Path(case.manager).parent / "operations"))
        case.addCleanup(self.operations.shutdown, 3)
        self.coordinator = LifecycleCoordinator(session.readiness, self.operations, backups)
        self.lane = CrashRestartLane(ProfilesStub(case.profile), SettingsStub(case.settings),
                                     self.coordinator, self.operations)
        self.logger = RecordingLogger()
        self.clock = [0.0]
        self.flow = CrashRestartFlow(session.readiness, session.ownership, self.lane, self.logger,
                                     clock=lambda: self.clock[0])

    def at(self, seconds: float) -> None:
        """Set the clock, then run one poll."""
        self.clock[0] = seconds
        self.flow.tick()

    def settle(self) -> None:
        """Wait, at most 5 s, until the operation lane is drained; fail the test when it is not."""
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.operations.is_drained():
                return
            time.sleep(0.01)
        self._case.fail("the operation lane did not drain")


class ScenarioCase(_SessionCase):
    """Base of the scenario tests: sessions, a chain and the two scripted events of a crash."""

    def chain(self, session: Session, backups=None) -> Chain:
        """Build the chain over a session."""
        return Chain(self, session, backups)

    def ready(self, session: Session) -> None:
        """Let both readiness probes of the session answer."""
        session.probe.ready = True
        session.mission.ready = True

    def crash(self, session: Session) -> None:
        """End the process: the shared process table is empty."""
        del session
        self.inventory.snapshot = InventorySnapshot(())
