"""Loot zones and encounters of a mission map plan: kinds, provider properties and linked loot (D1, D6)."""

from __future__ import annotations

import re
from typing import Any, Iterable

from .mission_map_geometry import Bounds, polygon_problem
from .mission_map_values import (
    ABUNDANCE_PERCENT, DEFAULT_ABUNDANCE, EVENT_NAME, IDENTIFIER, INFECTED_COUNT_RANGE, MAX_NAME_LENGTH,
    MAX_POLYGON_VERTICES, MIN_POLYGON_VERTICES, MIN_RADIUS, OPEN_KEY, OPEN_MAX_DEPTH, OPEN_MAX_KEYS,
    OPEN_MAX_LIST, OPEN_MAX_STRING, OPEN_PROPERTY_PROVIDERS, RADIUS_PROVIDERS, TIER_NAME, ObjectKind,
    ObjectRuleError, Provider, exact_fields, finite_number, integer, matching_text, plain_name,
)


# Fields of every object, then the extra fields that each kind adds
COMMON_FIELDS = frozenset(("id", "name", "kind", "visible", "locked"))
KIND_FIELDS = {
    ObjectKind.LOOT_CIRCLE: COMMON_FIELDS | {"centre", "radius", "tier", "abundance"},
    ObjectKind.LOOT_POLYGON: COMMON_FIELDS | {"vertices", "tier", "abundance"},
    ObjectKind.ENCOUNTER: COMMON_FIELDS | {"centre", "radius", "provider", "properties", "excluded", "linked_loot"},
}
# Fields of a linked loot object and of the closed native infected properties
LINKED_LOOT_FIELDS = frozenset(("enabled", "tier", "abundance", "radius"))
INFECTED_FIELDS = frozenset(("event_name", "smin", "smax", "dmin", "dmax"))
# Automatic name pattern of each kind: "<pattern> N"
AUTOMATIC_NAMES = {
    ObjectKind.LOOT_CIRCLE: "Loot circle", ObjectKind.LOOT_POLYGON: "Loot polygon", ObjectKind.ENCOUNTER: "Encounter",
}


def parse_object(raw: object) -> dict[str, Any]:
    """Return one normalized plan object after every rule that does not depend on the bounds."""
    if not isinstance(raw, dict):
        raise ObjectRuleError("each object must be an object")
    # The kind decides the closed field set
    try:
        kind = ObjectKind(raw.get("kind"))
    except ValueError as error:
        raise ObjectRuleError("kind must be loot_circle, loot_polygon or encounter") from error
    exact_fields(raw, "object", KIND_FIELDS[kind])
    result: dict[str, Any] = {
        "id": matching_text(raw["id"], "id", IDENTIFIER),
        "name": plain_name(raw["name"], "name", MAX_NAME_LENGTH),
        "kind": kind.value,
        "visible": _flag(raw["visible"], "visible"),
        "locked": _flag(raw["locked"], "locked"),
    }
    # Loot zones carry geometry, tier and abundance directly
    if kind is ObjectKind.LOOT_CIRCLE:
        result["centre"] = parse_point(raw["centre"], "centre")
        result["radius"] = _radius(raw["radius"], "radius")
    elif kind is ObjectKind.LOOT_POLYGON:
        result["vertices"] = _vertices(raw["vertices"])
    if kind is not ObjectKind.ENCOUNTER:
        result["tier"] = _tier(raw["tier"])
        result["abundance"] = _abundance(raw["abundance"])
        return result
    # Encounters carry a provider, its properties, the R11 exclusion and linked loot
    try:
        provider = Provider(raw["provider"])
    except ValueError as error:
        raise ObjectRuleError("provider is not a known encounter provider") from error
    result["centre"] = parse_point(raw["centre"], "centre")
    result["radius"] = _encounter_radius(raw["radius"], provider)
    result["provider"] = provider.value
    if provider in OPEN_PROPERTY_PROVIDERS:
        result["properties"] = parse_open_object(raw["properties"], "properties")
    else:
        result["properties"] = _infected_properties(raw["properties"])
    result["excluded"] = _flag(raw["excluded"], "excluded")
    result["linked_loot"] = _linked_loot(raw["linked_loot"], provider)
    return result


def object_bounds_problem(item: dict[str, Any], bounds: Bounds) -> str | None:
    """Return the first bounds-dependent rule that a parsed object fails, or None."""
    # Each point must lie inside the closed bounds
    points = item["vertices"] if item["kind"] == ObjectKind.LOOT_POLYGON.value else [item["centre"]]
    for point in points:
        if not bounds.contains(point["x"], point["z"]):
            return "geometry lies outside the terrain bounds"
    # Each radius that is not null is at most the larger extent
    radii = [item.get("radius")]
    if item["kind"] == ObjectKind.ENCOUNTER.value:
        radii.append(item["linked_loot"]["radius"])
    if any(radius is not None and radius > bounds.max_radius for radius in radii):
        return "a radius is larger than the larger terrain extent"
    return None


def automatic_name(kind: ObjectKind, objects: Iterable[dict[str, Any]]) -> str:
    """Return "<pattern> N", where N is 1 more than the highest number that a name of the pattern uses."""
    pattern = re.compile(re.escape(AUTOMATIC_NAMES[kind]) + r" ([0-9]+)")
    numbers = [int(match.group(1)) for item in objects if (match := pattern.fullmatch(item["name"]))]
    return f"{AUTOMATIC_NAMES[kind]} {max(numbers, default=0) + 1}"


def new_object_fields(kind: ObjectKind, identifier: str, name: str) -> dict[str, Any]:
    """Return the default fields of a new visible, unlocked object; geometry and provider come from the caller."""
    item: dict[str, Any] = {"id": identifier, "name": name, "kind": kind.value, "visible": True, "locked": False}
    # Encounters start with disabled linked loot that would default to Rich and Inherit (R07, D1)
    if kind is ObjectKind.ENCOUNTER:
        item.update(excluded=False, linked_loot={
            "enabled": False, "tier": None, "abundance": DEFAULT_ABUNDANCE, "radius": None})
    # Loot zones inherit the underlying tier and default to Rich
    else:
        item.update(tier=None, abundance=DEFAULT_ABUNDANCE)
    return item


def vertex_count(item: dict[str, Any]) -> int:
    """Return the number of polygon vertices that an object adds to the plan total."""
    return len(item.get("vertices", ()))


def parse_point(value: object, field: str) -> dict[str, float]:
    """Return a native point {x, z} with finite float coordinates."""
    point = exact_fields(value, field, {"x", "z"})
    return {"x": finite_number(point["x"], f"{field}.x"), "z": finite_number(point["z"], f"{field}.z")}


def parse_open_object(value: object, field: str, depth: int = 1) -> dict[str, Any]:
    """Return a copy of an open bounded provider object; only its size and key form are checked."""
    if not isinstance(value, dict):
        raise ObjectRuleError(f"{field} must be an object")
    if depth > OPEN_MAX_DEPTH:
        raise ObjectRuleError(f"{field} is nested deeper than {OPEN_MAX_DEPTH} levels")
    if len(value) > OPEN_MAX_KEYS:
        raise ObjectRuleError(f"{field} has more than {OPEN_MAX_KEYS} keys")
    result: dict[str, Any] = {}
    for key, item in value.items():
        matching_text(key, f"{field} key", OPEN_KEY)
        result[key] = _open_value(item, f"{field}.{key}", depth)
    return result


def _open_value(value: object, field: str, depth: int) -> Any:
    """Return a copy of one JSON value inside an open bounded object at the given container depth."""
    # Scalars: null, booleans, finite numbers and short strings
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        finite_number(value, field)
        return value
    if isinstance(value, str):
        if len(value) > OPEN_MAX_STRING:
            raise ObjectRuleError(f"{field} is longer than {OPEN_MAX_STRING} characters")
        return value
    # Containers count toward the depth limit
    if isinstance(value, dict):
        return parse_open_object(value, field, depth + 1)
    if isinstance(value, list):
        if depth + 1 > OPEN_MAX_DEPTH:
            raise ObjectRuleError(f"{field} is nested deeper than {OPEN_MAX_DEPTH} levels")
        if len(value) > OPEN_MAX_LIST:
            raise ObjectRuleError(f"{field} has more than {OPEN_MAX_LIST} items")
        return [_open_value(item, field, depth + 1) for item in value]
    raise ObjectRuleError(f"{field} is not a JSON value")


def _infected_properties(value: object) -> dict[str, Any]:
    """Return the closed native infected properties with ordered population ranges."""
    raw = exact_fields(value, "properties", INFECTED_FIELDS)
    low, high = INFECTED_COUNT_RANGE
    result = {"event_name": matching_text(raw["event_name"], "event_name", EVENT_NAME)}
    for name in ("smin", "smax", "dmin", "dmax"):
        result[name] = integer(raw[name], name, low, high)
    # Each minimum is at most its maximum
    if result["smin"] > result["smax"] or result["dmin"] > result["dmax"]:
        raise ObjectRuleError("each infected minimum must be at most its maximum")
    return result


def _linked_loot(value: object, provider: Provider) -> dict[str, Any]:
    """Return linked loot; its own radius exists only for a provider without a spawn radius (D6)."""
    raw = exact_fields(value, "linked_loot", LINKED_LOOT_FIELDS)
    enabled = _flag(raw["enabled"], "linked_loot.enabled")
    result = {"enabled": enabled, "tier": _tier(raw["tier"]), "abundance": _abundance(raw["abundance"])}
    # A spawn-radius provider shares its radius, so linked loot holds none
    if provider in RADIUS_PROVIDERS:
        if raw["radius"] is not None:
            raise ObjectRuleError("linked loot of this provider uses the encounter radius")
        result["radius"] = None
    elif raw["radius"] is None:
        if enabled:
            raise ObjectRuleError("enabled linked loot of this provider needs its own loot radius")
        result["radius"] = None
    else:
        result["radius"] = _radius(raw["radius"], "linked_loot.radius")
    return result


def _encounter_radius(value: object, provider: Provider) -> float | None:
    """Return the spawn radius: required for a native provider, null for one without a spawn radius."""
    if provider in RADIUS_PROVIDERS:
        if value is None:
            raise ObjectRuleError("this provider needs a spawn radius")
        return _radius(value, "radius")
    if value is not None:
        raise ObjectRuleError("this provider has no spawn radius")
    return None


def _vertices(value: object) -> list[dict[str, float]]:
    """Return the open vertex list of a simple polygon of at least 1 m²."""
    if not isinstance(value, list) or not MIN_POLYGON_VERTICES <= len(value) <= MAX_POLYGON_VERTICES:
        raise ObjectRuleError(f"vertices must list {MIN_POLYGON_VERTICES} to {MAX_POLYGON_VERTICES} points")
    vertices = [parse_point(point, "vertex") for point in value]
    problem = polygon_problem([(point["x"], point["z"]) for point in vertices])
    if problem is not None:
        raise ObjectRuleError(problem)
    return vertices


def _radius(value: object, field: str) -> float:
    """Return a radius of at least the minimum; the bounds limit is checked separately."""
    radius = finite_number(value, field)
    if radius < MIN_RADIUS:
        raise ObjectRuleError(f"{field} must be at least {MIN_RADIUS:g} m")
    return radius


def _tier(value: object) -> str | None:
    """Return a value-flag tier name, or None to inherit the underlying tier."""
    return None if value is None else matching_text(value, "tier", TIER_NAME)


def _abundance(value: object) -> str:
    """Return an abundance preset name (R07)."""
    if not isinstance(value, str) or value not in ABUNDANCE_PERCENT:
        raise ObjectRuleError("abundance must be sparse, normal, rich or very_rich")
    return value


def _flag(value: object, field: str) -> bool:
    """Return a JSON boolean."""
    if not isinstance(value, bool):
        raise ObjectRuleError(f"{field} must be true or false")
    return value
