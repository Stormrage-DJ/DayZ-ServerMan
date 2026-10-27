"""Window-free tests of the bridge transfer measurement: PNG payloads, page policy and the budget rule."""

from __future__ import annotations

import base64
import struct
import unittest
import zlib

from tests import PROJECT_ROOT
from tools.bridge_transfer_payloads import (
    CHUNK_SIZES, IMAGE_SIDE_LIMIT, PNG_SIGNATURE, RUNS, Case, call_budget, case_pngs, chunk_size, chunks,
    encode_case, power_of_two_floor, propose_budgets)
from tools.bridge_transfer_window import CSP_MARKER, CSP_PATTERN, compose_page


def png_parts(png: bytes) -> tuple[dict[bytes, bytes], bytes]:
    """Return the chunks of a PNG by type and its decompressed image data; check every CRC."""
    assert png.startswith(PNG_SIGNATURE)
    position, found, data = len(PNG_SIGNATURE), {}, b""
    while position < len(png):
        length, kind = struct.unpack(">I4s", png[position:position + 8])
        body = png[position + 8:position + 8 + length]
        crc = struct.unpack(">I", png[position + 8 + length:position + 12 + length])[0]
        assert crc == zlib.crc32(kind + body), kind
        found[kind] = body
        data += body if kind == b"IDAT" else b""
        position += 12 + length
    return found, zlib.decompress(data)


def row(side: int, chars: int, seconds: float, ok: bool = True, kind: str = "mask", **extra: object) -> dict:
    """Return a measured row with RUNS repeats of one call and total time."""
    runs = [{"ok": ok, "worst_call_s": seconds, "total_s": seconds} for _ in range(RUNS)]
    return {"case": f"{kind}-{side}", "kind": kind, "side": side, "call_chars": chars,
            "text_chars": chars, "chunk_chars": None, "status": "ok", "runs": runs, **extra}


class PayloadTests(unittest.TestCase):
    """The synthetic PNGs are valid and match the editor tier PNG and RGB layouts."""

    def test_mask_is_a_one_bit_palette_png_with_transparent_index_zero(self) -> None:
        """A band mask has 1-bit palette rows of filter type 0 and alternates every 256 rows."""
        found, data = png_parts(case_pngs(Case("m", "mask", 512, "band"))[0])
        self.assertEqual(struct.unpack(">IIBBBBB", found[b"IHDR"]), (512, 512, 1, 3, 0, 0, 0))
        self.assertEqual((found[b"PLTE"], found[b"tRNS"]), (b"\x00\x00\x00\xff\xff\xff", b"\x00"))
        rows = [data[index * 65:(index + 1) * 65] for index in range(512)]
        self.assertEqual({line[0] for line in rows}, {0})
        self.assertEqual((rows[0][1:], rows[256][1:]), (b"\x00" * 64, b"\xff" * 64))

    def test_tier_set_masks_differ_and_repeat(self) -> None:
        """Each tier of a set has its own noise, and a second generation gives the same files."""
        first = case_pngs(Case("m", "mask", 64, "noise", 4))
        self.assertEqual(len(set(first)), 4)
        self.assertEqual(first, case_pngs(Case("m", "mask", 64, "noise", 4)))

    def test_rgb_gradient_pixels(self) -> None:
        """Red follows the column, green the row, and blue stays 128."""
        found, data = png_parts(case_pngs(Case("g", "rgb", 8, "gradient"))[0])
        self.assertEqual(struct.unpack(">IIBBBBB", found[b"IHDR"]), (8, 8, 8, 2, 0, 0, 0))
        self.assertEqual(len(data), 8 * (1 + 24))
        line = data[3 * 25:4 * 25]
        self.assertEqual(line[0], 0)
        self.assertEqual([tuple(line[1 + 3 * x:4 + 3 * x]) for x in (0, 7)], [(0, 109, 128), (255, 109, 128)])

    def test_rgb_noise_size_and_base64_round_trip(self) -> None:
        """Noise rows have three bytes per pixel and the base64 text decodes to the PNG."""
        pngs, texts, png_s, b64_s = encode_case(Case("n", "rgb", 32, "noise"))
        self.assertEqual(len(png_parts(pngs[0])[1]), 32 * (1 + 96))
        self.assertEqual(base64.b64decode(texts[0]), pngs[0])
        self.assertGreaterEqual(min(png_s, b64_s), 0.0)

    def test_chunks_cover_the_text_in_order(self) -> None:
        """Chunks keep the order, and only the last one is shorter."""
        parts = chunks("abcdefghij", 4)
        self.assertEqual(parts, ["abcd", "efgh", "ij"])
        self.assertEqual(chunks("", 4), [""])


class PageTests(unittest.TestCase):
    """The measurement page has the editor content security policy unchanged."""

    def test_page_copies_the_editor_policy(self) -> None:
        """The policy element of editor.html replaces the marker and permits only data: images."""
        editor = (PROJECT_ROOT / "runnable" / "src" / "frontend" / "editor.html").read_text(encoding="utf-8")
        page = compose_page()
        policy = CSP_PATTERN.search(editor).group(0)
        self.assertIn(policy, page)
        self.assertIn("img-src data:;", policy)
        self.assertNotIn(CSP_MARKER, page)


class BudgetRuleTests(unittest.TestCase):
    """The budget rule gives the documented numbers for known rows."""

    def test_power_of_two_floor(self) -> None:
        """Values round down to a power of two; powers of two stay."""
        self.assertEqual([power_of_two_floor(value) for value in (1, 3, 4, 2_805_175)], [1, 2, 4, 2_097_152])

    def test_call_budget_stops_at_the_first_slow_or_failed_size(self) -> None:
        """Every smaller size must pass; a larger passing size after a failure does not count."""
        rows = [row(4096, 3_000_000, 0.5), row(8192, 9_000_000, 1.2), row(4096, 40_000_000, 0.9)]
        self.assertEqual(call_budget(rows), 2_097_152)
        rows[1] = row(8192, 9_000_000, 0.4, ok=False)
        self.assertEqual(call_budget(rows), 2_097_152)
        self.assertIsNone(call_budget([row(4096, 3_000_000, 1.01)]))

    def test_one_failed_repeat_fails_the_row(self) -> None:
        """A row passes only when all RUNS repeats succeed."""
        failed = row(4096, 1_000, 0.1)
        failed["runs"][2]["ok"] = False
        self.assertIsNone(call_budget([failed]))

    def test_budgets_choose_chunk_and_largest_image(self) -> None:
        """The faster passing chunk size inside the budget wins, and it sets the largest image."""
        single = [row(4096, 2_800_000, 0.5), row(4096, 6_000_000, 0.8, kind="rgb"),
                  row(8192, 20_000_000, 3.0, kind="rgb")]
        chunked = [row(8192, size, 0.2, kind="rgb", chunk_chars=size, text_chars=20_000_000) for size in CHUNK_SIZES]
        chunked[0]["runs"][0]["total_s"] = 4.0
        chunked[1]["runs"][0]["total_s"] = 3.5
        budgets = propose_budgets(single, chunked)
        self.assertEqual(budgets["call_chars"], 4_194_304)
        self.assertEqual(budgets["call_raw_bytes"], 3_145_728)
        self.assertEqual(budgets["chunk_chars"], CHUNK_SIZES[1])
        self.assertEqual((budgets["measured_image_side"], budgets["image_side"], budgets["image_pixels"]),
                         (8192, 8192, 8192 * 8192))
        self.assertEqual(budgets["image_raw_bytes"], 8_388_608)
        self.assertEqual((budgets["worst_mask_chars"], budgets["mask_margin"]), (2_800_000, 1.5))

    def test_slow_image_does_not_qualify(self) -> None:
        """An image above IMAGE_SECONDS sets no pixel limit; the byte limit stays at one call."""
        chunked = [row(8192, CHUNK_SIZES[0], 0.2, kind="rgb", chunk_chars=CHUNK_SIZES[0])]
        chunked[0]["runs"][1]["total_s"] = 9.0
        budgets = propose_budgets([row(4096, 2_800_000, 0.5)], chunked)
        self.assertEqual((budgets["call_chars"], budgets["chunk_chars"], budgets["image_side"]),
                         (2_097_152, None, None))
        self.assertEqual(budgets["image_raw_bytes"], 1_572_864)

    def test_chunk_size_above_the_budget_or_with_a_slow_call_is_not_chosen(self) -> None:
        """A chunk size above the call budget, or one slow text call, rules the size out."""
        chunked = [row(8192, size, 0.2, kind="rgb", chunk_chars=size) for size in CHUNK_SIZES]
        self.assertEqual(chunk_size(chunked, 2_097_152), CHUNK_SIZES[0])
        chunked[0]["runs"][2]["worst_call_s"] = 1.2
        self.assertIsNone(chunk_size(chunked, 2_097_152))
        self.assertEqual(chunk_size(chunked, 4_194_304), CHUNK_SIZES[1])

    def test_slow_finish_step_rules_out_the_chunk_size_and_the_image(self) -> None:
        """A9-314-1: a finish step above 1 s fails the per-call check like a slow text call."""
        chunked = [row(8192, CHUNK_SIZES[1], 0.3, kind="rgb", chunk_chars=CHUNK_SIZES[1], text_chars=60_000_000)]
        chunked[0]["runs"][1]["finish_s"] = 1.35
        budgets = propose_budgets([row(4096, 6_000_000, 0.5)], chunked)
        self.assertEqual((budgets["chunk_chars"], budgets["image_side"]), (None, None))
        chunked[0]["runs"][1]["finish_s"] = 0.33
        budgets = propose_budgets([row(4096, 6_000_000, 0.5)], chunked)
        self.assertEqual((budgets["chunk_chars"], budgets["image_side"]), (CHUNK_SIZES[1], 8192))

    def test_image_above_the_image_target_does_not_block_the_chunk_size(self) -> None:
        """A slow image with a slow finish step is outside every image budget and leaves the chunk choice."""
        fast = row(4096, CHUNK_SIZES[1], 0.3, kind="rgb", chunk_chars=CHUNK_SIZES[1], text_chars=60_000_000)
        slow = row(8192, CHUNK_SIZES[1], 0.3, kind="rgb", chunk_chars=CHUNK_SIZES[1], text_chars=260_000_000)
        for run in slow["runs"]:
            run.update(finish_s=1.35, total_s=13.2)
        budgets = propose_budgets([row(4096, 6_000_000, 0.5)], [fast, slow])
        self.assertEqual((budgets["chunk_chars"], budgets["image_side"], budgets["image_raw_bytes"]),
                         (CHUNK_SIZES[1], 4096, 33_554_432))

    def test_stopped_chunk_row_is_skipped(self) -> None:
        """QA-314-1: a chunk row without repeats (closed window or timeout) neither raises nor counts."""
        done = row(4096, CHUNK_SIZES[0], 0.05, kind="rgb", chunk_chars=CHUNK_SIZES[0], text_chars=67_134_884)
        stopped = dict(row(4096, CHUNK_SIZES[1], 0.05, kind="rgb", chunk_chars=CHUNK_SIZES[1]),
                       status="stopped: the window was closed", runs=[])
        budgets = propose_budgets([row(4096, 6_000_000, 0.5)], [done, stopped])
        self.assertEqual((budgets["chunk_chars"], budgets["image_side"]), (CHUNK_SIZES[0], 4096))

    def test_sizes_are_compared_on_the_same_images(self) -> None:
        """QA-314-2: an image that misses 5 s at one size leaves both sums, so the faster size wins."""
        slow_at_4 = [row(8192, size, 0.1, kind="rgb", chunk_chars=size, text_chars=60_000_000) for size in CHUNK_SIZES]
        fast = [row(4096, size, 0.1, kind="rgb", chunk_chars=size, text_chars=20_000_000) for size in CHUNK_SIZES]
        for image, totals in ((slow_at_4, (4.9, 5.2)), (fast, (1.0, 1.1))):
            for size_row, total in zip(image, totals):
                for run in size_row["runs"]:
                    run["total_s"] = total
        budgets = propose_budgets([row(4096, 6_000_000, 0.5)], slow_at_4 + fast)
        self.assertEqual((budgets["chunk_chars"], budgets["image_side"], budgets["image_raw_bytes"]),
                         (CHUNK_SIZES[0], 8192, 33_554_432))

    def test_failure_at_one_size_disqualifies_that_size(self) -> None:
        """QA-314-3 R3a: an image that fails only at 4 MiB rules out 4 MiB, although another image is faster there."""
        x_one = row(8192, CHUNK_SIZES[0], 0.1, kind="rgb", chunk_chars=CHUNK_SIZES[0], text_chars=60_000_000)
        x_four = row(8192, CHUNK_SIZES[1], 0.1, ok=False, kind="rgb", chunk_chars=CHUNK_SIZES[1],
                     text_chars=60_000_000, status="failed")
        y_one = row(4096, CHUNK_SIZES[0], 0.1, kind="rgb", chunk_chars=CHUNK_SIZES[0], text_chars=20_000_000)
        y_four = row(4096, CHUNK_SIZES[1], 0.1, kind="rgb", chunk_chars=CHUNK_SIZES[1], text_chars=20_000_000)
        for size_row, total in ((x_one, 3.0), (x_four, 0.4), (y_one, 1.0), (y_four, 0.9)):
            for run in size_row["runs"]:
                run["total_s"] = total
        single = [row(4096, 6_000_000, 0.5)]
        self.assertEqual(chunk_size([x_one, x_four, y_one, y_four], 4_194_304), CHUNK_SIZES[0])
        self.assertEqual(propose_budgets(single, [x_one, x_four, y_one, y_four])["image_raw_bytes"], 33_554_432)
        # R3b: the only image fails at 4 MiB and passes at 1 MiB
        self.assertEqual(chunk_size([x_one, x_four], 4_194_304), CHUNK_SIZES[0])

    def test_pixel_limit_is_capped_at_the_accepted_side(self) -> None:
        """A9 D3: a qualifying 16,384-pixel image proposes 8,192 pixels and reports the measured side."""
        single = [row(4096, 6_000_000, 0.5), row(16384, 3_000_000, 0.6, kind="rgb")]
        budgets = propose_budgets(single, [])
        self.assertEqual((budgets["measured_image_side"], budgets["image_side"], budgets["image_pixels"]),
                         (16384, IMAGE_SIDE_LIMIT, IMAGE_SIDE_LIMIT ** 2))


if __name__ == "__main__":
    unittest.main()
