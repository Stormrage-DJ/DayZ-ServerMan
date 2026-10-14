"""Parity of the window copies of backend Inspect texts with the Python catalogue (4.6, 6.9)."""

from __future__ import annotations

import json
import re
import unittest

from dayz_serverman.domain.mission_map_loot_report_texts import CAPACITY_REASON_TEXTS, KEPT_TEXTS, UNAVAILABLE_TEXTS
from dayz_serverman.repositories.mission_map_object_capacity import POINT_TARGET
from tests.ui_harness_support import FRONTEND


def window_text(name: str) -> str:
    """Return the string literal that the window wording module assigns to one key of its backend copies."""
    source = (FRONTEND / "map_editor_inspect_wording.js").read_text(encoding="utf-8")
    found = re.findall(rf'^  {name}: ("(?:[^"\\]|\\.)*"),$', source, re.MULTILINE)
    if len(found) != 1:
        raise AssertionError(f"{name}: expected one window copy, found {len(found)}")
    return json.loads(found[0])


class InspectWordingParityTests(unittest.TestCase):
    """The window shows the exact backend sentences; a change on one side fails here."""

    def test_point_capacity_sentence_matches_the_backend(self) -> None:
        """A point answer shows the Product Owner sentence of the point_target reason (2026-10-13 15:19:49)."""
        self.assertEqual(window_text("pointCapacity"), CAPACITY_REASON_TEXTS[POINT_TARGET])
        self.assertEqual(window_text("pointCapacity"),
                         "Building capacity: inspect a zone to see the buildings it covers.")

    def test_kept_and_unavailable_copies_match_the_backend(self) -> None:
        """The kept planner reason of a capacity entry and the not-holdable tier reason are exact copies."""
        self.assertEqual(window_text("plannerContent"), KEPT_TEXTS["planner_content"])
        self.assertEqual(window_text("tierNotHoldable"), UNAVAILABLE_TEXTS["tier_not_holdable"])


if __name__ == "__main__":
    unittest.main()
