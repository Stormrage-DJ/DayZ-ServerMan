"""Product Owner and window texts of `map inspect` (leaf 10.4).

Texts marked "window" are exact copies of `map_editor_inspect_wording.js`; `tests/test_cli_map_wording.py`
compares them. The progress lines and the two refusals are CLI texts (Product Owner 2026-10-16 14:26:38).
"""

from __future__ import annotations

from typing import Any

from ..domain.mission_map_loot_report_texts import KEPT_TEXTS

# Labels of the point answer
POINT_LABEL, GRID_LABEL, CELL_LABEL, ORIGINAL_LABEL = "Point", "Grid", "Cell", "Original"
EFFECTIVE_LABEL, SET_BY_LABEL, CAPACITY_LABEL = "Effective tier", "Set by", "Building capacity"
# Labels of the object answer
OBJECT_LABEL, TIER_WRITABLE_LABEL, GROUPS_LABEL = "Object", "Tier writable", "Building groups"
CELLS_LABEL = "Cells: {count} ({basis})"
CAPACITY_CHANGE = "Capacity change: global {global_value}, {zone}, together {effective}."
# Product Owner 2026-10-16 15:28:30: the basis of the cell count in plain words, and the zone that cannot be written
CELL_BASIS = {"d9": "a cell counts when its centre is inside the object", "header": "counted on the grid of the tier map"}
NOT_WRITABLE_ZONE = "{name} sets {tier}, but tiers cannot be written in this mission. The effective tier is the original tier."
WRITES_REASON_LINE = "Run {command} for the reason."
ENCOUNTERS_HEADING = "Encounters that cover this point"
HIDDEN_ONE, HIDDEN_MANY = "1 hidden object also sets this point.", "{count} hidden objects also set this point."
ORIGINAL_TIER = "The original tier"
# window: none, unknown, cell, fromZone, fromOriginal, notWritableZone, winner, capacityOnly, adjustment, zone, noZone
NONE_TEXT, UNKNOWN_TEXT = "None", "Unknown"
OUTSIDE_CELL = "Outside the tier map"
CAPACITY_ONLY = "It changes only the capacity. Its tier is Inherit."
NO_ZONE = "no zone"
CAPACITY_NONE = "This zone covers no buildings."
# window: capacity reasons of a building group without capacity
CAPACITY_REASONS = {
    "source_missing": "Their group is not in mapgroupproto.xml, so their capacity is not known.",
    "source_ambiguous": "Their group name is in mapgroupproto.xml more than once, so their capacity is not known.",
    "dirt_placement_not_movable": "They are in mapgroupdirt.xml, so Apply cannot change their capacity.",
    "ceiling_unresolved": "Their capacity is not set in mapgroupproto.xml, so it cannot be changed.",
    "default_missing": "mapgroupproto.xml has no default capacity for them, so their capacity cannot be changed.",
    "default_duplicate": "mapgroupproto.xml has more than one default capacity for them, so their capacity cannot "
                         "be changed.",
    "ceiling_lexical": "A lootmax value of their group in mapgroupproto.xml is not a whole number, so their "
                       "capacity cannot be changed.",
    "container_ambiguous": "Their group has two containers with the same name, so their capacity cannot be changed.",
    "ceiling_unsupported": "This adjustment would raise their capacity above the supported limit. Choose a smaller "
                           "adjustment.",
    "file_invalid": "A mapgroup file of this mission is not valid, so no building capacity is shown.",
    "reserved_conflict": "A mapgroup file uses a group name that the map editor reserves, so no building capacity "
                         "is shown.",
    "placement_geometry": "A building placement has no usable position or rotation, so no building capacity is "
                          "shown.",
}
CAPACITY_REASON_FALLBACK = "The capacity of these buildings cannot be shown."
# window: the cause of "not writable" for a reason that is not a bounds reason.
# The inspect answer carries no reason, so an object that cannot take tier writes gets this text.
WRITES_CAUSE = "Tiers cannot be written in this mission. The effective tier is the original tier."
# Progress on stderr and the two refusals; {command} stands for the text to type
READING = "Reading tiers"
READING_PERCENT = "Reading tiers, {percent}%"
STALE_RESTART = "The mission files changed. Reading tiers again"
CHANGED_WHILE_READING = "The mission files changed while the tiers were read. Nothing was changed. Run {command} again."
# window: the failure of a read whose job was lost twice (T7), then the CLI line
READ_STOPPED = "The read stopped."


def signed(value: int | float) -> str:
    """Return a capacity change in percent, with a sign for a gain: "+50%", "0%", "-25%" (window: signed)."""
    return f"{'+' if value > 0 else ''}{value}%"


def tier_text(tiers: list[str] | None) -> str:
    """Return a tier set in value-flag order; an empty set has no tier (window: tiers)."""
    return ", ".join(tiers) if tiers else NONE_TEXT


def containers_text(containers: list[dict[str, Any]]) -> str:
    """Return the container ceilings in answer order; after four, the rest are counted (window: mapInspectContainers)."""
    shown = ", ".join(f"{item['name']} {item['baseline']}" if item["baseline"] == item["realized"]
                      else f"{item['name']} {item['baseline']} to {item['realized']}" for item in containers[:4])
    more = f", and {len(containers) - 4} more." if len(containers) > 4 else "."
    return f"Containers: {shown}{more}"


def group_capacity_text(group: dict[str, Any]) -> str:
    """Return the group capacity sentence (window: groupCapacity)."""
    return f"Group capacity: {group['baseline']} now, {group['realized']} with this plan."


def capacity_reason(entry: dict[str, Any]) -> str:
    """Return why an entry has no capacity; an ambiguous container is named when the answer names it."""
    if entry.get("reason") == "container_ambiguous" and entry.get("field"):
        return f"Their group has more than one container named {entry['field']}, so their capacity cannot be changed."
    return CAPACITY_REASONS.get(str(entry.get("reason")), CAPACITY_REASON_FALLBACK)


def capacity_entry_text(entry: dict[str, Any]) -> str:
    """Return the text of one building group of an object answer (window: capacityEntry, 6.9).

    The order is: head, group capacity, containers, point-limited containers, group bound, bound text,
    generated group.
    """
    count = entry["placements"]
    adjustment = "" if entry["adjustment"] is None else f" at {signed(entry['adjustment'])}"
    source = "Buildings" if entry["source"] is None else entry["source"]
    head = f"{source}: {count} {'building' if count == 1 else 'buildings'}{adjustment}."
    if entry.get("kept"):
        kept = KEPT_TEXTS["planner_content"] if entry["kept"] == "planner_content" else "The capacity stays unchanged."
        return f"{head} {kept}"
    capacity = entry.get("capacity")
    if not capacity:
        return f"{head} {capacity_reason(entry)}"
    group, containers = capacity["group"], capacity.get("containers") or []
    parts = [head, group_capacity_text(group)]
    if containers:
        parts.append(containers_text(containers))
    parts.extend(f"The spawn points of {item['name']} allow {item['point_bound']} at most."
                 for item in containers if item.get("point_limited"))
    if capacity.get("achievable") is not None and capacity["achievable"] < group["realized"]:
        parts.append(f"The spawn points allow {capacity['achievable']} at most.")
    if capacity.get("bound_text"):
        parts.append(capacity["bound_text"])
    if capacity.get("generated_group"):
        parts.append(f"Apply writes them to group {capacity['generated_group']}.")
    return " ".join(parts)
