"""Terrain capabilities of one mission: the areaflags.map header, the A7 size formula, value flags and D9 cells."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Sequence

from .mission_map_geometry import Bounds, CellGrid, tier_writes_possible
from .mission_map_values import MAX_TERRAIN_EXTENT, TERRAIN_ID, TIER_NAME


# Five little-endian uint32 values: columns, rows, world width and height in metres, usage bits (evidence 1.3)
FIXED_HEADER = struct.Struct("<5I")
# One little-endian uint32 value-bit count between the usage layer and the value layer
VALUE_BITS_FIELD = struct.Struct("<I")
# Value-bit widths that the installed missions use and that the cell reader decodes
SUPPORTED_VALUE_BITS = frozenset((4, 8))
# Widest usage layer cell; the editor never writes the usage layer, it only skips it
MAX_USAGE_BITS = 32

# Closed reason codes of the capability snapshot
TERRAIN_FAMILY_UNKNOWN = "terrain_family_unknown"
AREAFLAGS_MISSING = "areaflags_missing"
MISSION_FILE_UNREADABLE = "mission_file_unreadable"
TIER_HEADER_INVALID = "tier_header_invalid"
LIMITS_MISSING = "limits_missing"
LIMITS_INVALID = "limits_invalid"
BOUNDS_NOT_CONFIRMED = "bounds_not_confirmed"
BOUNDS_DIFFER_FROM_HEADER = "bounds_differ_from_header"
TIER_NOT_HOLDABLE = "tier_not_holdable"

# Operations that the unsupported list names; a value flag that the layer cannot hold is "tier:<name>"
OPERATION_TERRAIN_IDENTITY = "terrain_identity"
OPERATION_DETECT_BOUNDS = "detect_bounds"
OPERATION_TIER_LAYER = "tier_layer"
OPERATION_TIER_WRITES = "tier_writes"
TIER_OPERATION_PREFIX = "tier:"

# Byte tables that split one 4-bit layer byte into its two cells; the low nibble is the first cell
_LOW_NIBBLE = bytes(value & 0x0F for value in range(256))
_HIGH_NIBBLE = bytes(value >> 4 for value in range(256))


class TerrainDataError(ValueError):
    """Raised when a mission terrain file is missing, unreadable or not valid; it carries a reason code."""

    def __init__(self, reason: str, message: str) -> None:
        """Store the closed reason code and use the message as the error text."""
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class AreaFlagsHeader:
    """Header of one areaflags.map whose file size matches the A7 formula."""

    columns: int
    rows: int
    world_width: int
    world_height: int
    usage_bits: int
    value_bits: int

    @property
    def value_offset(self) -> int:
        """Return the file offset of the first value-layer byte."""
        return value_bits_position(self.columns, self.rows, self.usage_bits) + VALUE_BITS_FIELD.size

    @property
    def expected_size(self) -> int:
        """Return the A7 file size: 24 + C·R·(usage bits + value bits) / 8."""
        return self.value_offset + self.columns * self.rows * self.value_bits // 8

    def detected_bounds(self) -> Bounds:
        """Return the header world at origin (0, 0); the admin must confirm it (R02, A7)."""
        return Bounds(0.0, 0.0, float(self.world_width), float(self.world_height))

    def cell_grid(self) -> CellGrid:
        """Return the D9 cell grid over the detected bounds."""
        return CellGrid(self.detected_bounds(), self.columns, self.rows)

    def to_dict(self) -> dict[str, int]:
        """Return the header fields of the capability snapshot."""
        return {"columns": self.columns, "rows": self.rows, "world_width": self.world_width,
                "world_height": self.world_height, "value_bits": self.value_bits}


def value_bits_position(columns: int, rows: int, usage_bits: int) -> int:
    """Return the file offset of the value-bit count, directly after the usage layer."""
    return FIXED_HEADER.size + columns * rows * usage_bits // 8


def parse_fixed_header(head: bytes) -> tuple[int, int, int, int, int]:
    """Return columns, rows, world width, world height and usage bits, or raise TerrainDataError."""
    if len(head) < FIXED_HEADER.size:
        raise TerrainDataError(TIER_HEADER_INVALID, "areaflags.map is shorter than its header")
    columns, rows, width, height, usage_bits = FIXED_HEADER.unpack_from(head)
    if columns < 1 or rows < 1:
        raise TerrainDataError(TIER_HEADER_INVALID, "areaflags.map has an empty cell grid")
    if not 1 <= width <= MAX_TERRAIN_EXTENT or not 1 <= height <= MAX_TERRAIN_EXTENT:
        raise TerrainDataError(TIER_HEADER_INVALID, "areaflags.map has a world size outside the allowed range")
    # The value-bit count must start on a byte, so the usage layer fills whole bytes
    if not 1 <= usage_bits <= MAX_USAGE_BITS or columns * rows * usage_bits % 8:
        raise TerrainDataError(TIER_HEADER_INVALID, "areaflags.map has an unsupported usage-bit width")
    return columns, rows, width, height, usage_bits


def build_header(fields: tuple[int, int, int, int, int], value_bits_field: bytes, file_size: int) -> AreaFlagsHeader:
    """Return the header when the value-bit width is supported and the size formula holds (A7)."""
    if len(value_bits_field) < VALUE_BITS_FIELD.size:
        raise TerrainDataError(TIER_HEADER_INVALID, "areaflags.map ends before its value-bit count")
    (value_bits,) = VALUE_BITS_FIELD.unpack_from(value_bits_field)
    if value_bits not in SUPPORTED_VALUE_BITS:
        raise TerrainDataError(TIER_HEADER_INVALID, f"areaflags.map has {value_bits} value bits; 4 or 8 are supported")
    header = AreaFlagsHeader(*fields, value_bits)
    # The value layer must also fill whole bytes, else no size can match
    if header.columns * header.rows * value_bits % 8 or header.expected_size != file_size:
        raise TerrainDataError(TIER_HEADER_INVALID, "areaflags.map size does not match its header (A7 formula)")
    return header


def terrain_family(mission_folder: str) -> str | None:
    """Return the terrain family: the template suffix after the last dot, or None without a usable suffix."""
    prefix, dot, suffix = mission_folder.rpartition(".")
    if not dot or not prefix or TERRAIN_ID.fullmatch(suffix) is None:
        return None
    return suffix


def value_flags(names: Sequence[str]) -> tuple[str, ...]:
    """Return the cfglimitsdefinition.xml value-flag names in file order; value bit N is the N-th name (A7)."""
    if not names:
        raise TerrainDataError(LIMITS_INVALID, "cfglimitsdefinition.xml has no value flags")
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str) or TIER_NAME.fullmatch(name) is None:
            raise TerrainDataError(LIMITS_INVALID, "cfglimitsdefinition.xml has a value flag with an invalid name")
        # Tier names in a plan compare without regard to case, so two spellings of one name are ambiguous
        if name.casefold() in seen:
            raise TerrainDataError(LIMITS_INVALID, f"cfglimitsdefinition.xml names the value flag {name} twice")
        seen.add(name.casefold())
    return tuple(names)


def tier_mask(flags: Sequence[str], tier: str) -> int:
    """Return the value-layer bit of a tier name: bit N − 1 for the N-th value flag."""
    folded = [name.casefold() for name in flags]
    if tier.casefold() not in folded:
        raise KeyError(tier)
    return 1 << folded.index(tier.casefold())


def tiers_of(value: int, flags: Sequence[str]) -> tuple[str, ...]:
    """Return the value-flag names whose bit is set in one cell value, in value-flag order."""
    return tuple(name for index, name in enumerate(flags) if value >> index & 1)


@dataclass(frozen=True)
class ValueRows:
    """Cells of consecutive value-layer rows. The layer is row-major, row j = 0 first (lowest Z), as D9 indexes it."""

    header: AreaFlagsHeader
    first_row: int
    row_count: int
    # File bytes from row_byte_span; a 4-bit row of an odd column count starts in the middle of a byte
    data: bytes

    def row(self, row: int) -> bytes:
        """Return one row as one byte per cell, for columns i = 0 to C − 1."""
        start = self._cell_offset(0, row)
        columns = self.header.columns
        if self.header.value_bits == 8:
            return self.data[start:start + columns]
        # Split the covering bytes into nibbles, low nibble first, then cut the row out
        chunk = self.data[start // 2:(start + columns + 1) // 2]
        cells = bytearray(2 * len(chunk))
        cells[0::2] = chunk.translate(_LOW_NIBBLE)
        cells[1::2] = chunk.translate(_HIGH_NIBBLE)
        return bytes(cells[start % 2:start % 2 + columns])

    def value(self, column: int, row: int) -> int:
        """Return the value bits of cell (i, j)."""
        if not 0 <= column < self.header.columns:
            raise IndexError("the column is outside the cell grid")
        index = self._cell_offset(column, row)
        if self.header.value_bits == 8:
            return self.data[index]
        return self.data[index // 2] >> 4 * (index % 2) & 0x0F

    def _cell_offset(self, column: int, row: int) -> int:
        """Return the cell position inside data, counted in cells from the first byte."""
        if not self.first_row <= row < self.first_row + self.row_count:
            raise IndexError("the row is outside the rows that were read")
        first_cell = self.first_row * self.header.columns
        # A 4-bit span starts at an even cell, so a row may start one nibble into its first byte
        lead = first_cell % 2 if self.header.value_bits == 4 else 0
        return (row * self.header.columns + column) - first_cell + lead


def row_byte_span(header: AreaFlagsHeader, first_row: int, row_count: int) -> tuple[int, int]:
    """Return the file offset and byte count that hold rows first_row to first_row + row_count − 1."""
    if row_count < 1 or first_row < 0 or first_row + row_count > header.rows:
        raise IndexError("the row range is outside the cell grid")
    first_cell = first_row * header.columns
    end_cell = (first_row + row_count) * header.columns
    start = first_cell * header.value_bits // 8
    end = -(-end_cell * header.value_bits // 8)
    return header.value_offset + start, end - start


@dataclass(frozen=True)
class TerrainCapabilities:
    """Capability snapshot of one mission (A7, A11). Bounds come from the plan, so tier writes are a method."""

    terrain_id: str | None
    header: AreaFlagsHeader | None
    flags: tuple[str, ...]
    # Reason codes when the header or the value flags could not be read; None when they were read
    header_reason: str | None = None
    limits_reason: str | None = None

    @property
    def holdable_tiers(self) -> tuple[str, ...]:
        """Return the value flags that the value layer can hold: the first value-bits names (A8)."""
        if self.header is None or self.limits_reason is not None:
            return ()
        return self.flags[:self.header.value_bits]

    def tier_writes(self, bounds: Bounds | None, confirmed: bool) -> tuple[bool, str | None]:
        """Return whether tier writes are available for these plan bounds, and the reason when they are not (D9)."""
        mission_reason = self.header_reason or self.limits_reason
        if mission_reason is not None or self.header is None:
            return False, mission_reason or TIER_HEADER_INVALID
        if bounds is None or not confirmed:
            return False, BOUNDS_NOT_CONFIRMED
        if not tier_writes_possible(bounds, confirmed, self.header.world_width, self.header.world_height):
            return False, BOUNDS_DIFFER_FROM_HEADER
        return True, None

    def unsupported(self) -> list[dict[str, str]]:
        """Return each operation that this mission cannot support, with its reason; plan bounds are not included."""
        items: list[dict[str, str]] = []
        if self.terrain_id is None:
            items.append({"operation": OPERATION_TERRAIN_IDENTITY, "reason": TERRAIN_FAMILY_UNKNOWN})
        if self.header_reason is not None:
            items.extend({"operation": operation, "reason": self.header_reason}
                         for operation in (OPERATION_DETECT_BOUNDS, OPERATION_TIER_LAYER, OPERATION_TIER_WRITES))
        elif self.limits_reason is not None:
            # The tier layer is read by tier name, so it also needs the value flags
            items.extend({"operation": operation, "reason": self.limits_reason}
                         for operation in (OPERATION_TIER_LAYER, OPERATION_TIER_WRITES))
        elif self.header is not None:
            items.extend({"operation": TIER_OPERATION_PREFIX + name, "reason": TIER_NOT_HOLDABLE}
                         for name in self.flags[self.header.value_bits:])
        return items

    def to_dict(self, bounds: Bounds | None = None, confirmed: bool = False) -> dict[str, Any]:
        """Return the capabilities field of the load result for the plan bounds and their confirmation."""
        available, reason = self.tier_writes(bounds, confirmed)
        detected = self.header.detected_bounds() if self.header is not None else None
        return {
            "terrain_id": self.terrain_id,
            "header": self.header.to_dict() if self.header is not None else None,
            "detected_bounds": None if detected is None else {
                "x_min": detected.x_min, "z_min": detected.z_min, "width": detected.width, "height": detected.height},
            "holdable_tiers": list(self.holdable_tiers),
            "tier_writes": {"available": available, "reason": reason},
            "unsupported": self.unsupported(),
        }
