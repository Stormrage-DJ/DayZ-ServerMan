"""Operator texts of the loot candidate report and of Inspect capacity (leaf 6.9, R08, R14, R16).

A report entry carries its closed reason code and one of these texts. An
Inspect answer carries only the code; the window (4.6) and `map inspect` (10.4)
show the text of this catalogue. No text promises a live item count or a
live-item percentage: capacity is a native upper limit, not item supply.
"""

from __future__ import annotations

# The exact explanation that R08 requires where users review capacity changes
CAPACITY_EXPLANATION = ("Percentages change native container capacity, not item supply. More capacity may leave the "
                        "live loot amount unchanged. Fixed dispatch loot is unchanged. Preview shows the supported "
                        "capacity changes and native limits.")

# Affected placements that stay unchanged for an accepted reason (Architect rulings of 6.8)
KEPT_TEXTS = {
    "planner_content": "This building uses a PlannerRich_ group of an earlier planner tool. The map editor does not "
                       "change planner content, so its capacity stays unchanged.",
    "cluster_placement": "This placement is in a mapgroupcluster file, which the map editor never writes. It keeps "
                         "the capacity of its group, which differs from the capacity of its zone.",
    "cluster_only_group": "Only mapgroupcluster files place this group, and the map editor never writes them. Its "
                          "capacity stays unchanged.",
}

# Mechanisms that capacity does not control, and limits of what capacity can do
MECHANISM_TEXTS = {
    "fixed_dispatch": "These groups also have fixed dispatch loot. Capacity does not change fixed dispatch loot.",
    "shared_category_capacity": "Several categories share one capacity. It is an upper limit for all of them "
                                "together, not a quota for each category.",
    "missing_supply": "No item type with a nominal above 0 has these categories. More capacity cannot add items "
                      "that have no supply.",
    "missing_supply_unknown": "The item types were not read, so categories without supply are not known.",
}

# A capacity value that is only an upper bound, and a container whose point count limits it
BOUND_TEXTS = {
    "qualified": "Upper bound: the point counts are not proved usable, so fewer items may fit.",
    "point_limited": "The container has fewer points than its capacity, so the points limit it.",
    "unknown": "The point count is not known, so no point bound is given.",
}

# Why a loot object is not available (D1 capability checks); the codes come from the 4.1 evaluation
UNAVAILABLE_TEXTS = {
    "tier_not_holdable": "The tier map of this mission cannot hold this tier. Choose another tier or Inherit.",
    "bounds_not_confirmed": "The terrain bounds are not confirmed, so tiers cannot be written.",
    "bounds_differ_from_header": "The terrain bounds differ from the tier map of this mission, so tiers cannot be "
                                 "written.",
    "tier_header_invalid": "The tier map of this mission is not valid, so tiers cannot be written.",
    "limits_invalid": "The value flags in cfglimitsdefinition.xml are not valid, so tiers cannot be written.",
    "limits_missing": "This mission has no cfglimitsdefinition.xml, so tiers cannot be written.",
    "areaflags_missing": "This mission has no areaflags.map, so tiers cannot be written.",
    "mission_file_unreadable": "A mission file could not be read.",
    "terrain_family_unknown": "The terrain family of this mission is not known.",
}

# Why an Inspect answer has no building capacity (Product Owner 2026-10-13 15:19:49)
CAPACITY_REASON_TEXTS = {
    "point_target": "Building capacity: inspect a zone to see the buildings it covers.",
}

# Fallback for a reason code without its own text; the code stays in the entry
UNKNOWN_REASON_TEXT = "This change cannot be made for a reason that the report does not describe."


def reason_text(table: dict[str, str], reason: str | None) -> str | None:
    """Return the text of a reason code, the fallback for an unknown code, or None without a reason."""
    if reason is None:
        return None
    return table.get(reason, UNKNOWN_REASON_TEXT)
