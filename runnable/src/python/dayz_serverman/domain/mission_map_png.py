"""Palette PNG of one original tier for the map display: bit depth 1, index 0 transparent, north up (3.15)."""

from __future__ import annotations

import struct
import zlib

from .mission_map_terrain import ValueRows


# Fixed eight-byte PNG file signature
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Palette: index 0 is black and fully transparent, index 1 is opaque white; the window tints index 1
PALETTE = b"\x00\x00\x00\xff\xff\xff"
TRANSPARENCY = b"\x00"
# Compression level of the measured 3.14 masks
ZLIB_LEVEL = 6


def png_chunk(kind: bytes, data: bytes) -> bytes:
    """Return one PNG chunk: length, type, data and the CRC-32 of type and data."""
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def tier_png(rows: ValueRows, mask: int | None) -> bytes:
    """Return cells matching a mask bit, or whole-cell zero when mask is None.

    The rows must hold the whole value layer. PNG row r is cell row j = R − 1 − r, so north is up,
    and PNG column is cell column i. Index 1 marks a cell of the tier.
    """
    header = rows.header
    columns, count = header.columns, header.rows
    if rows.first_row != 0 or rows.row_count != count:
        raise ValueError("the tier image needs every row of the value layer")
    selected_mask = 1 if header.value_bits == 16 and mask is not None else mask
    # One table for each bit position: a cell of the tier gives bit 7 − k of its byte, else 0
    tables = [bytes((0x80 >> position) if (value == 0 if selected_mask is None else value & selected_mask) else 0
                    for value in range(256)) for position in range(8)]
    width = (columns + 7) // 8
    # Padding must remain transparent even when real zero-valued cells are selected.
    padding = bytes([1 if mask is None else 0]) * (width * 8 - columns)
    compressor = zlib.compressobj(ZLIB_LEVEL)
    parts = []
    for line in range(count):
        row = count - 1 - line
        cells = _word_cells(rows, row, mask) if header.value_bits == 16 else rows.row(row)
        cells += padding
        # Join the eight bit positions with whole-number OR operations; the leftmost cell is the high bit
        packed = 0
        for position, table in enumerate(tables):
            packed |= int.from_bytes(cells[position::8].translate(table), "big")
        # Filter type 0 (None) starts each scan line
        parts.append(compressor.compress(b"\x00" + packed.to_bytes(width, "big")))
    parts.append(compressor.flush())
    # Bit depth 1, colour type 3 (palette), standard compression and filter, no interlace
    ihdr = struct.pack(">IIBBBBB", columns, count, 1, 3, 0, 0, 0)
    return b"".join((PNG_SIGNATURE, png_chunk(b"IHDR", ihdr), png_chunk(b"PLTE", PALETTE),
                     png_chunk(b"tRNS", TRANSPARENCY), png_chunk(b"IDAT", b"".join(parts)), png_chunk(b"IEND", b"")))


def _word_cells(rows: ValueRows, row: int, mask: int | None) -> bytes:
    """Reduce little-endian words to byte selections without discarding high tier bits."""
    columns = rows.header.columns
    start = row * columns * 2
    raw = rows.data[start:start + columns * 2]
    low, high = raw[0::2], raw[1::2]
    if mask is not None:
        # A named tier can occupy either byte; combine selected bits per cell.
        low = low.translate(bytes(bool(value & (mask & 255)) for value in range(256)))
        high = high.translate(bytes(bool(value & (mask >> 8)) for value in range(256)))
    # Unassigned needs both complete bytes to be zero, including unnamed bits.
    return (int.from_bytes(low, "big") | int.from_bytes(high, "big")).to_bytes(columns, "big")
