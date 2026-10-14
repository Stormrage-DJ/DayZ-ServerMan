"""Loot candidate report of one plan over its tier and capacity candidates (leaf 6.9, R08, R16).

The report is plain data (JSON values only) that the apply preview (8.6) binds
and shows. For each loot zone, it lists what the candidate adds, changes and
removes against the working baseline. It also gives the tier, the capacity
preset and the affected files of the zone.

Other parts list each capacity target with its native limits, the kept
placements and the refusals. They also list abundance-only zones, unavailable
objects and the mechanisms that capacity does not control. The report never
gives a live item count or a predicted live-item percentage, and it reads and
writes no file.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..domain.mission_map_areaflags_candidate import AreaflagsCandidate
from ..domain.mission_map_evaluation import evaluate_plan
from ..domain.mission_map_evaluation_shapes import LootShape, loot_shape
from ..domain.mission_map_loot_report_texts import CAPACITY_EXPLANATION, KEPT_TEXTS, UNAVAILABLE_TEXTS, reason_text
from ..domain.mission_map_terrain import TerrainCapabilities, TerrainDataError
from ..domain.mission_map_values import ABUNDANCE_PERCENT
from .mission_map_loot_report_capacity import CHANGED, ORDINARY, capacity_entries, mechanisms, winner
from .mission_map_loot_report_facts import group_facts, kept_positions, tier_cells
from .mission_map_mapgroup_candidate import MapgroupCandidates
from .mission_map_mapgroup_read import DIRT, POS, PROTO
from .mission_map_terrain import AREAFLAGS_FILE

# Version of the report shape; 8.6 binds the report into the preview fingerprint
REPORT_VERSION = 1
# Files that a detached placement changes: its generated group, its origin and its Dirt copy
DETACH_FILES = (PROTO, POS, DIRT)


def build_loot_report(plan: dict[str, Any], *, capabilities: TerrainCapabilities,
                      baseline: Mapping[str, bytes | None], areaflags: AreaflagsCandidate | TerrainDataError | None,
                      mapgroups: MapgroupCandidates, clusters: Mapping[str, bytes] | None = None,
                      types: Sequence[bytes] | None = None, current_groups: Iterable[str] = (),
                      maximum: int | None = None) -> dict[str, Any]:
    """Return the report of a validated D1 plan over its candidates.

    baseline holds the working-baseline bytes by relative path (areaflags.map
    and the three mapgroup files). areaflags is the 6.6 candidate, its refusal,
    or None when the mission has no areaflags.map.

    mapgroups is the 6.8 result over the same baseline and clusters. types are
    the item-type documents that the mission economy loads; without them,
    missing supply is unknown. current_groups names the generated groups that
    the mission has now, from the current applied record. maximum is the
    native ceiling limit given to 6.8, or None when no limit is proved.
    """
    shapes = [shape for item in plan["objects"] if (shape := loot_shape(item)) is not None]
    global_value = plan["global_abundance"]
    capacity = capacity_entries(mapgroups, group_facts(baseline.get(PROTO)), shapes, global_value)
    kept = _kept(mapgroups, baseline, clusters, shapes)
    zones = _zones(plan, capabilities, shapes, areaflags, mapgroups, set(current_groups), kept)
    refusals = _refusals(mapgroups, areaflags)
    candidate_groups = {clone.name for clone in mapgroups.clones}
    return {
        "version": REPORT_VERSION,
        "refused": bool(refusals),
        "explanation": CAPACITY_EXPLANATION,
        "native_limit": maximum,
        "global": {"adjustment": global_value,
                   "changed_groups": sorted({entry["source"] for entry in capacity if entry["mechanism"] == ORDINARY
                                             and entry["class"] == CHANGED and entry["adjustment"] == global_value})},
        "zones": zones,
        "removed_groups": [] if mapgroups.refused else sorted(set(current_groups) - candidate_groups),
        "capacity": capacity,
        "kept": kept,
        "refusals": refusals,
        "abundance_only": [zone["id"] for zone in zones if zone["abundance_only"]],
        "unavailable": [{"id": zone["id"], "name": zone["name"], "reason": zone["reason"], "text": zone["reason_text"]}
                        for zone in zones if not zone["available"]],
        "mechanisms": mechanisms(capacity, kept, types),
        "files": _files(baseline, areaflags, mapgroups),
    }


def _zones(plan: dict[str, Any], capabilities: TerrainCapabilities, shapes: list[LootShape],
           areaflags: AreaflagsCandidate | TerrainDataError | None, mapgroups: MapgroupCandidates,
           current: set[str], kept: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one entry for each enabled loot object in plan order: standalone zones and enabled linked loot."""
    evaluation = {item["id"]: item for item in evaluate_plan(plan, capabilities)["objects"]}
    cells = tier_cells(plan, areaflags.content) if isinstance(areaflags, AreaflagsCandidate) else {}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    moved: Counter[str] = Counter()
    for clone in mapgroups.clones:
        counts: Counter[str] = Counter()
        for binding in clone.detached:
            # A generated group has another vector than the global one, so a zone wins at each of its placements
            shape = winner(shapes, binding.position[0], binding.position[2])
            if shape is not None:
                counts[shape.identifier] += binding.multiplicity
        for identifier, count in sorted(counts.items()):
            moved[identifier] += count
            groups[identifier].append({"name": clone.name, "source": clone.source[1], "placements": count,
                                       "present": clone.name in current})
    kept_by_zone: dict[str, Counter[str]] = defaultdict(Counter)
    for item in kept:
        kept_by_zone[item["zone"]][item["reason"]] += 1
    result = []
    for shape in shapes:
        state = evaluation[shape.identifier]
        adjustment = ABUNDANCE_PERCENT[shape.abundance]
        count = cells.get(shape.identifier)
        own = groups[shape.identifier]
        files = ([AREAFLAGS_FILE] if count else []) + (list(DETACH_FILES) if moved[shape.identifier] else [])
        result.append({
            "id": shape.identifier, "name": shape.name, "kind": shape.kind, "hidden": shape.hidden,
            "tier": shape.tier, "abundance": shape.abundance, "zone_adjustment": adjustment,
            "effective_adjustment": plan["global_abundance"] + adjustment, "abundance_only": shape.tier is None,
            "available": state["available"], "reason": state["reason"],
            "reason_text": reason_text(UNAVAILABLE_TEXTS, state["reason"]),
            # Detachment removes a mapgrouppos.xml placement and adds its copy to mapgroupdirt.xml
            "added": {"groups": [item for item in own if not item["present"]], "placements": moved[shape.identifier]},
            "changed": {"tier_cells": count},
            "removed": {"placements": moved[shape.identifier]},
            "unchanged": {"groups": [item for item in own if item["present"]]},
            "kept": dict(sorted(kept_by_zone[shape.identifier].items())),
            "files": files,
        })
    return result


def _kept(mapgroups: MapgroupCandidates, baseline: Mapping[str, bytes | None],
          clusters: Mapping[str, bytes] | None, shapes: list[LootShape]) -> list[dict[str, Any]]:
    """Return each affected placement that stays unchanged, with its reason, its text and its winning zone."""
    positions = kept_positions(baseline, clusters, mapgroups.kept)
    result = []
    for item in mapgroups.kept:
        position = positions.get((item.file, item.index))
        shape = winner(shapes, *position) if position is not None else None
        result.append({"file": item.file, "index": item.index, "name": item.name, "adjustment": item.adjustment,
                       "reason": item.reason, "text": reason_text(KEPT_TEXTS, item.reason),
                       "zone": shape.identifier if shape is not None else None})
    return result


def _refusals(mapgroups: MapgroupCandidates,
              areaflags: AreaflagsCandidate | TerrainDataError | None) -> list[dict[str, Any]]:
    """Return every refusal of the complete Apply with its reason, file, source, field and placements."""
    result = []
    if isinstance(areaflags, TerrainDataError):
        result.append({"reason": areaflags.reason, "file": AREAFLAGS_FILE, "source": None, "field": None,
                       "placements": [], "message": str(areaflags)})
    for item in mapgroups.refusals:
        result.append({"reason": item.reason, "file": item.file, "source": item.source, "field": item.field,
                       "placements": [list(placement) for placement in item.placements], "message": item.message})
    return result


def _files(baseline: Mapping[str, bytes | None], areaflags: AreaflagsCandidate | TerrainDataError | None,
           mapgroups: MapgroupCandidates) -> list[dict[str, Any]]:
    """Return each candidate file with its change flag and its whole-file and owned-value digests (D3)."""
    result = []
    if isinstance(areaflags, AreaflagsCandidate):
        before = baseline.get(AREAFLAGS_FILE)
        changed = before is None or hashlib.sha256(before).hexdigest() != areaflags.sha256
        result.append({"path": AREAFLAGS_FILE, "changed": changed, "sha256": areaflags.sha256,
                       "owned_value_sha256": areaflags.owned_value_sha256})
    result += [{"path": item.path, "changed": item.changed, "sha256": item.sha256,
                "owned_value_sha256": item.owned_value_sha256} for item in mapgroups.files]
    return result
