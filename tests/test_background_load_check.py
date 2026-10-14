"""Verify the image generator and the A9 rule of the 3.16 real-window check; no window opens."""

from __future__ import annotations

import struct
import unittest

from dayz_serverman.domain.mission_map_image import image_header
from tools.background_load_image import CALL_SECONDS, IMAGE_SECONDS, TARGETS, judge, noise_png


class BackgroundLoadCheckTests(unittest.TestCase):
    """The generated file stays below its target; the rule applies the 5 s and 1 s limits."""

    def test_targets_are_the_a9_steps(self) -> None:
        """32 MiB, the 16 MiB fallback and one call budget; 5 s for the image and 1 s for a call."""
        self.assertEqual(TARGETS, {"32mib": 33_554_432, "16mib": 16_777_216, "6mib": 6_291_456})
        self.assertEqual((IMAGE_SECONDS, CALL_SECONDS), (5.0, 1.0))

    def test_noise_png_fits_its_target_and_side(self) -> None:
        """Random rows fill the file close to the target, and the header gives the side."""
        data = noise_png(512, 600_000)
        self.assertLessEqual(len(data), 600_000)
        self.assertGreater(len(data), 300_000)
        header = image_header(data)
        self.assertEqual((header.media_type, header.width, header.height), ("image/png", 512, 512))
        self.assertEqual(struct.unpack(">II", data[16:24]), (512, 512))

    def test_too_large_first_try_drops_rows(self) -> None:
        """A random source that compresses worse than expected still gives a file within the target."""
        data = noise_png(256, 300_000, random=lambda count: bytes(range(256)) * (count // 256) + bytes(count % 256))
        self.assertLessEqual(len(data), 300_000)

    def test_judge(self) -> None:
        """Every limit must hold, and an unfinished or failed run fails."""
        good = {"drawn": True, "error": None, "total_s": 4.9, "join_s": 0.5, "calls": [{"seconds": 0.9}]}
        self.assertTrue(judge(good)["passed"])
        for change in ({"total_s": 5.1}, {"join_s": 1.2}, {"calls": [{"seconds": 1.01}]}, {"drawn": False},
                       {"error": "boom"}, {"calls": []}):
            self.assertFalse(judge({**good, **change})["passed"], change)
        self.assertFalse(judge({})["passed"])


if __name__ == "__main__":
    unittest.main()
