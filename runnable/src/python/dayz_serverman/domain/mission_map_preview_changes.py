"""Zones and encounters added, changed and removed since the last Apply (R16, 6.9 ruling of 2026-10-13 15:18:59).

The last-applied record of leaf 8.7 stores the global value and the loot and
encounter objects of the applied plan. It also stores the realization of the
generated groups. The preview compares the current plan with those objects
by object ID. A missing ID is removed, a new ID is added, and a different
configuration is changed. A record without these fields has no previous plan, so every current
object is listed as added.
"""

from __future__ import annotations

from typing import Any

from .mission_map_applied import GLOBAL_FIELD, GROUPS_FIELD, OBJECTS_FIELD
from .mission_map_plan import configuration_fingerprint

# Fields of applied-record version 2, which leaf 8.7 writes (domain/mission_map_applied.py).
# A version 1 record has none of them, so the preview reads them only when they are present.
APPLIED_OBJECTS_FIELD = OBJECTS_FIELD
APPLIED_GLOBAL_FIELD = GLOBAL_FIELD
APPLIED_GROUPS_FIELD = GROUPS_FIELD


def applied_objects(record: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """Return the plan objects that the last Apply stored, or None when the record has none."""
    if record is None:
        return None
    objects = record.get(APPLIED_OBJECTS_FIELD)
    return objects if isinstance(objects, list) else None


def applied_global(record: dict[str, Any] | None) -> int | None:
    """Return the global value that the last Apply stored, or None when the record has none."""
    value = None if record is None else record.get(APPLIED_GLOBAL_FIELD)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def applied_groups(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the generated groups of the current applied realization; none without the field."""
    groups = None if record is None else record.get(APPLIED_GROUPS_FIELD)
    return [item for item in groups if isinstance(item, dict)] if isinstance(groups, list) else []


def applied_group_names(record: dict[str, Any] | None) -> list[str]:
    """Return the names of the generated groups of the current applied realization; none without the field."""
    return sorted({item["name"] for item in applied_groups(record) if isinstance(item.get("name"), str)})


def object_changes(objects: list[dict[str, Any]], previous: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Return the objects added, changed and removed since the last Apply, in plan order.

    The comparison uses the configuration of each object, as the configuration
    fingerprint does. A change of name, visibility or lock alone changes no
    mission file, so it is no change here.
    """
    if previous is None:
        return {"previous": False, "added": [_entry(item) for item in objects], "changed": [], "removed": []}
    before = {item["id"]: item for item in previous if isinstance(item, dict) and isinstance(item.get("id"), str)}
    current = {item["id"] for item in objects}
    added, changed = [], []
    for item in objects:
        old = before.get(item["id"])
        if old is None:
            added.append(_entry(item))
        elif _configuration(old) != _configuration(item):
            changed.append(_entry(item))
    removed = [_entry(item) for identifier, item in before.items() if identifier not in current]
    return {"previous": True, "added": added, "changed": changed, "removed": removed}


def unavailable_encounters(objects: list[dict[str, Any]], providers: dict[str, Any]) -> list[dict[str, Any]]:
    """Return each encounter that its provider cannot write now, with the reason and the R11 choice state.

    An encounter is unavailable when its provider is not available, or when
    its native infected event is not offered. Its spawning is never written;
    its linked loot stays independent of this (R11). An encounter that the
    plan does not exclude needs a choice before Apply.
    """
    result = []
    for item in objects:
        if item["kind"] != "encounter":
            continue
        provider = providers.get(item["provider"]) or {}
        reason = None if provider.get("available") else provider.get("reason") or "provider_unavailable"
        event = item["properties"].get("event_name")
        if reason is None and item["provider"] == "native_infected" and event not in provider.get("event_names", ()):
            reason = "event_not_offered"
        if reason is not None:
            result.append(dict(_entry(item), reason=reason, excluded=item["excluded"],
                               choice_required=not item["excluded"],
                               linked_loot=bool(item["linked_loot"]["enabled"])))
    return result


def _configuration(item: dict[str, Any]) -> str:
    """Return the configuration digest of one object; its presentation fields and number forms do not count."""
    return configuration_fingerprint({"terrain": {"bounds": None}, "global_abundance": 0, "objects": [item]})


def _entry(item: dict[str, Any]) -> dict[str, Any]:
    """Return the identity of one listed object."""
    return {"id": item.get("id"), "name": item.get("name"), "kind": item.get("kind"),
            "provider": item.get("provider")}
