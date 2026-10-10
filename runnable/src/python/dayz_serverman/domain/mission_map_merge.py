"""Replace and Merge of an imported or copied plan into the destination draft, with its compatibility report (D7)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .mission_map_objects import vertex_count
from .mission_map_plan import (
    STORED_ONLY_FIELDS, PlanForm, bounds_of, new_identifier, parse_objects, parse_plan,
)
from .mission_map_values import MAX_OBJECTS, MAX_PLAN_VERTICES, PlanValidationError, document_error

# Answers why an object is unavailable at the destination, or None; task 3 and task 7 supply the capability check
Availability = Callable[[dict[str, Any]], "str | None"]


class TransferMode(str, Enum):
    """How imported objects reach the destination draft."""

    REPLACE = "replace"
    MERGE = "merge"


@dataclass(frozen=True)
class Candidate:
    """Imported or copied content after D1 validation against the destination bounds."""

    objects: list[dict[str, Any]]
    terrain: dict[str, Any]
    global_abundance: int
    background: dict[str, Any] | None
    # Objects that fail a bounds-dependent rule; they leave only after the admin confirms
    excluded: list[dict[str, Any]] = field(default_factory=list)
    # Stored-only fields and the local image path that the import dropped
    discarded: list[str] = field(default_factory=list)
    # Items of a foreign format that have no place in the plan; filled by the R21 migration
    unmapped: list[dict[str, Any]] = field(default_factory=list)


def read_candidate(raw: object, destination: dict[str, Any], *, keep_path: bool = False) -> Candidate:
    """Validate a plan document for the destination; a bounds-independent failure refuses all of it."""
    if not isinstance(raw, dict):
        raise document_error("the plan document must be a JSON object")
    # Drop the stored-only fields and, unless the plan is copied on this machine, the local image path
    discarded = sorted(STORED_ONLY_FIELDS & set(raw))
    portable = {key: value for key, value in raw.items() if key not in STORED_ONLY_FIELDS}
    background = portable.get("background")
    path = background.get("path") if isinstance(background, dict) else None
    if isinstance(background, dict) and "path" in background:
        portable["background"] = {key: value for key, value in background.items() if key != "path"}
        if not keep_path:
            discarded.append("background.path")
    # Check every document-level rule with the version policy first, then the objects separately
    raw_objects = portable.get("objects")
    shell = parse_plan(dict(portable, objects=[]), PlanForm.PORTABLE)
    if not isinstance(raw_objects, list) or len(raw_objects) > MAX_OBJECTS:
        raise document_error(f"objects must be a list of at most {MAX_OBJECTS} items")
    objects, problems = parse_objects(raw_objects, bounds_of(destination))
    if any(not problem.bounds_dependent for problem in problems):
        raise PlanValidationError(problems)
    # Objects outside the destination bounds are listed with their reason, never clipped or moved
    excluded = [{"index": problem.object_index, "id": problem.object_id,
                 "name": _name(raw_objects[problem.object_index]), "reason": problem.message,
                 "linked_loot": _linked_loot(raw_objects[problem.object_index])} for problem in problems]
    if shell["background"] is not None:
        shell["background"]["path"] = path if keep_path else None
    return Candidate(objects, shell["terrain"], shell["global_abundance"], shell["background"], excluded, discarded)


def combine(destination: dict[str, Any], candidate: Candidate,
            mode: TransferMode) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Return the new draft and the remapped identifiers; association, plan ID, records and terrain stay."""
    result = copy.deepcopy(destination)
    imported = copy.deepcopy(candidate.objects)
    remapped: list[dict[str, str]] = []
    if mode is TransferMode.REPLACE:
        # The imported list and global value replace the destination's; a missing background takes the import's
        result["objects"] = imported
        result["global_abundance"] = candidate.global_abundance
        if result["background"] is None and candidate.background is not None:
            result["background"] = copy.deepcopy(candidate.background)
        return result, remapped
    # Merge inserts the imported block at index 0 in its order; colliding identifiers get new random ones
    taken = {item["id"].casefold() for item in destination["objects"]}
    used = [item["id"] for item in destination["objects"]] + [item["id"] for item in imported]
    for item in imported:
        if item["id"].casefold() in taken:
            fresh = new_identifier(used)
            remapped.append({"from": item["id"], "to": fresh})
            used.append(fresh)
            item["id"] = fresh
    result["objects"] = imported + result["objects"]
    return result, remapped


def compatibility(destination: dict[str, Any], candidate: Candidate, mode: TransferMode,
                  availability: Availability | None = None,
                  combined: tuple[dict[str, Any], list[dict[str, str]]] | None = None) -> dict[str, Any]:
    """Return the D7 preview: terrain, bounds, counts, identifiers, insertion, exclusions and unavailable objects.

    An apply passes the draft it will save as combined, so the reported identifiers are the saved ones.
    """
    imported_bounds = candidate.terrain["bounds"]
    own_bounds = destination["terrain"]["bounds"]
    terrain_match = candidate.terrain["terrain_id"].casefold() == destination["terrain"]["terrain_id"].casefold()
    bounds_equal = imported_bounds == own_bounds
    result, remapped = combined or combine(destination, candidate, mode)
    # A preview names the identifiers to renew; the new random ones are drawn at apply and reported there
    if combined is None:
        remapped = [{"from": entry["from"]} for entry in remapped]
    report: dict[str, Any] = {
        "mode": mode.value, "terrain_match": terrain_match, "bounds_equal": bounds_equal,
        "bounds_difference": {name: imported_bounds[name] - own_bounds[name] for name in own_bounds},
        "imported_terrain_id": candidate.terrain["terrain_id"], "object_count": len(candidate.objects),
        "insertion": "replace" if mode is TransferMode.REPLACE else "top", "remapped": remapped,
        "excluded": candidate.excluded, "excluded_count": len(candidate.excluded),
        "discarded": candidate.discarded, "unmapped": candidate.unmapped,
        "global_abundance": {"destination": destination["global_abundance"],
                             "imported": candidate.global_abundance, "result": result["global_abundance"]},
        "unavailable": [{"id": item["id"], "name": item["name"], "reason": reason} for item in candidate.objects
                        if availability is not None and (reason := availability(item)) is not None],
        "result_object_count": len(result["objects"]),
        "result_vertex_count": sum(vertex_count(item) for item in result["objects"]),
    }
    # The result must pass D1 as a whole; a plan-level limit refuses the import with the counts
    report["refusal"] = None
    if report["result_object_count"] > MAX_OBJECTS or report["result_vertex_count"] > MAX_PLAN_VERTICES:
        report["refusal"] = (f"the result would hold {report['result_object_count']} objects and "
                             f"{report['result_vertex_count']} polygon vertices; the limits are {MAX_OBJECTS} "
                             f"and {MAX_PLAN_VERTICES}")
    # Another terrain or other bounds need a placement review; exclusions and unmapped items need confirmation
    report["placement_review"] = not (terrain_match and bounds_equal)
    report["confirmation_required"] = bool(report["placement_review"] or candidate.excluded or candidate.unmapped)
    return report


def _linked_loot(raw: object) -> dict[str, Any] | None:
    """Return the linked loot that leaves with an excluded encounter (enabled and loot radius), or None."""
    loot = raw.get("linked_loot") if isinstance(raw, dict) and raw.get("kind") == "encounter" else None
    return {"enabled": loot["enabled"], "radius": loot["radius"]} if isinstance(loot, dict) else None


def _name(raw: object) -> str | None:
    """Return the name of a raw object for the exclusion list, when it has one."""
    name = raw.get("name") if isinstance(raw, dict) else None
    return name.strip() if isinstance(name, str) else None
