"""Placement routing of the mapgroup capacity candidates (leaf 6.8, R08, D8).

Each ordinary source group takes the capacity of the global adjustment. A
placement whose realized vector differs from that of its source moves to a
generated group with its own vector. Equal source and vector pairs share one
generated group. These cases refuse with their reason when a change needs
them: a missing or ambiguous source, invalid geometry, an
unresolved ceiling and a placement that cannot move. Nothing is guessed, and
nothing is left out silently: affected planner placements and cluster
placements that cannot follow their group are reported as kept.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..domain.mission_map_capacity_targets import placement_adjustments
from ..domain.mission_map_evaluation_shapes import loot_shape
from ..domain.mission_map_native_capacity import CapacityRefusal, CapacityTarget, capacity_target
from ..domain.mission_map_plan import canonical_json
from ..domain.mission_map_values import ABUNDANCE_PERCENT
from .medical_zone_scan import Node
from .mission_map_mapgroup_clone import full_key, generated_name
from .mission_map_mapgroup_read import (
    DIRT, PLACEMENT_GEOMETRY, PROTO, MapgroupRefused, Placement, Prototype, baseline_source,
    read_cluster_placements,
)

# Refusal reasons of the routing; the reader and the 6.7 computation add theirs
DIRT_PLACEMENT = "dirt_placement_not_movable"
NAME_COLLISION = "generated_name_collision"
# Reasons of an affected placement that stays unchanged: planner content is not converted, and the
# editor never writes cluster files (Architect ruling F9)
PLANNER_KEPT = "planner_content"
CLUSTER_KEPT = "cluster_placement"
CLUSTER_ONLY_KEPT = "cluster_only_group"


@dataclass(frozen=True)
class MapgroupRefusal:
    """One refusal of the complete candidate: reason, file, source, field and the placements it concerns."""

    reason: str
    file: str
    source: str | None
    field: str | None
    placements: tuple[tuple[str, int, str], ...]
    message: str


@dataclass(frozen=True)
class KeptPlacement:
    """An affected placement that stays unchanged for an accepted reason, so the report can name it."""

    file: str
    index: int
    name: str
    adjustment: int
    reason: str


@dataclass
class Clone:
    """One generated group: its full key and name, its source group, its target and its placements."""

    key: list[object]
    name: str
    source: Node
    target: CapacityTarget
    placements: list[Placement] = field(default_factory=list)


@dataclass
class Routing:
    """The ordinary groups whose ceilings change, the generated groups and every refusal."""

    sources: list[tuple[Node, CapacityTarget]] = field(default_factory=list)
    clones: dict[str, Clone] = field(default_factory=dict)
    targets: list[CapacityTarget] = field(default_factory=list)
    kept: list[KeptPlacement] = field(default_factory=list)
    refusals: list[MapgroupRefusal] = field(default_factory=list)


def neutral(plan: dict[str, Any]) -> bool:
    """Return whether the plan changes no capacity: global 0 and every loot zone adjustment 0."""
    shapes = [shape for item in plan["objects"] if (shape := loot_shape(item)) is not None]
    return plan["global_abundance"] == 0 and all(ABUNDANCE_PERCENT[shape.abundance] == 0 for shape in shapes)


def route(plan: dict[str, Any], prototype: Prototype | None, placements: list[Placement],
          maximum: int | None, clusters: Mapping[str, bytes] | None = None) -> Routing:
    """Return the routing of every placement of a plan that changes capacity.

    clusters maps each mapgroupcluster file of the mission to its bytes. Its
    placements follow a group that also has a pos or Dirt placement. A group
    that only cluster files place takes no change (Architect ruling F11).
    """
    routing = Routing()
    if prototype is not None:
        placements = placements + _cluster_placements(routing, prototype, clusters or {})
    invalid = [item for item in placements if not item.geometry_valid]
    for path in sorted({item.file for item in invalid}):
        routing.refusals.append(_refusal(PLACEMENT_GEOMETRY, path, None, None,
                                         [item for item in invalid if item.file == path],
                                         f"{path} has placements without a finite position or rotation"))
    valid = [item for item in placements if item.geometry_valid]
    adjustments = placement_adjustments(plan, [(item.position[0], item.position[2]) for item in valid])
    global_value = plan["global_abundance"]
    by_source: dict[int, tuple[Node, list[tuple[Placement, int]]]] = {}
    unresolved: dict[tuple[str, str], list[Placement]] = defaultdict(list)
    for item, adjustment in zip(valid, adjustments):
        affected = adjustment != 0 or global_value != 0
        if item.planner:
            if affected:
                routing.kept.append(KeptPlacement(item.file, item.index, item.name, adjustment, PLANNER_KEPT))
            continue
        node, reason = prototype.resolve(item.name) if prototype is not None else (None, "source_missing")
        if node is None:
            if affected:
                unresolved[(reason, item.name)].append(item)
            continue
        by_source.setdefault(id(node), (node, []))[1].append((item, adjustment))
    for (reason, name), items in sorted(unresolved.items()):
        routing.refusals.append(_refusal(reason, PROTO, name, None, items,
                                         f"placement {name} has no unique source group in {PROTO}"))
    for node, items in by_source.values():
        if all(item.cluster for item, _adjustment in items):
            # No ceiling of the group is read or written; each affected cluster placement is reported
            routing.kept += [KeptPlacement(item.file, item.index, item.name, adjustment, CLUSTER_ONLY_KEPT)
                             for item, adjustment in items if adjustment != 0 or global_value != 0]
            continue
        _route_source(routing, prototype, node, items, global_value, maximum)
    _check_names(routing)
    return routing


def _cluster_placements(routing: Routing, prototype: Prototype, clusters: Mapping[str, bytes]) -> list[Placement]:
    """Return the cluster placements whose name matches a prototype group; refuse a file that is not XML."""
    result: list[Placement] = []
    for path in sorted(clusters):
        try:
            result += read_cluster_placements(path, clusters[path], set(prototype.buckets))
        except MapgroupRefused as error:
            routing.refusals.append(MapgroupRefusal(error.reason, path, None, None, (), str(error)))
    return result


def _route_source(routing: Routing, prototype: Prototype, node: Node, items: list[tuple[Placement, int]],
                  global_value: int, maximum: int | None) -> None:
    """Set the global target of one source and move each placement whose vector differs to a clone."""
    adjustments = {adjustment for _item, adjustment in items}
    if global_value == 0 and adjustments == {0}:
        return
    name = node.attributes.get("name", "")
    try:
        source = baseline_source(prototype, node)
        targets = {value: capacity_target(source, value, maximum=maximum)
                   for value in sorted(adjustments | {global_value})}
    except CapacityRefusal as refusal:
        affected = [item for item, adjustment in items if adjustment != 0 or global_value != 0]
        routing.refusals.append(_refusal(refusal.reason, PROTO, name, refusal.field, affected,
                                         f"group {name} in {PROTO} has no supported capacity"))
        return
    # A zone can cancel a global value other than 0. Its adjustment-0 target is listed too: the generated
    # group keeps the baseline ceilings, and the report shows it as no change (6.8 QA F6).
    routing.targets += [target for value, target in targets.items() if value != 0 or global_value != 0]
    ordinary = targets[global_value]
    if ordinary.changed:
        routing.sources.append((node, ordinary))
    stuck = []
    for item, adjustment in items:
        target = targets[adjustment]
        if _vector(target) == _vector(ordinary):
            continue
        if item.cluster:
            routing.kept.append(KeptPlacement(item.file, item.index, item.name, adjustment, CLUSTER_KEPT))
            continue
        if item.file == DIRT:
            # Existing Dirt bindings stay; only editor detachments are added there
            stuck.append(item)
            continue
        key = full_key(source.identity, tuple(container.identity for container in source.containers),
                       target.group.realized, tuple(value.realized for value in target.containers))
        text = canonical_json(key).decode("utf-8")
        clone = routing.clones.setdefault(text, Clone(key, generated_name(key), node, target))
        clone.placements.append(item)
    if stuck:
        routing.refusals.append(_refusal(DIRT_PLACEMENT, DIRT, name, None, stuck,
                                         f"{DIRT} placements of group {name} need another capacity"))


def _check_names(routing: Routing) -> None:
    """Refuse two unequal full keys whose names agree, without an invented suffix (D8)."""
    names: dict[str, str] = {}
    for text, clone in routing.clones.items():
        if names.setdefault(clone.name, text) != text:
            routing.refusals.append(_refusal(NAME_COLLISION, PROTO, clone.name, None, clone.placements,
                                             f"generated name {clone.name} has two different keys"))


def _vector(target: CapacityTarget) -> tuple[int, tuple[int, ...]]:
    """Return the realized group ceiling and container ceilings of a target."""
    return target.group.realized, tuple(item.realized for item in target.containers)


def _refusal(reason: str, path: str, source: str | None, field_name: str | None, items: list[Placement],
             message: str) -> MapgroupRefusal:
    """Return one refusal that names the placements it concerns."""
    return MapgroupRefusal(reason, path, source, field_name, tuple(item.identity for item in items),
                           f"{message}: {reason}. No file was changed.")
