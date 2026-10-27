"""Capacity targets of a plan from the winning zone at each baseline placement (R08, task 6.7).

The winning zone and the additive global value follow the shared 4.1
evaluation. Targets are group and container ceilings, not item supply quotas
or predicted live item quantities.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .mission_map_evaluation_shapes import LootShape, loot_shape
from .mission_map_native_capacity import BaselineSource, CapacityRefusal, CapacityTarget, capacity_target
from .mission_map_values import ABUNDANCE_PERCENT


@dataclass(frozen=True)
class SourceTargets:
    """The effective adjustment of each placement, in input order, and one target per affected adjustment."""

    source: tuple[str, str]
    adjustments: tuple[int, ...]
    targets: tuple[CapacityTarget, ...]


@dataclass(frozen=True)
class Refusal:
    """A required target that cannot be computed; the whole Apply refuses (detailed design R08)."""

    source: tuple[str, str]
    field: str | None
    reason: str
    placements: tuple[int, ...]


@dataclass(frozen=True)
class CapacityTargets:
    """All source targets and all refusals of one plan."""

    sources: tuple[SourceTargets, ...]
    refusals: tuple[Refusal, ...]


def placement_adjustments(plan: dict[str, Any], points: Sequence[tuple[float, float]]) -> tuple[int, ...]:
    """Return the effective adjustment at each placement X/Z of a validated D1 plan."""
    return _adjustments(_shapes(plan), plan["global_abundance"], points)


def capacity_targets(plan: dict[str, Any], sources: Iterable[tuple[BaselineSource, Sequence[tuple[float, float]]]],
                     *, maximum: int | None = None) -> CapacityTargets:
    """Return the targets of each baseline source from the winning zone at each of its placements.

    A placement with a zero effective adjustment keeps its baseline source and
    needs no target, so it needs no proved ceiling. maximum is the supported
    native limit when evidence proves one.
    """
    shapes = _shapes(plan)
    results, refusals = [], []
    for source, points in sources:
        adjustments = _adjustments(shapes, plan["global_abundance"], points)
        targets = []
        for adjustment in sorted(set(adjustments) - {0}):
            try:
                targets.append(capacity_target(source, adjustment, maximum=maximum))
            except CapacityRefusal as refusal:
                # Name every placement that needs the refused target
                placements = tuple(index for index, value in enumerate(adjustments) if value == adjustment)
                refusals.append(Refusal(source.identity, refusal.field, refusal.reason, placements))
        results.append(SourceTargets(source.identity, adjustments, tuple(targets)))
    return CapacityTargets(tuple(results), tuple(refusals))


def _shapes(plan: dict[str, Any]) -> list[LootShape]:
    """Return the loot contributors in priority order; hidden and locked zones still contribute."""
    return [shape for item in plan["objects"] if (shape := loot_shape(item)) is not None]


def _adjustments(shapes: list[LootShape], global_value: int,
                 points: Sequence[tuple[float, float]]) -> tuple[int, ...]:
    """Add the highest covering zone adjustment to the global value at each point, as 4.1 does."""
    result = []
    for x, z in points:
        winner = next((shape for shape in shapes if shape.covers(x, z)), None)
        result.append(global_value + (ABUNDANCE_PERCENT[winner.abundance] if winner is not None else 0))
    return tuple(result)
