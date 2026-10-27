"""Synthetic PNG payloads and the budget rule of the bridge transfer measurement (plan leaf 3.14).

This module has no window and no file I/O, so the unit tests can run in CI. The measurement script
tools/measure_bridge_transfer.py sends these payloads through real pywebview evaluate_js calls.
"""

from __future__ import annotations

import base64
import random
import struct
import time
import zlib
from collections.abc import Iterator
from dataclasses import dataclass


# PNG file signature (PNG specification, section 5.2)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# zlib level of every synthetic PNG; the default level of zlib.compress
ZLIB_LEVEL = 6
# Repeats of each case; every repeat must succeed
RUNS = 3
# A180 rule: the slowest of the repeats of one bridge call takes 1 s or less
CALL_SECONDS = 1.0
# Verification target (Architect A9 D5), not a runtime timeout: the slowest whole background image load
# (all calls, the finish step and the decode) takes 5 s or less
IMAGE_SECONDS = 5.0
# Accepted pixel limit (Architect A9 D3): at most 8,192 pixels on each side, because a decoded image of
# 8,192 x 8,192 pixels is 256 MiB of RGBA; the transfer measurement alone cannot set this limit
IMAGE_SIDE_LIMIT = 8192
# Compared chunk sizes, in base64 characters per call (1 MiB and 4 MiB)
CHUNK_SIZES = (1 << 20, 4 << 20)


@dataclass(frozen=True)
class Case:
    """One measured payload family: a square image of `side` pixels sent `calls` times in each run."""

    name: str
    kind: str  # "mask" (1-bit palette tier mask) or "rgb" (8-bit RGB background)
    side: int
    pattern: str  # "band", "noise" or "gradient"
    calls: int = 1  # sequential tier masks of one header (4 or 8 value bits)


# Fixed case order, from the smallest to the largest base64 text per call
CASES = (
    Case("mask-band-4096", "mask", 4096, "band"),
    Case("mask-noise-4096", "mask", 4096, "noise"),
    Case("mask-noise-4096-x4", "mask", 4096, "noise", 4),
    Case("mask-noise-4096-x8", "mask", 4096, "noise", 8),
    Case("rgb-gradient-4096", "rgb", 4096, "gradient"),
    Case("rgb-gradient-8192", "rgb", 8192, "gradient"),
    Case("rgb-gradient-16384", "rgb", 16384, "gradient"),
    Case("rgb-noise-4096", "rgb", 4096, "noise"),
    Case("rgb-noise-8192", "rgb", 8192, "noise"),
)
# Cases that the run records without an attempt, with the reason
NOT_ATTEMPTED = {"rgb-noise-16384": "not attempted: its base64 text is near 1 GiB, above any useful budget"}


def png_chunk(kind: bytes, data: bytes) -> bytes:
    """Return one PNG chunk: length, type, data and the CRC-32 of type and data."""
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def build_png(width: int, height: int, bit_depth: int, color_type: int, rows: Iterator[bytes],
              extra: bytes = b"") -> bytes:
    """Return a PNG of the given rows, each with filter type 0, compressed as one stream."""
    header = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    # Compress row by row, so a 16384-pixel image never exists as one raw buffer
    compressor = zlib.compressobj(ZLIB_LEVEL)
    parts = [compressor.compress(b"\x00" + row) for row in rows]
    parts.append(compressor.flush())
    return (PNG_SIGNATURE + png_chunk(b"IHDR", header) + extra
            + png_chunk(b"IDAT", b"".join(parts)) + png_chunk(b"IEND", b""))


def mask_rows(side: int, pattern: str, seed: int) -> Iterator[bytes]:
    """Yield 1-bit mask rows: bands of 256 rows, or random bits (the incompressible worst case)."""
    width = side // 8
    generator = random.Random(seed)
    for row in range(side):
        if pattern == "band":
            yield (b"\xff" if (row // 256) % 2 else b"\x00") * width
        else:
            yield generator.randbytes(width)


def rgb_rows(side: int, pattern: str, seed: int) -> Iterator[bytes]:
    """Yield 8-bit RGB rows: a red-green gradient on constant blue, or random bytes."""
    generator = random.Random(seed)
    ramp = bytes(column * 255 // (side - 1) for column in range(side))
    blue = bytes([128]) * side
    for row in range(side):
        if pattern == "noise":
            yield generator.randbytes(3 * side)
            continue
        # Interleave the column ramp (red), the row value (green) and the constant blue
        pixels = bytearray(3 * side)
        pixels[0::3] = ramp
        pixels[1::3] = bytes([row * 255 // (side - 1)]) * side
        pixels[2::3] = blue
        yield bytes(pixels)


def case_pngs(case: Case) -> list[bytes]:
    """Return the PNG files of one run of a case; a tier set has one different mask for each tier."""
    if case.kind == "mask":
        # Palette entry 0 is fully transparent, as in the editor tier PNG
        extra = png_chunk(b"PLTE", b"\x00\x00\x00\xff\xff\xff") + png_chunk(b"tRNS", b"\x00")
        return [build_png(case.side, case.side, 1, 3, mask_rows(case.side, case.pattern, tier), extra)
                for tier in range(case.calls)]
    return [build_png(case.side, case.side, 8, 2, rgb_rows(case.side, case.pattern, 0))]


def encode_case(case: Case) -> tuple[list[bytes], list[str], float, float]:
    """Return the PNGs, their base64 texts and the PNG and base64 encoding times in seconds."""
    started = time.perf_counter()
    pngs = case_pngs(case)
    encoded = time.perf_counter()
    texts = [base64.b64encode(png).decode("ascii") for png in pngs]
    return pngs, texts, encoded - started, time.perf_counter() - encoded


def chunks(text: str, size: int) -> list[str]:
    """Split a base64 text into calls of at most `size` characters."""
    return [text[start:start + size] for start in range(0, len(text), size)] or [""]


def passed(row: dict) -> bool:
    """Return whether every repeat of a measured row succeeded."""
    runs = row.get("runs", [])
    return row.get("status") == "ok" and len(runs) == RUNS and all(run["ok"] for run in runs)


def worst(row: dict, field: str) -> float:
    """Return the slowest value of one timing field over the repeats of a row; a missing field counts as 0."""
    return max(run.get(field, 0.0) for run in row["runs"])


def calls_within_limit(row: dict) -> bool:
    """Return whether every text call and the finish step of a row took CALL_SECONDS or less (A9-314-1)."""
    return worst(row, "worst_call_s") <= CALL_SECONDS and worst(row, "finish_s") <= CALL_SECONDS


def power_of_two_floor(value: int) -> int:
    """Return the largest power of two that is not above a positive value."""
    return 1 << (value.bit_length() - 1)


def call_budget(single_rows: list[dict]) -> int | None:
    """Return the per-call base64 budget in characters, or None when no single-call case passed.

    Rule (A180, fixed before the run): take the largest per-call size S such that every single-call case
    of size S or less passed all repeats with its slowest call at CALL_SECONDS or less; round S down to a
    power of two.
    """
    largest = None
    for row in sorted(single_rows, key=lambda item: item["call_chars"]):
        if not passed(row) or worst(row, "worst_call_s") > CALL_SECONDS:
            break
        largest = row["call_chars"]
    return None if largest is None else power_of_two_floor(largest)


def chunk_size(chunk_rows: list[dict], budget: int) -> int | None:
    """Return the chunk size for images above the call budget, or None when no compared size passed.

    The candidates are the compared sizes within the call budget that have a row with repeats; a stopped row
    without repeats is skipped. The compared images are those that pass within IMAGE_SECONDS at one or more
    candidate sizes; a slower image is outside every image budget. A candidate size is disqualified when
    one compared image fails at it: no row, not every repeat ok, or a text call or the finish step above
    CALL_SECONDS (A9-314-1). Among the remaining sizes, the lower sum of slowest totals on the compared
    images wins; a tie takes the smaller size.
    """
    measured = [row for row in chunk_rows if row.get("runs") and row["chunk_chars"] <= budget]
    rows = {size: {row["case"]: row for row in measured if row["chunk_chars"] == size}
            for size in sorted({row["chunk_chars"] for row in measured})}
    # Compare the images that load within IMAGE_SECONDS at one or more candidate sizes
    images = sorted({case for cases in rows.values() for case, row in cases.items()
                     if passed(row) and worst(row, "total_s") <= IMAGE_SECONDS})
    if not images:
        return None
    # Keep the sizes at which every compared image passed with each call within CALL_SECONDS
    candidates = [(sum(worst(cases[case], "total_s") for case in images), size) for size, cases in rows.items()
                  if all(case in cases and passed(cases[case]) and calls_within_limit(cases[case])
                         for case in images)]
    return min(candidates, default=(None, None))[1]


def image_qualifies(row: dict, budget: int, chunk: int | None) -> bool:
    """Return whether an RGB row passed within IMAGE_SECONDS, in one call within the budget or in chunks."""
    if row["kind"] != "rgb" or not passed(row) or worst(row, "total_s") > IMAGE_SECONDS:
        return False
    if not calls_within_limit(row):
        return False
    return row["chunk_chars"] == chunk if row.get("chunk_chars") else row["call_chars"] <= budget


def propose_budgets(single_rows: list[dict], chunk_rows: list[dict]) -> dict:
    """Apply the budget rule to measured rows and return the proposed budgets.

    The pixel limit is the largest RGB side that qualifies, capped at IMAGE_SIDE_LIMIT; the uncapped side
    is reported as measured_image_side. The image byte limit is the larger of the raw
    bytes of one call and the raw bytes of the largest qualifying chunked text, rounded down to a power of
    two. The mask margin is the call budget divided by the largest tier mask text.
    """
    budget = call_budget(single_rows)
    if budget is None:
        return {"call_chars": None, "reason": "no single-call case passed the rule"}
    chunk = chunk_size(chunk_rows, budget)
    images = [row for row in single_rows + chunk_rows if image_qualifies(row, budget, chunk)]
    measured_side = max((row["side"] for row in images), default=None)
    side = min(measured_side, IMAGE_SIDE_LIMIT) if measured_side else None
    chunked = max((row["text_chars"] for row in images if row.get("chunk_chars")), default=0)
    masks = [row["call_chars"] for row in single_rows if row["kind"] == "mask"]
    return {
        "call_chars": budget,
        "call_raw_bytes": budget * 3 // 4,
        "chunk_chars": chunk,
        "measured_image_side": measured_side,
        "image_side": side,
        "image_pixels": side * side if side else None,
        "image_raw_bytes": max(budget * 3 // 4, power_of_two_floor(chunked * 3 // 4) if chunked else 0),
        "worst_mask_chars": max(masks, default=None),
        "mask_margin": round(budget / max(masks), 2) if masks else None,
    }
