"""Synthetic map image and the A9 rule of the 3.16 real-window check; no window and no file I/O.

The image is an 8-bit RGB PNG of at most 8,192 × 8,192 pixels whose file size is just below a byte
target. Random rows make the bytes incompressible, and plain rows fill the remaining pixel rows.
"""

from __future__ import annotations

import os
import struct
import zlib

from dayz_serverman.domain.mission_map_png import PNG_SIGNATURE, png_chunk


# The A9 condition: whole image in 5 s or less; each bridge call and the final join in 1 s or less
IMAGE_SECONDS = 5.0
CALL_SECONDS = 1.0
# Byte targets of the condition: 32 MiB, the 16 MiB fallback and one call budget (6 MiB)
TARGETS = {"32mib": 33_554_432, "16mib": 16_777_216, "6mib": 6_291_456}
# Room for the chunks, the plain rows and the zlib overhead of random rows
MARGIN = 262_144


def noise_png(side: int, target: int, random: object = os.urandom) -> bytes:
    """Return a side × side RGB PNG of at most target bytes: random rows first, then plain grey rows."""
    row_bytes = side * 3
    # Random data does not compress, so each random row costs about its raw size plus the filter byte
    noisy = max(0, min(side, (target - MARGIN) // (row_bytes + 1)))
    while True:
        compressor = zlib.compressobj(6)
        parts = []
        plain = b"\x00" + bytes([96]) * row_bytes
        for line in range(side):
            parts.append(compressor.compress(b"\x00" + random(row_bytes) if line < noisy else plain))
        parts.append(compressor.flush())
        ihdr = struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0)
        data = b"".join((PNG_SIGNATURE, png_chunk(b"IHDR", ihdr), png_chunk(b"IDAT", b"".join(parts)),
                         png_chunk(b"IEND", b"")))
        if len(data) <= target or noisy == 0:
            return data
        # Too large by a little: drop random rows until the file fits
        noisy -= max(1, (len(data) - target) // row_bytes + 1)


def judge(run: dict) -> dict:
    """Return the A9 verdict of one run: total, slowest call and join against their limits."""
    calls = [call["seconds"] for call in run.get("calls", [])]
    slowest = max(calls, default=float("inf"))
    checks = {"loaded": bool(run.get("drawn")) and run.get("error") is None,
              "total": run.get("total_s", float("inf")) <= IMAGE_SECONDS,
              "calls": slowest <= CALL_SECONDS,
              "join": run.get("join_s", float("inf")) <= CALL_SECONDS}
    return {"slowest_call_s": slowest, "checks": checks, "passed": all(checks.values())}
