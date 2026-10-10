"""Cover the terrain capability domain: header and A7 size formula, value flags, families and D9 value cells."""

from __future__ import annotations

import struct
import unittest

from dayz_serverman.domain.mission_map_geometry import Bounds
from dayz_serverman.domain.mission_map_terrain import (
    AREAFLAGS_MISSING, BOUNDS_DIFFER_FROM_HEADER, BOUNDS_NOT_CONFIRMED, LIMITS_INVALID, TERRAIN_FAMILY_UNKNOWN,
    TIER_HEADER_INVALID, TIER_NOT_HOLDABLE, TerrainCapabilities, TerrainDataError, ValueRows, build_header,
    parse_fixed_header, row_byte_span, terrain_family, tier_mask, tiers_of, value_bits_position, value_flags,
)


# Value flags of the installed Pripyat and Chernarus missions (evidence 1.3)
FIVE_FLAGS = ("Tier1", "Tier2", "Tier3", "Tier4", "Unique")


def areaflags_bytes(columns: int, rows: int, width: int, height: int, usage_bits: int, value_bits: int,
                    cells: dict[tuple[int, int], int] | None = None, usage_fill: int = 0xA5) -> bytes:
    """Build a synthetic areaflags.map: header, usage layer, value-bit count and a row-major value layer.

    Cell (i, j) is at index j·C + i, row j = 0 first; a 4-bit layer puts the first cell in the low nibble.
    """
    usage = bytes([usage_fill]) * (columns * rows * usage_bits // 8)
    layer = bytearray(-(-columns * rows * value_bits // 8))
    for (column, row), value in (cells or {}).items():
        index = row * columns + column
        if value_bits == 8:
            layer[index] = value
        else:
            layer[index // 2] |= value << 4 * (index % 2)
    return struct.pack("<5I", columns, rows, width, height, usage_bits) + usage + struct.pack("<I", value_bits) + layer


def header_of(data: bytes):
    """Parse a synthetic file through the domain functions, as the repository does."""
    fields = parse_fixed_header(data[:20])
    position = value_bits_position(fields[0], fields[1], fields[4])
    return build_header(fields, data[position:position + 4], len(data))


class HeaderTests(unittest.TestCase):
    """Verify the header fields, the supported widths and the A7 size formula."""

    def test_four_and_eight_value_bits_pass_the_size_formula(self) -> None:
        """24 + C·R·(usage + value bits) / 8 holds for both installed widths."""
        for usage_bits, value_bits in ((16, 4), (32, 8), (32, 4)):
            with self.subTest(value_bits=value_bits, usage_bits=usage_bits):
                data = areaflags_bytes(8, 4, 40, 20, usage_bits, value_bits)
                header = header_of(data)
                self.assertEqual(header.expected_size, len(data))
                self.assertEqual(header.expected_size, 24 + 8 * 4 * (usage_bits + value_bits) // 8)
                self.assertEqual(header.to_dict(), {"columns": 8, "rows": 4, "world_width": 40, "world_height": 20,
                                                    "value_bits": value_bits})

    def test_size_mismatch_is_an_invalid_header(self) -> None:
        """One byte more or less than the formula is refused with tier_header_invalid."""
        data = areaflags_bytes(8, 4, 40, 20, 16, 4)
        for changed in (data + b"\0", data[:-1]):
            with self.assertRaises(TerrainDataError) as caught:
                header_of(changed)
            self.assertEqual(caught.exception.reason, TIER_HEADER_INVALID)

    def test_unsupported_and_damaged_headers_are_refused(self) -> None:
        """Other value-bit widths, empty grids, odd usage widths and short files give tier_header_invalid."""
        cases = {
            "two value bits": areaflags_bytes(8, 4, 40, 20, 16, 2),
            "sixteen value bits": areaflags_bytes(8, 4, 40, 20, 16, 16),
            "no columns": struct.pack("<5I", 0, 4, 40, 20, 16) + struct.pack("<I", 4),
            "no world": areaflags_bytes(8, 4, 0, 20, 16, 4),
            "usage not in bytes": struct.pack("<5I", 3, 1, 40, 20, 4) + b"\0\0" + struct.pack("<I", 4) + b"\0\0",
            "value layer not in bytes": areaflags_bytes(3, 1, 30, 10, 8, 4),
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(TerrainDataError) as caught:
                header_of(data)
            self.assertEqual(caught.exception.reason, TIER_HEADER_INVALID)
        with self.assertRaises(TerrainDataError):
            parse_fixed_header(b"\0" * 19)
        with self.assertRaises(TerrainDataError):
            build_header((8, 4, 40, 20, 16), b"\4\0", 64)

    def test_detected_bounds_and_grid_use_the_header_world_at_origin(self) -> None:
        """The detected bounds start at (0, 0) and the D9 grid divides them into C × R cells."""
        header = header_of(areaflags_bytes(8, 4, 40, 20, 16, 4))
        self.assertEqual(header.detected_bounds(), Bounds(0.0, 0.0, 40.0, 20.0))
        grid = header.cell_grid()
        self.assertEqual((grid.cell_width, grid.cell_height), (5.0, 5.0))
        self.assertEqual(grid.centre(0, 0), (2.5, 2.5))


class FlagAndFamilyTests(unittest.TestCase):
    """Verify value-flag order, holdable tiers and the terrain family."""

    def test_value_bit_n_is_the_nth_flag(self) -> None:
        """Bit N − 1 maps to the N-th value flag, and a cell value lists its flags in that order."""
        flags = value_flags(list(FIVE_FLAGS))
        self.assertEqual(tier_mask(flags, "Tier1"), 1)
        self.assertEqual(tier_mask(flags, "unique"), 16)
        self.assertEqual(tiers_of(0b10110, flags), ("Tier2", "Tier3", "Unique"))
        self.assertRaises(KeyError, tier_mask, flags, "Tier9")

    def test_invalid_value_flags_are_refused(self) -> None:
        """No flags, a bad name, a missing name and a case-only duplicate give limits_invalid."""
        for names in ([], ["Tier 1"], [None], ["Tier1", "TIER1"]):
            with self.subTest(names=names), self.assertRaises(TerrainDataError) as caught:
                value_flags(names)
            self.assertEqual(caught.exception.reason, LIMITS_INVALID)

    def test_terrain_family_is_the_template_suffix(self) -> None:
        """The suffix after the last dot names the family; a name without one gives None."""
        self.assertEqual(terrain_family("dayzOffline.chernarusplus"), "chernarusplus")
        self.assertEqual(terrain_family("serverman-test20261001-2.chernarusplus"), "chernarusplus")
        self.assertEqual(terrain_family("plannerProbe.Pripyat"), "Pripyat")
        for name in ("chernarusplus", "dayzOffline.", ".enoch", "a.b c"):
            self.assertIsNone(terrain_family(name), name)


class ValueRowsTests(unittest.TestCase):
    """Verify D9 indexing of the value layer: row-major, row j = 0 first, low nibble first."""

    def rows(self, columns: int, rows: int, value_bits: int, cells: dict, first: int, count: int) -> ValueRows:
        """Return the rows of a synthetic layer cut by row_byte_span, as the repository reads them."""
        data = areaflags_bytes(columns, rows, columns * 5, rows * 5, 8, value_bits, cells)
        header = header_of(data)
        offset, length = row_byte_span(header, first, count)
        return ValueRows(header, first, count, data[offset:offset + length])

    def test_cells_of_both_widths_and_odd_nibble_edges(self) -> None:
        """Every cell reads back by value and by row, also rows that start in the middle of a byte."""
        for value_bits, columns in ((4, 5), (4, 4), (8, 5)):
            top = (1 << value_bits) - 1
            cells = {(i, j): (i * 7 + j * 3 + 1) % (top + 1) for i in range(columns) for j in range(4)}
            for first, count in ((0, 4), (1, 2), (3, 1)):
                with self.subTest(value_bits=value_bits, columns=columns, first=first):
                    part = self.rows(columns, 4, value_bits, cells, first, count)
                    for j in range(first, first + count):
                        expected = bytes(cells[(i, j)] for i in range(columns))
                        self.assertEqual(part.row(j), expected)
                        self.assertEqual([part.value(i, j) for i in range(columns)], list(expected))

    def test_reads_outside_the_rows_are_refused(self) -> None:
        """A row outside the read range, a column outside the grid and an empty range raise IndexError."""
        part = self.rows(4, 4, 4, {}, 1, 2)
        self.assertRaises(IndexError, part.row, 0)
        self.assertRaises(IndexError, part.row, 3)
        self.assertRaises(IndexError, part.value, 4, 1)
        self.assertRaises(IndexError, row_byte_span, part.header, 3, 2)
        self.assertRaises(IndexError, row_byte_span, part.header, 0, 0)


class CapabilityTests(unittest.TestCase):
    """Verify the capability snapshot fields, tier writes and the reason for each unsupported case."""

    def snapshot(self, value_bits: int, **changes) -> TerrainCapabilities:
        """Return a valid Pripyat-like snapshot with the given value-bit width."""
        header = header_of(areaflags_bytes(4, 4, 20480, 20480, 16, value_bits))
        values = {"terrain_id": "Pripyat", "header": header, "flags": FIVE_FLAGS} | changes
        return TerrainCapabilities(**values)

    def test_holdable_tiers_follow_the_value_bits(self) -> None:
        """Four value bits hold four flags and list Unique as not holdable; eight bits hold all five."""
        four = self.snapshot(4)
        self.assertEqual(four.holdable_tiers, FIVE_FLAGS[:4])
        self.assertEqual(four.unsupported(), [{"operation": "tier:Unique", "reason": TIER_NOT_HOLDABLE}])
        eight = self.snapshot(8)
        self.assertEqual(eight.holdable_tiers, FIVE_FLAGS)
        self.assertEqual(eight.unsupported(), [])

    def test_tier_writes_need_confirmed_bounds_equal_to_the_header(self) -> None:
        """Unconfirmed, shifted or resized bounds give their reason; equal confirmed bounds allow writes."""
        capabilities = self.snapshot(4)
        exact = Bounds(0.0, 0.0, 20480.0, 20480.0)
        self.assertEqual(capabilities.tier_writes(None, False), (False, BOUNDS_NOT_CONFIRMED))
        self.assertEqual(capabilities.tier_writes(exact, False), (False, BOUNDS_NOT_CONFIRMED))
        self.assertEqual(capabilities.tier_writes(Bounds(1.0, 0.0, 20480.0, 20480.0), True),
                         (False, BOUNDS_DIFFER_FROM_HEADER))
        self.assertEqual(capabilities.tier_writes(Bounds(0.0, 0.0, 20000.0, 20480.0), True),
                         (False, BOUNDS_DIFFER_FROM_HEADER))
        self.assertEqual(capabilities.tier_writes(exact, True), (True, None))

    def test_snapshot_fields_match_the_load_result(self) -> None:
        """to_dict gives exactly the capability fields of the implementation notes."""
        result = self.snapshot(4).to_dict(Bounds(0.0, 0.0, 20480.0, 20480.0), True)
        self.assertEqual(set(result), {"terrain_id", "header", "detected_bounds", "holdable_tiers", "tier_writes",
                                       "unsupported"})
        self.assertEqual(result["detected_bounds"], {"x_min": 0.0, "z_min": 0.0, "width": 20480.0, "height": 20480.0})
        self.assertEqual(result["holdable_tiers"], ["Tier1", "Tier2", "Tier3", "Tier4"])
        self.assertEqual(result["tier_writes"], {"available": True, "reason": None})

    def test_missing_header_and_unknown_family_give_reasons(self) -> None:
        """Without a header no bounds are detected and no tier is holdable; each operation names its reason."""
        capabilities = TerrainCapabilities(None, None, FIVE_FLAGS, header_reason=AREAFLAGS_MISSING)
        result = capabilities.to_dict()
        self.assertIsNone(result["header"])
        self.assertIsNone(result["detected_bounds"])
        self.assertEqual(result["holdable_tiers"], [])
        self.assertEqual(result["tier_writes"], {"available": False, "reason": AREAFLAGS_MISSING})
        self.assertEqual(result["unsupported"], [
            {"operation": "terrain_identity", "reason": TERRAIN_FAMILY_UNKNOWN},
            {"operation": "detect_bounds", "reason": AREAFLAGS_MISSING},
            {"operation": "tier_layer", "reason": AREAFLAGS_MISSING},
            {"operation": "tier_writes", "reason": AREAFLAGS_MISSING},
        ])

    def test_invalid_limits_keep_detected_bounds_but_stop_tiers(self) -> None:
        """A valid header with invalid value flags still detects bounds; the tier layer and writes are unsupported."""
        capabilities = self.snapshot(4, flags=(), limits_reason=LIMITS_INVALID)
        result = capabilities.to_dict(Bounds(0.0, 0.0, 20480.0, 20480.0), True)
        self.assertIsNotNone(result["detected_bounds"])
        self.assertEqual(result["holdable_tiers"], [])
        self.assertEqual(result["tier_writes"], {"available": False, "reason": LIMITS_INVALID})
        self.assertEqual([item["operation"] for item in result["unsupported"]], ["tier_layer", "tier_writes"])


if __name__ == "__main__":
    unittest.main()
