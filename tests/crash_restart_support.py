"""Scripted parts for the crash restart tests: logger, lifecycle, lane and a rig with a fake clock."""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.application.crash_restart_flow import CrashRestartFlow  # noqa: E402
from dayz_serverman.application.lifecycle_ownership import LaunchOwnership  # noqa: E402
from dayz_serverman.application.operations.models import OperationState  # noqa: E402
from dayz_serverman.domain.lifecycle import LaunchEvidence, LifecycleSnapshot, ServerState  # noqa: E402

WALL_TEXT = "2026-11-23T12:00:00.000+00:00"


class RecordingLogger:
    """Logger that keeps the event name and the other arguments of every record."""

    def __init__(self) -> None:
        """Start with no records."""
        self.records: list[tuple[str, dict]] = []

    def emit(self, event: str, **kwargs: object) -> None:
        """Keep the event and its arguments."""
        self.records.append((event, kwargs))

    def events(self) -> list[str]:
        """Return the event names in order."""
        return [event for event, _kwargs in self.records]


class ScriptedLifecycle:
    """Lifecycle port with a scripted state and profile; it keeps the released handle tokens."""

    def __init__(self) -> None:
        """Start stopped without a profile."""
        self.state = ServerState.STOPPED
        self.profile_id: str | None = None
        self.released: list[str | None] = []
        self.failure: Exception | None = None

    def status(self) -> LifecycleSnapshot:
        """Return the scripted status, or raise the scripted failure."""
        if self.failure is not None:
            raise self.failure
        return LifecycleSnapshot(self.state, 700, profile_id=self.profile_id)

    def release_handle(self, handle_token: str | None) -> None:
        """Keep the token."""
        self.released.append(handle_token)


class ScriptedLane:
    """Lane port with scripted ids and states; it keeps the submits and the last still-wanted check."""

    def __init__(self) -> None:
        """Start with no operations."""
        self.ids: set[str] = set()
        self.submits: list[tuple[str]] = []
        self.states: dict[str, OperationState] = {}
        self.fail_submit: Exception | None = None
        self.last_check: Callable[[], bool] | None = None

    def lifecycle_operation_ids(self) -> frozenset[str]:
        """Return the scripted ids."""
        return frozenset(self.ids)

    def submit(self, profile_id: str, still_wanted: Callable[[], bool]) -> str:
        """Keep the call and the check, then return a fixed id or fail."""
        if self.fail_submit is not None:
            raise self.fail_submit
        self.submits.append((profile_id,))
        self.last_check = still_wanted
        return "op-1"

    def state(self, operation_id: str) -> OperationState:
        """Return the scripted state; an unknown operation is still running."""
        return self.states.get(operation_id, OperationState.RUNNING)


class Rig:
    """The flow over scripted parts, a real ownership holder and a clock that the test sets."""

    def __init__(self) -> None:
        """Build the parts; time starts at 0."""
        self.logger = RecordingLogger()
        self.lifecycle = ScriptedLifecycle()
        self.lane = ScriptedLane()
        self.ownership = LaunchOwnership()
        self.clock = [0.0]
        self.flow = CrashRestartFlow(
            self.lifecycle, self.ownership, self.lane, self.logger,
            clock=lambda: self.clock[0], wall_clock=lambda: datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC),
        )

    def running(self, token: str = "tok-a", handle: str | None = "h-a",
                profile: str | None = "livonia-main", ready: bool = True) -> None:
        """Script a running launch with its evidence, profile and READY latch."""
        self.ownership.set(LaunchEvidence(700, "c:\\dayz\\dayzserver_x64.exe", 70_000, token, handle))
        self.lifecycle.state = ServerState.RUNNING_MANAGED
        self.lifecycle.profile_id = profile
        if ready:
            self.ownership.mark_ready(token)

    def ended(self) -> None:
        """Script a status that shows the process gone; the evidence stays."""
        self.lifecycle.state = ServerState.STOPPED
        self.lifecycle.profile_id = None

    def at(self, seconds: float) -> None:
        """Set the clock, then run one poll."""
        self.clock[0] = seconds
        self.flow.tick()
