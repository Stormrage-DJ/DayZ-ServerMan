"""Building capacity of one Inspect object target (R14; 6.9 Architect ruling 2026-10-13 15:18:59).

Only an object target selects buildings: the mapgrouppos.xml and
mapgroupdirt.xml placements whose own X/Z the object covers (D9). The answer
has one entry for each source group and effective adjustment, with the
placement count, the 6.9 capacity entry and the kept reason where it applies.
An entry without capacity names the reason: an unresolved source, an
unsupported ceiling, or a Dirt placement that Apply cannot move.

The base is the 8.6 base: the working baseline, or the current mission files
without one. The values come from the same 6.7 computation as Apply, so
nothing is invented. Cluster files are not read. A point target selects no
building; its answer names the reason POINT_TARGET instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.medical_zone_rule import is_reserved
from ..domain.mission_map_evaluation_shapes import loot_shape
from ..domain.mission_map_native_capacity import CapacityRefusal, capacity_target
from .medical_zone_scan import Node
from .mission_map_loot_report_capacity import GENERATED, NONE, ORDINARY, adjustment_at, capacity_entry, winner
from .mission_map_loot_report_facts import node_facts
from .mission_map_mapgroup_clone import full_key, generated_name
from .mission_map_mapgroup_read import (
    DIRT, PLACEMENT_GEOMETRY, POS, PROTO, RESERVED_CONFLICT, SOURCE_MISSING, MapgroupRefused, Prototype,
    baseline_source, read_placements, read_prototype,
)
from .mission_map_mapgroup_routing import DIRT_PLACEMENT, PLANNER_KEPT, neutral

# Reason code of a point answer, whose capacity is null: a point selects no building
POINT_TARGET = "point_target"


def object_capacity(plan: dict[str, Any], object_id: str, base: Mapping[str, bytes | None]) -> list[dict[str, Any]]:
    """Return the capacity entries of the buildings that one object of a validated plan covers.

    base maps mapgroupproto.xml, mapgrouppos.xml and mapgroupdirt.xml to their
    bytes, None for an absent file. An object without a loot area covers no
    building.

    A base that 6.8 refuses as a whole gives one entry with the refusal reason
    and no capacity. The reasons are an unreadable file and a reserved name in
    any of the files. Another one is a placement without a finite position or
    rotation when the plan changes capacity.
    """
    item = next(entry for entry in plan["objects"] if entry["id"] == object_id)
    shape = loot_shape(item)
    if shape is None:
        return []
    shapes = [found for entry in plan["objects"] if (found := loot_shape(entry)) is not None]
    global_value = plan["global_abundance"]
    try:
        prototype = None if base.get(PROTO) is None else read_prototype(base[PROTO])
        documents = [read_placements(path, base[path]) for path in (POS, DIRT) if base.get(path) is not None]
    except MapgroupRefused as error:
        return [_entry(None, None, 0, None, error.reason, None)]
    # 6.8 refuses a group with a reserved name at any depth of the three files, also for a neutral plan
    roots = ([prototype.root] if prototype is not None else []) + [root for root, _items in documents]
    if any(is_reserved(name) for root in roots for name in _group_names(root)):
        return [_entry(None, None, 0, None, RESERVED_CONFLICT, None)]
    placements = [placement for _root, items in documents for placement in items]
    if not neutral(plan) and not all(placement.geometry_valid for placement in placements):
        return [_entry(None, None, 0, None, PLACEMENT_GEOMETRY, None)]
    # Each group key is the source (or the unresolved name), the adjustment, the kept reason and the refusal
    groups: dict[tuple[str, int, str | None, str | None], dict[str, Any]] = {}
    for placement in placements:
        if placement.position is None:
            continue
        x, z = placement.position[0], placement.position[2]
        if not shape.covers(x, z):
            continue
        adjustment = adjustment_at(shapes, global_value, x, z)
        affected = adjustment != 0 or global_value != 0
        node, reason = prototype.resolve(placement.name) if prototype is not None else (None, SOURCE_MISSING)
        kept = PLANNER_KEPT if placement.planner and affected else None
        # 6.8 keeps an affected planner placement without resolving it, so a missing group is no refusal
        reason = None if kept else reason
        name = node.attributes.get("name", "") if node is not None else placement.name
        group = groups.setdefault((name, adjustment, kept, reason), {"node": node, "count": 0, "dirt": False,
                                                                     "zones": set()})
        group["count"] += 1
        group["dirt"] = group["dirt"] or placement.file == DIRT
        found = winner(shapes, x, z)
        if found is not None:
            group["zones"].add(found.identifier)
    result = []
    for (name, adjustment, kept, reason), group in sorted(groups.items(), key=lambda entry: entry[0][:2]):
        if group["node"] is None:
            result.append(_entry(name, adjustment, group["count"], kept, reason, None))
            continue
        refusal, capacity = _capacity(prototype, group["node"], adjustment, global_value, kept, group)
        result.append(_entry(name, adjustment, group["count"], kept, refusal, capacity))
    return result


def _capacity(prototype: Prototype, node: Node, adjustment: int, global_value: int, kept: str | None,
              group: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Return the refusal reason, or the 6.9 capacity entry of one source at one adjustment.

    A kept placement keeps the baseline capacity of its group. A Dirt placement
    that would need a generated group refuses, as Apply does (6.8).
    """
    try:
        source = baseline_source(prototype, node)
        target = capacity_target(source, 0 if kept else adjustment)
        ordinary = capacity_target(source, global_value)
    except CapacityRefusal as refusal:
        return refusal.reason, None
    vector = (target.group.realized, tuple(item.realized for item in target.containers))
    if kept:
        mechanism, name = NONE, None
    elif vector == (ordinary.group.realized, tuple(item.realized for item in ordinary.containers)):
        mechanism, name = ORDINARY, None
    else:
        if group["dirt"]:
            return DIRT_PLACEMENT, None
        key = full_key(source.identity, tuple(item.identity for item in source.containers), vector[0], vector[1])
        mechanism, name = GENERATED, generated_name(key)
    return None, capacity_entry(target, node_facts(node), mechanism, name, sorted(group["zones"]))


def _group_names(root: Node) -> list[str]:
    """Return the name of every group element below a parsed root, at any depth."""
    names, stack = [], [root]
    while stack:
        node = stack.pop()
        if node.tag == "group":
            names.append(node.attributes.get("name", ""))
        stack.extend(node.children)
    return names


def _entry(source: str | None, adjustment: int | None, count: int, kept: str | None, reason: str | None,
           capacity: dict[str, Any] | None) -> dict[str, Any]:
    """Return one plain entry of the object answer; reason says why capacity is null."""
    return {"source": source, "adjustment": adjustment, "placements": count, "kept": kept, "reason": reason,
            "capacity": capacity}
