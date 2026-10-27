"""One-time migration of Pripyat prototype plan exports (schema 1 and 2) into a portable plan document (R21)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .mission_map_objects import parse_object
from .mission_map_values import (
    IDENTIFIER, MAX_TERRAIN_EXTENT, PLAN_FORMAT, PLAN_VERSION, ObjectRuleError, document_error,
)


# Prototype schema versions that the migration reads; schema 1 holds bandit points only
PROTOTYPE_SCHEMAS = (1, 2)
# The prototype only planned Pripyat; its coordinates are native X and Z from (0, 0)
PROTOTYPE_TERRAIN = "Pripyat"
# Top-level fields of a prototype export and the policy texts among them
ZONE_FIELD, POINT_FIELD = "lootOverrides", "points"
POLICY_FIELDS = ("weaponPolicy", "lootPolicy")
KNOWN_FIELDS = frozenset(("schemaVersion", "terrain", "world", ZONE_FIELD, POINT_FIELD, *POLICY_FIELDS))
# Prototype tier numbers and the value flags they name
PROTOTYPE_TIERS = (1, 2, 3, 4)
# Imported zones change no abundance: the export has none (R21)
IMPORTED_ABUNDANCE = "normal"


@dataclass(frozen=True)
class Migration:
    """A portable plan document made from a prototype export, and the items that have no place in it."""

    document: dict[str, Any]
    unmapped: list[dict[str, Any]]


def is_prototype_export(value: object) -> bool:
    """Return whether a JSON value looks like a prototype export: no plan format marker, a schemaVersion."""
    return isinstance(value, dict) and "format" not in value and "schemaVersion" in value


def migrate(value: dict[str, Any]) -> Migration:
    """Map the zones of a prototype export exactly and list everything else; nothing is repaired or guessed."""
    schema, world = value.get("schemaVersion"), value.get("world")
    # Only the two prototype schemas of the Pripyat planner are read
    if not _whole(schema) or schema not in PROTOTYPE_SCHEMAS:
        raise document_error("the file is not a supported prototype export (schema 1 or 2)")
    if isinstance(world, bool) or not isinstance(world, (int, float)) or not (
            math.isfinite(world) and 0 < world <= MAX_TERRAIN_EXTENT):
        raise document_error("the prototype export has no valid world size")
    terrain = value.get("terrain", PROTOTYPE_TERRAIN)
    if not isinstance(terrain, str) or terrain.casefold() != PROTOTYPE_TERRAIN.casefold():
        raise document_error("only Pripyat prototype exports can be migrated")
    points, zones = value.get(POINT_FIELD, []), value.get(ZONE_FIELD, []) if schema == 2 else []
    if not isinstance(points, list) or not isinstance(zones, list):
        raise document_error("the prototype points and loot zones must be lists")
    # List the bandit points, the policy texts and any other field; none of them enters the plan
    unmapped = [_point(point) for point in points]
    unmapped += [{"kind": "policy", "id": name, "detail": str(value[name])[:256],
                  "reason": "a policy text has no place in the plan"} for name in POLICY_FIELDS if name in value]
    extra = sorted(set(value) - KNOWN_FIELDS) + ([ZONE_FIELD] if schema == 1 and ZONE_FIELD in value else [])
    unmapped += [{"kind": "field", "id": name, "detail": None, "reason": "the field is not part of the prototype "
                  "schema that the migration reads"} for name in extra]
    # The last prototype zone wins overlaps, so it becomes the top of the list
    objects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for zone in reversed(zones):
        try:
            mapped = _zone(zone)
            if {item["id"].casefold() for item in mapped} & seen:
                raise ObjectRuleError("the zone identifier is used twice")
        except ObjectRuleError as error:
            unmapped.append({"kind": "zone", "id": _zone_id(zone), "detail": None, "reason": str(error)})
            continue
        seen |= {item["id"].casefold() for item in mapped}
        objects.extend(mapped)
    document = {
        "format": PLAN_FORMAT, "version": PLAN_VERSION,
        "terrain": {"terrain_id": PROTOTYPE_TERRAIN, "bounds": {"x_min": 0, "z_min": 0, "width": world,
                    "height": world}, "bounds_source": "manual", "bounds_confirmed": False},
        "global_abundance": 0, "objects": objects, "background": None,
    }
    return Migration(document, unmapped)


def _zone(zone: object) -> list[dict[str, Any]]:
    """Return the plan objects of one prototype zone: one polygon, or one circle for each brush stroke point."""
    if not isinstance(zone, dict) or set(zone) - {"id", "tier", "kind", "points", "radius"}:
        raise ObjectRuleError("the zone does not have the prototype zone fields")
    identifier, tier, points = zone.get("id"), zone.get("tier"), zone.get("points")
    if not isinstance(identifier, str) or IDENTIFIER.fullmatch(identifier) is None:
        raise ObjectRuleError("the zone identifier is not valid")
    if not _whole(tier) or tier not in PROTOTYPE_TIERS:
        raise ObjectRuleError("the zone tier must be 1, 2, 3 or 4")
    if not isinstance(points, list) or not points or not all(
            isinstance(point, list) and len(point) == 2 for point in points):
        raise ObjectRuleError("the zone points must be [X, Z] pairs")
    common = {"visible": True, "locked": False, "tier": f"Tier{tier}", "abundance": IMPORTED_ABUNDANCE}
    # A polygon keeps its vertices; D1 decides whether its outline is valid
    if zone.get("kind") == "polygon" and "radius" not in zone:
        vertices = [{"x": point[0], "z": point[1]} for point in points]
        return [parse_object({"id": identifier, "name": identifier, "kind": "loot_polygon", "vertices": vertices,
                              **common})]
    if zone.get("kind") != "brush":
        raise ObjectRuleError("the zone kind must be polygon or brush")
    # A brush covers every point within its radius of any stroke point: one circle for each stroke point
    single = len(points) == 1
    return [parse_object({"id": identifier if single else f"{identifier}-{number}",
                          "name": identifier if single else f"{identifier} point {number}", "kind": "loot_circle",
                          "centre": {"x": point[0], "z": point[1]}, "radius": zone.get("radius"), **common})
            for number, point in enumerate(points, 1)]


def _point(point: object) -> dict[str, Any]:
    """Return the unmapped-item entry of one bandit point."""
    fields = point if isinstance(point, dict) else {}
    detail = (f"group of {fields.get('count')} at X {fields.get('x')}, Z {fields.get('z')}, "
              f"weapon {fields.get('weapon')}")
    return {"kind": "bandit_point", "id": fields.get("id") if isinstance(fields.get("id"), str) else None,
            "detail": detail, "reason": "AI_Bandits points are not mapped before task 7, and the deployed groups "
            "already exist in the provider file"}


def _whole(value: object) -> bool:
    """Return whether a JSON value is an integer, not a boolean or a float."""
    return isinstance(value, int) and not isinstance(value, bool)


def _zone_id(zone: object) -> str | None:
    """Return the identifier of a raw zone for the unmapped list, when it has one."""
    identifier = zone.get("id") if isinstance(zone, dict) else None
    return identifier if isinstance(identifier, str) else None
