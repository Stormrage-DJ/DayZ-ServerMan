"""Capacity entries and mechanism limits of the loot candidate report (leaf 6.9, R08).

Each 6.7 target of the 6.8 candidate becomes one entry. The entry keeps the
rational request, the realized integer, the rounding difference and the point
bound apart. The achievable value is min(group, sum(min(container, points)))
and only an upper bound when qualified; it never predicts live items.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

from ..domain.mission_map_evaluation_shapes import LootShape
from ..domain.mission_map_loot_report_texts import BOUND_TEXTS, KEPT_TEXTS, MECHANISM_TEXTS, reason_text
from ..domain.mission_map_native_capacity import CapacityTarget, FieldTarget
from ..domain.mission_map_values import ABUNDANCE_PERCENT
from .mission_map_loot_report_facts import GroupFacts, supplied_categories
from .mission_map_mapgroup_candidate import CloneRealization, MapgroupCandidates

# Entry classes: a ceiling differs from the baseline, or every ceiling keeps its baseline value (6.8 QA F6)
CHANGED, NO_CHANGE = "changed", "no_change"
# How the placements of a target get their capacity: the ordinary group, a generated group, or neither.
# Neither means that only kept placements have this vector, for example cluster placements.
ORDINARY, GENERATED, NONE = "ordinary", "generated", "none"


def winner(shapes: Sequence[LootShape], x: float, z: float) -> LootShape | None:
    """Return the highest enabled loot object that covers a placement at its own X and Z (D9, R06)."""
    return next((shape for shape in shapes if shape.covers(x, z)), None)


def adjustment_at(shapes: Sequence[LootShape], global_value: int, x: float, z: float) -> int:
    """Return the effective adjustment at a placement: the global value plus its winning zone."""
    shape = winner(shapes, x, z)
    return global_value + (ABUNDANCE_PERCENT[shape.abundance] if shape is not None else 0)


def capacity_entries(mapgroups: MapgroupCandidates, facts: dict[str, GroupFacts], shapes: Sequence[LootShape],
                     global_value: int) -> list[dict[str, Any]]:
    """Return one entry for each target of a candidate that is not refused, by source and adjustment."""
    if mapgroups.refused:
        return []
    clones = {(clone.source[1], clone.group_ceiling, clone.container_ceilings): clone for clone in mapgroups.clones}
    # The ordinary vector of a source: its global target, or its baseline when the global value is 0
    ordinary = {target.source: _vector(target) for target in mapgroups.targets if target.adjustment == global_value}
    entries = []
    for target in sorted(mapgroups.targets, key=lambda item: (item.source, item.adjustment)):
        vector = _vector(target)
        same = vector == ordinary.get(target.source, _baseline(target))
        clone = None if same else clones.get((target.source[1], str(vector[0]),
                                              tuple(str(value) for value in vector[1])))
        mechanism = ORDINARY if same else GENERATED if clone is not None else NONE
        entries.append(capacity_entry(target, facts.get(target.source[1]), mechanism,
                                      clone.name if clone is not None else None,
                                      _clone_zones(clone, target, shapes, global_value)))
    return entries


def mechanisms(entries: list[dict[str, Any]], kept: list[dict[str, Any]],
               types: Sequence[bytes] | None) -> dict[str, Any]:
    """Return the mechanisms that capacity does not control and the limits of the changed groups.

    Only entries of class changed count. Missing supply is None when the item types were not read.
    """
    changed = [entry for entry in entries if entry["class"] == CHANGED]
    categories = {entry["source"]: entry["categories"] for entry in changed}
    shared = [{"source": source, "categories": names} for source, names in sorted(categories.items())
              if len(names) > 1]
    supplied = supplied_categories(types)
    missing: dict[str, list[str]] | None = None
    if supplied is not None:
        found: dict[str, set[str]] = defaultdict(set)
        for source, names in categories.items():
            for name in names:
                if name not in supplied:
                    found[name].add(source)
        missing = {name: sorted(sources) for name, sources in sorted(found.items())}
    counts = Counter(item["reason"] for item in kept)
    return {
        "fixed_dispatch": {"groups": sorted({entry["source"] for entry in changed if entry["fixed_dispatch"]}),
                           "text": MECHANISM_TEXTS["fixed_dispatch"]},
        "shared_category_capacity": {"groups": shared, "text": MECHANISM_TEXTS["shared_category_capacity"]},
        "missing_supply": {"categories": missing, "text": MECHANISM_TEXTS["missing_supply" if missing is not None
                                                                          else "missing_supply_unknown"]},
        "uncontrolled_placements": [{"reason": reason, "count": count, "text": reason_text(KEPT_TEXTS, reason)}
                                    for reason, count in sorted(counts.items())],
    }


def capacity_entry(target: CapacityTarget, facts: GroupFacts | None, mechanism: str, generated_group: str | None,
                   zones: list[str]) -> dict[str, Any]:
    """Return the plain entry of one target with its fields, point bound and source facts; Inspect uses it too."""
    points = facts.points if facts is not None else {}
    bound = "unknown" if target.achievable is None else "qualified" if target.qualified else None
    return {
        "file": target.source[0], "source": target.source[1], "adjustment": target.adjustment,
        "factor": str(target.factor), "class": CHANGED if target.changed else NO_CHANGE, "mechanism": mechanism,
        "generated_group": generated_group, "zones": zones,
        "group": _field(target.group),
        "containers": [_field(item, points.get(item.identity)) for item in target.containers],
        "achievable": target.achievable, "qualified": target.qualified, "bound_text": reason_text(BOUND_TEXTS, bound),
        "fixed_dispatch": facts is not None and facts.fixed_dispatch,
        "categories": list(facts.all_categories()) if facts is not None else [],
    }


def _field(item: FieldTarget, points: int | None | bool = False) -> dict[str, Any]:
    """Return one ceiling: baseline, rational request, realized integer and difference; a container adds points.

    points False marks the group ceiling, which has no points of its own.
    """
    entry: dict[str, Any] = {"name": item.identity, "explicit": item.explicit, "baseline": item.baseline,
                             "requested": str(item.requested), "realized": item.realized,
                             "difference": str(item.difference)}
    if points is not False:
        limited = points is not None and points < item.realized
        entry.update(points=points, point_bound=None if points is None else min(item.realized, points),
                     point_limited=limited, point_text=reason_text(BOUND_TEXTS, "point_limited" if limited else None))
    return entry


def _clone_zones(clone: CloneRealization | None, target: CapacityTarget, shapes: Sequence[LootShape],
                 global_value: int) -> list[str]:
    """Return the zones that win at the detached placements of a generated group with this target's adjustment."""
    if clone is None:
        return []
    zones = set()
    for binding in clone.detached:
        x, z = binding.position[0], binding.position[2]
        shape = winner(shapes, x, z)
        if shape is not None and adjustment_at(shapes, global_value, x, z) == target.adjustment:
            zones.add(shape.identifier)
    return sorted(zones)


def _vector(target: CapacityTarget) -> tuple[int, tuple[int, ...]]:
    """Return the realized group and container ceilings of a target."""
    return target.group.realized, tuple(item.realized for item in target.containers)


def _baseline(target: CapacityTarget) -> tuple[int, tuple[int, ...]]:
    """Return the baseline group and container ceilings of a target."""
    return target.group.baseline, tuple(item.baseline for item in target.containers)
