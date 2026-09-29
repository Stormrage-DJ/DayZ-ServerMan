"""Validation rules for profile-scoped mission configuration."""

from __future__ import annotations

import math
import re
from typing import Any


class MissionValidationError(ValueError):
    """Raised when mission configuration values are invalid."""
    pass


# Economy globals edited as non-negative integers
GLOBAL_INTEGER_KEYS = frozenset({
    "SpawnInitial", "RespawnAttempt", "ZombieMaxCount",
    "CleanupLifetimeDeadInfected", "ZoneSpawnDist", "AnimalMaxCount",
    "CleanupLifetimeDeadAnimal", "CleanupAvoidance", "LootSpawnAvoidance",
    "CleanupLifetimeRuined", "CleanupLifetimeDefault", "CleanupLifetimeDeadPlayer",
    "TimeLogin", "TimeLogout", "TimePenalty",
})
# Economy globals edited as booleans
GLOBAL_BOOLEAN_KEYS = frozenset({"FoodDecay", "WorldWetTempUpdate"})
# Loot damage ratios bounded from 0.0 through 1.0
LOOT_DAMAGE_KEYS = frozenset({"LootDamageMin", "LootDamageMax"})
# All managed economy globals
GLOBAL_KEYS = GLOBAL_INTEGER_KEYS | GLOBAL_BOOLEAN_KEYS | LOOT_DAMAGE_KEYS
# Editable fields of named dynamic events
EVENT_FIELDS = frozenset({
    "active", "nominal", "lifetime", "restock", "saferadius",
    "distanceradius", "cleanupradius",
})
# Editable fields of animal and vehicle populations
POPULATION_FIELDS = frozenset({"active", "nominal", "min", "max"})
# Dynamic events managed by the mission editor
EVENT_NAMES = frozenset({
    "StaticHeliCrash", "StaticMilitaryConvoy", "StaticPoliceCar",
    "StaticPoliceSituation", "StaticTrain", "StaticAirplaneCrate",
    "StaticContaminatedArea", "StaticBonfire", "StaticChristmasTree",
    "StaticSantaCrash",
})
# Animal and vehicle populations managed by the mission editor
POPULATION_NAMES = frozenset({
    "VehicleCivilianSedan", "VehicleHatchback02", "VehicleOffroad02",
    "VehicleOffroadHatchback", "VehicleSedan02", "VehicleTruck01", "VehicleBoat",
    "AnimalBear", "AnimalCow", "AnimalDeer", "AnimalGoat", "AnimalPig",
    "AnimalRoeDeer", "AnimalSheep", "AnimalWildBoar", "AnimalWolf",
    "AmbientFox", "AmbientHare", "AmbientHen",
})
# Starter item identifier: ASCII letters, digits, and underscore
ITEM_ID = re.compile(r"[A-Za-z0-9_]+")


def validate_mission_updates(target: str, value: object) -> dict[str, Any]:
    """Return validated mission updates for one target area."""
    if not isinstance(value, dict) or not value:
        raise MissionValidationError("updates must be a non-empty object")
    # Dispatch to the validator that owns the target area
    validator = {"economy": _economy, "weather": _weather,
                 "spawnable_damage": _spawnable, "starter_loadout": _starter,
                 "events": _events}.get(target)
    if validator is None:
        raise MissionValidationError("mission target is not supported")
    return validator(value)


def validate_proposed_state(target: str, current: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Merge a partial update, then validate every managed persisted value."""
    # Events merge by name so unrelated entries stay intact
    if target == "events":
        merged = {"events": {name: dict(fields) for name, fields in current["events"].items()}}
        for name, fields in updates["events"].items():
            merged["events"].setdefault(name, {}).update(fields)
        return _events(merged, require_complete=True)
    # A new damage cap must stay below the stored maximum
    if target == "spawnable_damage" and updates["maximum"] >= current["maximum"]:
        raise MissionValidationError("spawnable damage cap must be below the current maximum")
    # Merge scalar targets and re-validate the complete state
    merged = dict(current)
    merged.update(updates)
    validator = {"economy": _economy, "weather": _weather,
                 "spawnable_damage": _spawnable, "starter_loadout": _starter}[target]
    return validator(merged, require_complete=True)


def validate_current_state(target: str, current: dict[str, Any]) -> dict[str, Any]:
    """Return validated persisted state for one target area."""
    validator = {"economy": _economy, "weather": _weather,
                 "spawnable_damage": _spawnable, "starter_loadout": _starter,
                 "events": _events}.get(target)
    if validator is None:
        raise MissionValidationError("mission target is not supported")
    return validator(current, require_complete=True)


def _economy(value: dict[str, Any], *, require_complete: bool = False) -> dict[str, Any]:
    """Return validated economy globals with type-correct values."""
    # Partial updates may omit fields; complete states must not
    if not set(value) <= GLOBAL_KEYS or (require_complete and set(value) != GLOBAL_KEYS):
        raise MissionValidationError("economy state has missing or unknown fields")
    result: dict[str, Any] = {}
    # Validate each global through its field class
    for key, raw in value.items():
        if key in LOOT_DAMAGE_KEYS:
            result[key] = _number(raw, key)
        elif key in GLOBAL_BOOLEAN_KEYS:
            if not isinstance(raw, bool):
                raise MissionValidationError(f"{key} must be true or false")
            result[key] = raw
        else:
            result[key] = _integer(raw, key)
    # Keep the damage range ordered
    if result.get("LootDamageMin", 0) > result.get("LootDamageMax", 1):
        raise MissionValidationError("LootDamageMin must not exceed LootDamageMax")
    return result


def _weather(value: dict[str, Any], *, require_complete: bool = False) -> dict[str, Any]:
    """Return validated weather overrides with bounded timings."""
    allowed = {"rain_disabled", "rain_time_min", "rain_time_max", "rain_fade"}
    if not set(value) <= allowed or (require_complete and set(value) != allowed):
        raise MissionValidationError("weather state has missing or unknown fields")
    result = dict(value)
    if "rain_disabled" in result and not isinstance(result["rain_disabled"], bool):
        raise MissionValidationError("rain_disabled must be true or false")
    # Timing fields are non-negative integers
    for key in allowed - {"rain_disabled"}:
        if key in result:
            result[key] = _integer(result[key], key)
    # Keep the rain window ordered
    if result.get("rain_time_min", 0) > result.get("rain_time_max", 2_147_483_647):
        raise MissionValidationError("rain_time_min must not exceed rain_time_max")
    return result


def _spawnable(value: dict[str, Any], *, require_complete: bool = False) -> dict[str, Any]:
    """Return the validated spawnable damage cap."""
    if set(value) != {"maximum"}:
        raise MissionValidationError("spawnable damage requires maximum")
    return {"maximum": _number(value["maximum"], "maximum")}


def _starter(value: dict[str, Any], *, require_complete: bool = False) -> dict[str, Any]:
    """Return validated starter loadout item identifiers."""
    if set(value) != {"items"} or not isinstance(value["items"], list):
        raise MissionValidationError("starter loadout requires an items array")
    items: list[str] = []
    # Identifiers must be unique ASCII item names
    for raw in value["items"]:
        if not isinstance(raw, str) or ITEM_ID.fullmatch(raw) is None:
            raise MissionValidationError("starter item identifiers must use ASCII letters, digits, or underscore")
        if raw in items:
            raise MissionValidationError("starter items must not repeat")
        items.append(raw)
    return {"items": items}


def _events(value: dict[str, Any], *, require_complete: bool = False) -> dict[str, Any]:
    """Return validated event and population field values."""
    if set(value) != {"events"} or not isinstance(value["events"], dict) or not value["events"]:
        raise MissionValidationError("events updates require a non-empty events object")
    result: dict[str, dict[str, int]] = {}
    # Only event and population names are manageable
    for name, fields in value["events"].items():
        if name not in EVENT_NAMES | POPULATION_NAMES or not isinstance(fields, dict) or not fields:
            raise MissionValidationError("event name or values are not managed")
        # Field sets depend on whether the name is an event or population
        allowed = EVENT_FIELDS if name in EVENT_NAMES else POPULATION_FIELDS
        if not set(fields) <= allowed or (require_complete and set(fields) != allowed):
            raise MissionValidationError(f"event {name} has missing or unknown fields")
        parsed = {key: _integer(raw, f"{name}.{key}") for key, raw in fields.items()}
        # active is a zero-or-one flag
        if parsed.get("active") not in (None, 0, 1):
            raise MissionValidationError(f"event {name}.active must be zero or one")
        # Keep population bounds ordered
        if name in POPULATION_NAMES and parsed.get("min", 0) > parsed.get("max", 2_147_483_647):
            raise MissionValidationError(f"event {name}.min must not exceed max")
        result[name] = parsed
    return {"events": result}


def _integer(value: object, name: str) -> int:
    """Return a non-negative 32-bit integer or raise."""
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 2_147_483_647:
        raise MissionValidationError(f"{name} must be a non-negative integer")
    return value


def _number(value: object, name: str) -> float:
    """Return a finite ratio from 0.0 through 1.0 or raise."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MissionValidationError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise MissionValidationError(f"{name} must be from 0.0 through 1.0")
    return result
