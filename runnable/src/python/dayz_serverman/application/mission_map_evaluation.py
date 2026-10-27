"""Resumable object evaluation over an immutable snapshot with a final freshness gate."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import Any

from ..domain.mission_map_evaluation_tiers import TierSampling
from ..domain.mission_map_terrain import TerrainCapabilities, ValueRows


# Target work time leaves headroom below the accepted one-second call limit.
SLICE_TARGET_SECONDS = 0.5


class ObjectTierRead:
    """Drive bounded sampling calls; job identity, expiry and reader scope stay with Inspect."""

    def __init__(self, plan: dict[str, Any], object_id: str, capabilities: TerrainCapabilities,
                 values: ValueRows, plan_source: dict[str, Any], freshness: Callable[[], bool], *,
                 clock: Callable[[], float] = monotonic) -> None:
        """Capture one validated plan and immutable layer; inject freshness and clock for tests."""
        self.sampling = TierSampling(plan, object_id, capabilities, values, plan_source)
        self._work = self.sampling.work()
        self._freshness = freshness
        self._clock = clock
        self._sampled = False
        self._answer: dict[str, Any] | None = None

    def advance(self, deadline: float | None = None) -> dict[str, Any]:
        """Return running progress, a fresh final result, or stale without tier data.

        Inspect passes the deadline of its whole call; otherwise one slice target from now applies.
        """
        if self._answer is not None:
            return self._answer
        if deadline is None:
            deadline = self._clock() + SLICE_TARGET_SECONDS
        # Each unit bounds geometry work to one shape row or one value row.
        while self._clock() < deadline:
            if self._sampled:
                if self._freshness():
                    self._answer = {"status": "done", "result": self.sampling.result()}
                else:
                    self._answer = {"status": "stale"}
                return self._answer
            try:
                next(self._work)
            except StopIteration:
                self._sampled = True
        return {"status": "running", "rows_done": self.sampling.rows_done,
                "rows_total": self.sampling.rows_total}
