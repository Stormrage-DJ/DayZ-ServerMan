"""The private work of one Inspect job: capture pass, checks, point answer or sampling, then the final proof (A181).

Only mission-read slices hold the reader side. Sampling works on the in-memory snapshot.
An object answer adds the building capacity of the 6.9 ruling from the working
baseline, or from the mapgroup files of the capture pass when there is none.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from ..adapters.windows.server_folder_lock import ServerFolderBusy
from ..domain.mission_map_evaluation import evaluate_location
from ..repositories.mission_map_evaluation import snapshot_from_pass
from ..repositories.mission_map_object_capacity import POINT_TARGET, object_capacity
from ..repositories.mission_map_read_pass import MissionReadPass
from .mission_map_evaluation import ObjectTierRead
from .mission_map_reader_port import ReaderPort


# Target time of one call. A reader slice stops reading at it, or after the minimum read time when that ends
# later. Sampling stops at it. The checks after a capture and the building capacity step have no deadline.
# The capacity step runs in a call of its own, without the reader side (6.9 QA F1).
CALL_TARGET_SECONDS = 0.5
# Longest reader wait of one slice; a closed reader side returns running with unchanged progress
SLICE_READER_WAIT_SECONDS = 0.25
# Read time that a slice keeps after a late reader acquisition and the context recheck, so every slice reads
MINIMUM_READ_SECONDS = 0.1

# Running phases that the window and the CLI show
READING = "reading"
SAMPLING = "sampling"
# Private stages of a job
_CAPTURE, _SAMPLE, _CAPACITY, _PROOF = "capture", "sample", "capacity", "proof"
# The private stale answer; the service adds the job identity
STALE = {"status": "stale"}


@dataclass(frozen=True)
class PlanSource:
    """Which plan the job evaluates: the saved revision or the draft fingerprint."""

    kind: str
    revision: int | None
    fingerprint: str | None

    def object_source(self) -> dict[str, Any]:
        """Return the plan_source field of an object result."""
        return {"saved": self.revision} if self.kind == "saved" else {"draft": self.fingerprint}


@dataclass(frozen=True)
class InspectTarget:
    """A native point, or the ID of one plan object."""

    point: tuple[float, float] | None = None
    object_id: str | None = None


def reading() -> dict[str, Any]:
    """Return the running answer of a mission-read step; row fields are null while reading."""
    return {"status": "running", "phase": READING, "rows_done": None, "rows_total": None}


class InspectionRun:
    """Advance one job by one call; the job registry serializes the calls."""

    def __init__(self, mission: Path, plan: dict[str, Any], source: PlanSource, target: InspectTarget,
                 reader: ReaderPort, recheck: Callable[[], bool], *,
                 clock: Callable[[], float] = monotonic, base: Mapping[str, bytes | None] | None = None) -> None:
        """Keep the validated plan and start the capture pass; `recheck` compares the trusted context.

        base holds the mapgroup files of the working baseline. Without it, an
        object pass also reads the mapgroup files of the mission (6.9 ruling).
        """
        self._mission = mission
        self._plan = plan
        self._source = source
        self._target = target
        self._reader = reader
        self._recheck = recheck
        self._clock = clock
        self._stage = _CAPTURE
        self._base = base
        self._buildings = target.object_id is not None and base is None
        self._pass: MissionReadPass | None = MissionReadPass(mission, retain=True, clock=clock,
                                                             buildings=self._buildings)
        self._digests: tuple[str | None, ...] = ()
        self._sampling: ObjectTierRead | None = None
        self._result: dict[str, Any] | None = None

    @property
    def stage(self) -> str:
        """Return the private stage: capture, sample, capacity or proof."""
        return self._stage

    def advance(self) -> dict[str, Any]:
        """Run one call: a reader slice first when the stage reads, then sampling until the call target.

        An object answer then takes one call for its building capacity and the proof pass after it.
        """
        start = self._clock()
        deadline = start + CALL_TARGET_SECONDS
        if self._stage == _CAPTURE:
            outcome = self._slice(start)
            if outcome is not None:
                return outcome
            # The checks run after the pass, outside the reader side
            content = self._pass.content()
            snapshot = snapshot_from_pass(self._mission, content, self._pass.digests())
            self._pass = None
            if self._buildings:
                self._base = content.buildings
            if self._target.object_id is None:
                return self._point(snapshot)
            values = snapshot.require_tiers()
            self._digests = snapshot.digests
            # The job proves freshness with the read passes, so the sampling gate is always open
            self._sampling = ObjectTierRead(self._plan, self._target.object_id, snapshot.capabilities, values,
                                            self._source.object_source(), lambda: True, clock=self._clock)
            self._stage = _SAMPLE
        if self._stage == _SAMPLE:
            answer = self._sampling.advance(deadline)
            if answer["status"] == "running":
                return {"status": "running", "phase": SAMPLING, "rows_done": answer["rows_done"],
                        "rows_total": answer["rows_total"]}
            # The capacity step gets the next call of its own, so it never shares a call with capture or sampling
            rows = self._sampling.sampling.rows_total
            self._result, self._sampling, self._stage = answer["result"], None, _CAPACITY
            return {"status": "running", "phase": SAMPLING, "rows_done": rows, "rows_total": rows}
        if self._stage == _CAPACITY:
            # No reader side is held here. A call boundary follows the capture, so the proof pass always runs
            self._result = self._with_capacity(self._result)
            self._stage = _PROOF
            self._pass = MissionReadPass(self._mission, retain=False, clock=self._clock, buildings=self._buildings)
            return reading()
        outcome = self._slice(start)
        if outcome is not None:
            return outcome
        # Only equal full digests prove the content; equal metadata never does
        if self._pass.digests() != self._digests:
            return STALE
        return {"status": "done", "result": self._result}

    def _slice(self, start: float) -> dict[str, Any] | None:
        """Read one slice under the reader side; return an answer unless the pass is now complete."""
        try:
            with self._reader.hold(SLICE_READER_WAIT_SECONDS):
                # The trusted context and the plan binding are rechecked in every reader-held slice
                if not self._recheck():
                    return STALE
                # The minimum read time starts after the recheck, so a slow recheck cannot starve the read
                checked = self._clock()
                self._pass.read_slice(max(start + CALL_TARGET_SECONDS, checked + MINIMUM_READ_SECONDS))
        except ServerFolderBusy:
            return reading()
        if self._pass.changed:
            return STALE
        return None if self._pass.complete else reading()

    def _point(self, snapshot: Any) -> dict[str, Any]:
        """Return the point answer in the call that ended the capture; it needs no proof pass."""
        x, z = self._target.point
        result = evaluate_location(self._plan, snapshot.capabilities, snapshot.values, x, z)
        # A point selects no building, so capacity stays null with its reason (Product Owner 15:19:49)
        result.update(plan_source=self._source.kind, plan_revision=self._source.revision,
                      capacity_reason=POINT_TARGET)
        return {"status": "done", "result": result}

    def _with_capacity(self, result: dict[str, Any]) -> dict[str, Any]:
        """Return the object result with the capacity of the buildings it covers; the base is then released."""
        capacity = object_capacity(self._plan, self._target.object_id, self._base or {})
        self._base = None
        return {**result, "capacity": capacity, "capacity_reason": None}
