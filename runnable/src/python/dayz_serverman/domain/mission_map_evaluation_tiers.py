"""Incremental row-span sampling of distinct original and effective value flags."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from typing import Any

from .mission_map_evaluation import plan_bounds
from .mission_map_evaluation_shapes import loot_shape, split_spans
from .mission_map_terrain import TerrainCapabilities, ValueRows, tier_mask, tiers_of


class TierSampling:
    """Retain progress and distinct masks while visiting one snapshot in work units."""

    def __init__(self, plan: dict[str, Any], object_id: str, capabilities: TerrainCapabilities,
                 values: ValueRows, plan_source: dict[str, Any]) -> None:
        """Prepare the selected D1 object and enabled explicit overrides in priority order."""
        self.values = values
        self.capabilities = capabilities
        self.plan_source = deepcopy(plan_source)
        self.object_id = object_id
        self.rows_done = 0
        self.rows_total = values.header.rows
        self.original_mask = 0
        self.effective_mask = 0
        self.cell_count = 0
        self.writable, _ = capabilities.tier_writes(plan_bounds(plan), plan["terrain"]["bounds_confirmed"])
        item = next(item for item in plan["objects"] if item["id"] == object_id)
        self.target = loot_shape(item, include_disabled=True)
        # Disabled linked loot creates no area on a radius-less provider.
        if item["kind"] == "encounter" and item["radius"] is None and not item["linked_loot"]["enabled"]:
            self.target = None
        # An excluded encounter keeps enabled linked loot.
        self.overrides = []
        for item in plan["objects"]:
            shape = loot_shape(item)
            if self.writable and shape is not None and shape.tier is not None:
                try:
                    mask = tier_mask(capabilities.holdable_tiers, shape.tier)
                except KeyError:
                    continue
                self.overrides.append((shape, mask))

    def work(self) -> Iterator[None]:
        """Yield after each row preparation, override intersection and row completion."""
        grid = self.values.header.cell_grid()
        if self.target is None:
            self.rows_done = self.rows_total
            return
        target_box = self.target.extent()
        # Bounding boxes prune unrelated overrides without changing closed coverage.
        candidates = [(shape, mask) for shape, mask in self.overrides
                      if _boxes_meet(target_box, shape.extent())]
        upper_bound = layer_mask(self.values)
        for _, mask in candidates:
            upper_bound |= mask
        # A lower equal footprint can claim no cell after the higher explicit tier.
        candidates = distinct_footprints(candidates)
        for row in range(self.rows_total):
            spans = self.target.spans(grid, row)
            self.cell_count += sum(end - start for start, end in spans)
            row_values = self.values.row(row) if spans else b""
            self.original_mask |= span_mask(row_values, spans)
            yield
            # Higher explicit tiers claim cells before lower ones, even below a null-tier winner.
            remaining = spans
            active = candidates if self.effective_mask & upper_bound != upper_bound else ()
            for shape, mask in active:
                if not remaining:
                    break
                remaining, claimed = split_spans(remaining, shape.spans(grid, row))
                if claimed:
                    self.effective_mask |= mask
                yield
            if active or not candidates:
                self.effective_mask |= span_mask(row_values, remaining)
            self.rows_done = row + 1
            yield

    def result(self) -> dict[str, Any]:
        """Return completed tier sets in value-flag order; freshness is the caller's gate."""
        return {"object_id": self.object_id,
                "effective_tiers": list(tiers_of(self.effective_mask, self.capabilities.flags)),
                "original_tiers": list(tiers_of(self.original_mask, self.capabilities.flags)),
                "cell_count": self.cell_count, "cells": "d9" if self.writable else "header",
                "tier_writable": self.writable, "plan_source": self.plan_source}


def span_mask(row: bytes, spans: list[tuple[int, int]]) -> int:
    """Union set bits across exclusive-ended column spans without visiting duplicates."""
    mask = 0
    for start, end in spans:
        for value in set(row[start:end]):
            mask |= value
    return mask


def _boxes_meet(first: tuple[float, ...], second: tuple[float, ...]) -> bool:
    """Return whether two closed axis-aligned boxes intersect."""
    return first[0] <= second[2] and second[0] <= first[2] and first[1] <= second[3] and second[1] <= first[3]


def layer_mask(values: ValueRows) -> int:
    """Bound inherited bits by the union of every original cell in the immutable layer."""
    raw = 0
    for value in set(values.data):
        raw |= value
    return raw if values.header.value_bits == 8 else (raw & 15) | (raw >> 4)


def distinct_footprints(candidates: list) -> list:
    """Retain the highest explicit tier per exactly equal immutable geometry."""
    seen, result = set(), []
    for shape, mask in candidates:
        geometry = (shape.centre, shape.radius, shape.vertices)
        if geometry not in seen:
            seen.add(geometry)
            result.append((shape, mask))
    return result
