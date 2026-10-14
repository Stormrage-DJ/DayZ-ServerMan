"""Verify the 1-bit palette PNG of one original tier: chunks, transparency, north-up rows and the D9 cells."""

from __future__ import annotations

import struct
import unittest
import zlib

from dayz_serverman.domain.mission_map_png import PNG_SIGNATURE, tier_png
from dayz_serverman.domain.mission_map_terrain import ValueRows
from tests.test_mission_map_terrain import areaflags_bytes, header_of


def chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """Split a PNG into (type, data) pairs and check every CRC-32."""
    assert data.startswith(PNG_SIGNATURE)
    position, found = len(PNG_SIGNATURE), []
    while position < len(data):
        (length,) = struct.unpack(">I", data[position:position + 4])
        kind = data[position + 4:position + 8]
        body = data[position + 8:position + 8 + length]
        (crc,) = struct.unpack(">I", data[position + 8 + length:position + 12 + length])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF, kind
        found.append((kind, body))
        position += 12 + length
    return found


def pixels(data: bytes) -> list[list[int]]:
    """Decode the palette indexes of a 1-bit PNG with filter type 0 on every scan line."""
    parts = dict(chunks(data))
    width, height = struct.unpack(">II", parts[b"IHDR"][:8])
    raw = zlib.decompress(b"".join(body for kind, body in chunks(data) if kind == b"IDAT"))
    stride = (width + 7) // 8 + 1
    result = []
    for line in range(height):
        row = raw[line * stride:(line + 1) * stride]
        assert row[0] == 0
        result.append([row[1 + column // 8] >> (7 - column % 8) & 1 for column in range(width)])
    return result


def rows_of(columns: int, count: int, value_bits: int, cells: dict[tuple[int, int], int]) -> ValueRows:
    """Return the whole value layer of a synthetic areaflags.map through the domain reader."""
    data = areaflags_bytes(columns, count, 1000, 600, 8, value_bits, cells)
    header = header_of(data)
    return ValueRows(header, 0, count, data[header.value_offset:])


class TierPngTests(unittest.TestCase):
    """The PNG bytes are the cell binding of A69 with north up."""

    def test_chunks_palette_and_transparency(self) -> None:
        """IHDR is 1-bit palette; index 0 is transparent black and index 1 opaque white."""
        data = tier_png(rows_of(3, 2, 8, {}), 1)
        kinds = [kind for kind, _ in chunks(data)]
        self.assertEqual(kinds, [b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"])
        parts = dict(chunks(data))
        self.assertEqual(parts[b"IHDR"], struct.pack(">IIBBBBB", 3, 2, 1, 3, 0, 0, 0))
        self.assertEqual(parts[b"PLTE"], b"\x00\x00\x00\xff\xff\xff")
        self.assertEqual(parts[b"tRNS"], b"\x00")
        self.assertEqual(pixels(data), [[0, 0, 0], [0, 0, 0]])

    def test_rows_are_north_up_and_columns_follow_i(self) -> None:
        """PNG row r is cell row j = R − 1 − r; row j = 0 lies at the lowest Z, so it is the last PNG row."""
        rows = rows_of(4, 3, 8, {(0, 0): 0b01, (3, 0): 0b11, (1, 2): 0b10, (2, 1): 0b01})
        self.assertEqual(pixels(tier_png(rows, 0b01)), [[0, 0, 0, 0], [0, 0, 1, 0], [1, 0, 0, 1]])
        self.assertEqual(pixels(tier_png(rows, 0b10)), [[0, 1, 0, 0], [0, 0, 0, 0], [0, 0, 0, 1]])

    def test_four_bit_odd_columns_and_padding_bits(self) -> None:
        """A 4-bit layer with an odd column count keeps the low-nibble-first binding across rows."""
        cells = {(column, row): (column + row) % 16 for column in range(9) for row in range(6)}
        rows = rows_of(9, 6, 4, cells)
        for bit in range(4):
            mask = 1 << bit
            expected = [[1 if cells[(column, 5 - line)] & mask else 0 for column in range(9)] for line in range(6)]
            self.assertEqual(pixels(tier_png(rows, mask)), expected, bit)

    def test_every_byte_value_maps_by_its_mask_bit(self) -> None:
        """All 256 cell values of an 8-bit layer, across scan-line byte boundaries."""
        cells = {(value % 16, value // 16): value for value in range(256)}
        rows = rows_of(16, 16, 8, cells)
        for bit in range(8):
            decoded = pixels(tier_png(rows, 1 << bit))
            for (column, row), value in cells.items():
                self.assertEqual(decoded[15 - row][column], value >> bit & 1)

    def test_partial_rows_are_refused(self) -> None:
        """A tier image always covers the whole value layer."""
        data = areaflags_bytes(2, 3, 10, 10, 8, 8)
        header = header_of(data)
        with self.assertRaises(ValueError):
            tier_png(ValueRows(header, 1, 2, data[header.value_offset + 2:]), 1)


if __name__ == "__main__":
    unittest.main()
