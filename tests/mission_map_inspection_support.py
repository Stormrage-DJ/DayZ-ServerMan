"""Clocks, reader ports and a synthetic mission for the Inspect tests of leaf 4.2."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dayz_serverman.adapters.windows.server_folder_lock import READER_BUSY, ServerFolderBusy
from dayz_serverman.application.mission_map_inspection import MissionMapInspection
from dayz_serverman.application.mission_map_inspection_jobs import InspectionJobs
from tests.test_mission_map_editor_load_save import CoordinatorFixture
from tests.test_mission_map_terrain import areaflags_bytes
from tests.test_mission_map_terrain_files import limits_text


# Value flags of the synthetic missions; four bits hold all of them
NAMES = ("Tier1", "Tier2", "Tier3", "Tier4")


class StepClock:
    """A monotonic clock that advances by one step at each reading, so slices end after a few reads."""

    def __init__(self, step: float = 0.2) -> None:
        """Start at zero."""
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        """Advance, then return the time."""
        self.now += self.step
        return self.now


class ManualClock:
    """A clock that moves only when the test moves it."""

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """Return the time."""
        return self.now


class RecordingReader:
    """Reader port that counts holds, knows whether it is held and can refuse as busy."""

    def __init__(self) -> None:
        """Start free and unused."""
        self.holds = 0
        self.held = False
        self.busy = False

    @contextmanager
    def hold(self, bound_seconds: float) -> Iterator[None]:
        """Hold the fake reader side, or raise ServerFolderBusy while `busy` is set."""
        if self.busy:
            raise ServerFolderBusy(READER_BUSY)
        self.holds += 1
        self.held = True
        try:
            yield
        finally:
            self.held = False


def mission_bytes(columns: int = 32, rows: int = 32, bits: int = 4) -> bytes:
    """Return a valid 1,000 m areaflags.map whose cells repeat all value combinations."""
    cells = {(i, j): (i + 3 * j) % (1 << bits) for j in range(rows) for i in range(columns)}
    return areaflags_bytes(columns, rows, 1000, 1000, 16, bits, cells)


def write_mission(mission: Path, area: bytes | None = None, names: tuple[str, ...] = NAMES) -> None:
    """Write areaflags.map and cfglimitsdefinition.xml into a temporary mission folder."""
    (mission / "areaflags.map").write_bytes(mission_bytes() if area is None else area)
    (mission / "cfglimitsdefinition.xml").write_text(limits_text(*names), encoding="utf-8")


class InspectionFixture(CoordinatorFixture):
    """The coordinator fixture with a valid synthetic mission and an Inspect service with injected time."""

    def setUp(self) -> None:
        """Write the mission and build the service with a recording reader and stepping clocks."""
        super().setUp()
        write_mission(self.mission)
        self.reader = RecordingReader()
        self.clock = StepClock()
        self.registry_clock = ManualClock()
        self.jobs = InspectionJobs(clock=self.registry_clock)
        self.inspection = MissionMapInspection(self.service, self.reader, jobs=self.jobs, clock=self.clock)

    def saved(self, objects: list[dict[str, Any]]) -> dict[str, Any]:
        """Save a plan with these objects through the coordinator and return the draft that was saved."""
        plan = self.draft(objects=objects)
        self.save(plan)
        return plan

    def start(self, target: dict[str, Any], plan: object = None, profile_id: str = "main") -> dict[str, Any]:
        """Call the Inspect start handler."""
        return self.inspection.inspect_mission_map({"profile_id": profile_id, "plan": plan, "target": target})

    def proceed(self, job_id: str, profile_id: str = "main") -> dict[str, Any]:
        """Call the continue handler."""
        return self.inspection.continue_mission_map_inspection({"profile_id": profile_id, "job_id": job_id})

    def cancel(self, job_id: str, profile_id: str = "main") -> dict[str, Any]:
        """Call the cancel handler."""
        return self.inspection.cancel_mission_map_inspection({"profile_id": profile_id, "job_id": job_id})

    def finish(self, answer: dict[str, Any], limit: int = 5000) -> tuple[dict[str, Any], list[str]]:
        """Continue a running answer to its end; return the end answer and the phase of each running answer."""
        phases = []
        while answer["status"] == "running" and limit:
            phases.append(answer["job"]["phase"])
            answer = self.proceed(answer["job"]["id"])
            limit -= 1
        return answer, phases
