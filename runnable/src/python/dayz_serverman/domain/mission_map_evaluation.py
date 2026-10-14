"""Shared D9 location evaluation and cheap per-object availability summaries."""

from __future__ import annotations

from typing import Any

from .mission_map_evaluation_shapes import LootShape, loot_shape
from .mission_map_geometry import Bounds, circle_covers
from .mission_map_terrain import TerrainCapabilities, ValueRows, tiers_of
from .mission_map_values import ABUNDANCE_PERCENT


def plan_bounds(plan: dict[str, Any]) -> Bounds:
    """Return the native rectangle of a validated D1 plan."""
    return Bounds(**plan["terrain"]["bounds"])


def shape_reference(shape: LootShape) -> dict[str, Any]:
    """Return the presentation identity of an effective contributor."""
    return {"id": shape.identifier, "name": shape.name, "kind": shape.kind, "hidden": shape.hidden}


def tier_problem(shape: LootShape, capabilities: TerrainCapabilities, writable: bool,
                 reason: str | None) -> str | None:
    """Report an unavailable explicit tier while allowing abundance-only zones."""
    if shape.tier is None:
        return None
    if not writable:
        return reason
    if shape.tier.casefold() not in {name.casefold() for name in capabilities.holdable_tiers}:
        return "tier_not_holdable"
    return None


def covering_encounters(plan: dict[str, Any], x: float, z: float) -> list[dict[str, Any]]:
    """Return each encounter whose closed spawn circle covers the point, in plan order (R14).

    Hidden, excluded and unavailable encounters stay listed, because they stay
    in the plan (R11, R12). A provider without a spawn radius covers no point.
    """
    result = []
    for item in plan["objects"]:
        if item["kind"] != "encounter" or item["radius"] is None:
            continue
        if circle_covers(item["centre"]["x"], item["centre"]["z"], item["radius"], x, z):
            result.append({"id": item["id"], "name": item["name"], "provider": item["provider"],
                           "hidden": not item["visible"], "excluded": item["excluded"]})
    return result


def evaluate_plan(plan: dict[str, Any], capabilities: TerrainCapabilities,
                  revision: int | None = None) -> dict[str, Any]:
    """Summarize validated loot properties without sampling object tier sets.

    Encounter spawning support belongs to its provider adapter. This summary
    evaluates linked loot independently of spawning availability.
    """
    writable, reason = capabilities.tier_writes(plan_bounds(plan), plan["terrain"]["bounds_confirmed"])
    objects = []
    for item in plan["objects"]:
        shape = loot_shape(item)
        # A disabled linked area contributes neither tier nor abundance.
        if shape is None:
            objects.append({"id": item["id"], "available": True, "reason": None,
                            "zone_adjustment": 0, "effective_adjustment": plan["global_abundance"],
                            "tier": None, "tier_writable": writable, "abundance_only": False})
            continue
        problem = tier_problem(shape, capabilities, writable, reason)
        adjustment = ABUNDANCE_PERCENT[shape.abundance]
        objects.append({"id": item["id"], "available": problem is None, "reason": problem,
                        "zone_adjustment": adjustment, "effective_adjustment": plan["global_abundance"] + adjustment,
                        "tier": shape.tier, "tier_writable": writable and problem is None,
                        "abundance_only": shape.tier is None})
    return {"revision": revision, "global": plan["global_abundance"], "objects": objects}


def evaluate_location(plan: dict[str, Any], capabilities: TerrainCapabilities,
                      values: ValueRows | None, x: float, z: float, *,
                      sample_cell: bool = True) -> dict[str, Any]:
    """Evaluate an Inspect click at its D9 centre, or a building at its own point.

    Values are an immutable snapshot. A missing header or layer leaves original
    tiers unknown. Bounds mismatch retains header tiers and evaluates abundance
    at the clicked point. Covering encounters use the same point as the loot
    zones. No capacity is invented without building data.
    """
    bounds = plan_bounds(plan)
    writable, _ = capabilities.tier_writes(bounds, plan["terrain"]["bounds_confirmed"])
    header = capabilities.header
    original, header_cell, cell = None, None, None
    test_x, test_z = x, z
    # Header cells remain readable when the draft bounds prevent tier writes.
    if header is not None:
        grid = header.cell_grid()
        i, j = grid.cell_at(x, z)
        if grid.bounds.contains(x, z):
            header_cell = {"i": i, "j": j}
            if values is not None:
                original = list(tiers_of(values.value(i, j), capabilities.flags))
        if writable and sample_cell:
            test_x, test_z = grid.centre(i, j)
            cell = {"i": i, "j": j, "x": test_x, "z": test_z}
    # Visibility and lock state never remove a loot contributor.
    shapes = [shape for item in plan["objects"] if (shape := loot_shape(item)) is not None]
    covering = [shape for shape in shapes if shape.covers(test_x, test_z)]
    winner = covering[0] if covering else None
    explicit = next((shape for shape in covering if shape.tier is not None), None)
    tier_object = explicit if writable else None
    effective = original
    if tier_object is not None:
        held = next((name for name in capabilities.holdable_tiers
                     if name.casefold() == tier_object.tier.casefold()), None)
        if held is not None:
            effective = [held]
        else:
            tier_object = None
    # Only the independent abundance winner and tier winner are contributors.
    contributors = {shape.identifier: shape for shape in (winner, tier_object) if shape is not None}
    zone = ABUNDANCE_PERCENT[winner.abundance] if winner is not None else 0
    not_writable = None
    if explicit is not None and not writable:
        not_writable = {"id": explicit.identifier, "name": explicit.name, "tier": explicit.tier}
    return {"x": x, "z": z, "grid": {"h": (x - bounds.x_min) / bounds.width * 10,
                                         "v": (z - bounds.z_min) / bounds.height * 10},
            "header_cell": header_cell, "cell": cell, "original_tier": original, "effective_tier": effective,
            "tier_source": "zone" if tier_object else "original" if original is not None else "unknown",
            "tier_object": shape_reference(tier_object) if tier_object else None, "not_writable": not_writable,
            "winner": shape_reference(winner) if winner else None,
            "abundance": {"global": plan["global_abundance"], "zone": zone,
                          "zone_preset": winner.abundance if winner else None,
                          "effective": plan["global_abundance"] + zone},
            "hidden_contributors": [shape_reference(shape) for shape in contributors.values() if shape.hidden],
            "abundance_only": winner is not None and winner.tier is None,
            "encounters": covering_encounters(plan, test_x, test_z), "capacity": None}
