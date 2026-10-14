"""Tier candidate of areaflags.map: the value bits of covered cells for enabled tier overrides (6.6, D9).

The builder is pure. It starts from the working baseline bytes and returns new bytes and their digests.
Publication of the candidate belongs to the apply preview and Apply (8.6, 8.7).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Sequence

from .mission_map_evaluation import plan_bounds
from .mission_map_evaluation_shapes import LootShape, loot_shape, split_spans
from .mission_map_terrain import (
    FIXED_HEADER, TIER_NOT_HOLDABLE, VALUE_BITS_FIELD, AreaFlagsHeader, TerrainCapabilities, TerrainDataError,
    build_header, parse_fixed_header, tier_mask, value_bits_position, value_flags,
)


@dataclass(frozen=True)
class AreaflagsCandidate:
    """Candidate bytes of areaflags.map with the D3 file digest and owned-value digest."""

    content: bytes
    sha256: str
    # D3: the owned projection of areaflags.map is the whole file, so this digest equals sha256
    owned_value_sha256: str


def owned_value_digest(content: bytes) -> str:
    """Return the D3 owned-value digest of areaflags.map: SHA-256 of the whole exact file."""
    return hashlib.sha256(content).hexdigest()


def parse_areaflags(content: bytes) -> AreaFlagsHeader:
    """Return the header of exact areaflags.map bytes through the 3.5 parser, or raise tier_header_invalid."""
    fields = parse_fixed_header(content[:FIXED_HEADER.size])
    position = value_bits_position(fields[0], fields[1], fields[4])
    return build_header(fields, content[position:position + VALUE_BITS_FIELD.size], len(content))


def build_areaflags_candidate(baseline: bytes, plan: dict[str, Any], flags: Sequence[str]) -> AreaflagsCandidate:
    """Return the tier candidate of a validated D1 plan over the working baseline bytes.

    Each covered cell gets the complete value mask of the highest enabled explicit tier that covers its centre,
    as the 4.1 evaluation reads it. Other cells, the header, the usage layer and the value-bit count keep their
    baseline bytes. A plan without enabled tier overrides returns the baseline unchanged: capacity-only objects
    add no tier write (D9). Otherwise TerrainDataError names the closed reason of a refusal. The cases are an
    unsupported file, plan bounds without tier writes, invalid value flags and a tier that the layer cannot hold.
    """
    shapes = [shape for item in plan["objects"] if (shape := loot_shape(item)) is not None and shape.tier is not None]
    if not shapes:
        return _candidate(bytes(baseline))
    header = parse_areaflags(baseline)
    capabilities = TerrainCapabilities(None, header, value_flags(flags))
    writable, reason = capabilities.tier_writes(plan_bounds(plan), plan["terrain"]["bounds_confirmed"])
    if not writable:
        raise TerrainDataError(reason, "tier writes are not available for this mission and plan bounds")
    overrides = [(shape, _held_mask(capabilities, shape)) for shape in shapes]
    content = bytearray(baseline)
    grid = header.cell_grid()
    for row in range(header.rows):
        remaining = [(0, header.columns)]
        # Plan order is priority order: a higher override claims its cells before lower ones (R06)
        for shape, mask in overrides:
            remaining, claimed = split_spans(remaining, shape.spans(grid, row))
            for start, end in claimed:
                _fill(content, header, row * header.columns + start, row * header.columns + end, mask)
            if not remaining:
                break
    return _candidate(bytes(content))


def _held_mask(capabilities: TerrainCapabilities, shape: LootShape) -> int:
    """Return the value mask of an explicit tier, or refuse the complete candidate for a tier the layer cannot hold."""
    try:
        return tier_mask(capabilities.holdable_tiers, shape.tier)
    except KeyError:
        raise TerrainDataError(TIER_NOT_HOLDABLE,
                               f"the value layer cannot hold the tier {shape.tier} of object {shape.identifier}") from None


def _fill(content: bytearray, header: AreaFlagsHeader, first: int, end: int, mask: int) -> None:
    """Set cells first to end − 1 (index j·C + i) to the complete mask, never OR with the baseline bits.

    In a 4-bit layer, an even cell index is the low nibble and an odd cell index is the high nibble (A69). The partner nibble of a partly covered byte keeps its baseline bits.
    """
    offset = header.value_offset
    if header.value_bits == 8:
        content[offset + first:offset + end] = bytes([mask]) * (end - first)
        return
    if first % 2:
        position = offset + first // 2
        content[position] = content[position] & 0x0F | mask << 4
        first += 1
    if end % 2 and first < end:
        position = offset + (end - 1) // 2
        content[position] = content[position] & 0xF0 | mask
        end -= 1
    if first < end:
        content[offset + first // 2:offset + end // 2] = bytes([mask | mask << 4]) * ((end - first) // 2)


def _candidate(content: bytes) -> AreaflagsCandidate:
    """Return the candidate with its file digest, which is also the owned-value digest (D3)."""
    digest = owned_value_digest(content)
    return AreaflagsCandidate(content, digest, digest)
