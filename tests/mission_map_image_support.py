"""Synthetic map images for the image selection and display-read tests; nothing reads a real image."""

from __future__ import annotations

import struct
import zlib
from typing import Callable

from dayz_serverman.domain.mission_map_png import PNG_SIGNATURE, png_chunk


def png_rgb(width: int, height: int, colour: Callable[[int, int], tuple[int, int, int]] = lambda x, y: (0, 0, 0),
            extra: bytes = b"") -> bytes:
    """Return a decodable 8-bit RGB PNG; colour(x, y) gives each pixel, with y = 0 the top row."""
    lines = b"".join(b"\x00" + b"".join(bytes(colour(x, y)) for x in range(width)) for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    # An optional private chunk makes a file larger without changing its pixels
    padding = png_chunk(b"prVt", extra) if extra else b""
    return b"".join((PNG_SIGNATURE, png_chunk(b"IHDR", ihdr), padding, png_chunk(b"IDAT", zlib.compress(lines)),
                     png_chunk(b"IEND", b"")))


def png_header_only(width: int, height: int) -> bytes:
    """Return the signature and IHDR of a PNG with any pixel size; the header check needs no pixels."""
    return PNG_SIGNATURE + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))


def jpeg_header_only(width: int, height: int, marker: int = 0xC0) -> bytes:
    """Return SOI, an APP0 segment, fill bytes and one start-of-frame segment with the pixel size."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    frame = b"\xff\xff" + bytes((marker,)) + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + frame + b"\xff\xd9"
