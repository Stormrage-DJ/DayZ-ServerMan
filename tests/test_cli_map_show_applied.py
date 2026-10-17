"""QA-10.31-F4: the applied-state line of `map show` by plan state (Product Owner 2026-10-17 13:41:00 in evidence 10.31)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.cli.map_render import show_blocks  # noqa: E402
from dayz_serverman.cli.output import render_blocks  # noqa: E402

CHANGES = "The saved plan has changes that no Apply has written."
MATCHES = "The saved plan matches the last Apply."
EMPTY = "The saved plan is empty."


def lines(state: str, *, objects: int = 0, capacity: int = 0, changes: bool = False, plan: bool = True) -> list[str]:
    """Return the `map show` lines of a synthetic load answer."""
    document = {"objects": [{"id": f"o{n}"} for n in range(objects)], "global_abundance": capacity,
                "terrain": {"bounds_confirmed": True}} if plan else None
    loaded = {"state": state, "mission_name": "m", "plan": document, "draft_changes": changes, "capabilities": {},
              "read_only": False, "read_only_reason": None, "revision": 1}
    return render_blocks(show_blocks({"display_name": "Main"}, loaded)).splitlines()


class AppliedLineTests(unittest.TestCase):
    """One line at most, chosen by the plan state."""

    def applied(self, shown: list[str]) -> list[str]:
        """Return the applied-state lines among the lines."""
        return [line for line in shown if line.startswith("The saved plan")]

    def test_no_line_unless_the_plan_is_valid(self) -> None:
        """A missing, invalid, newer or foreign plan prints no applied-state line."""
        for state in ("MISSING", "CORRUPT", "INVALID_PLAN", "FUTURE_SCHEMA", "NEWER_PLAN", "OTHER_ASSOCIATION"):
            with self.subTest(state=state):
                self.assertEqual(self.applied(lines(state, objects=1, changes=True)), [])
                self.assertEqual(self.applied(lines(state, plan=False)), [])

    def test_changes_that_no_apply_has_written(self) -> None:
        """A valid plan with changes, also an empty one."""
        self.assertEqual(self.applied(lines("VALID", objects=1, changes=True)), [CHANGES])
        self.assertEqual(self.applied(lines("VALID", changes=True)), [CHANGES])

    def test_matches_the_last_apply_only_with_content(self) -> None:
        """Objects, or a global capacity other than 0, imply a last Apply."""
        self.assertEqual(self.applied(lines("VALID", objects=2)), [MATCHES])
        self.assertEqual(self.applied(lines("VALID", capacity=25)), [MATCHES])
        self.assertEqual(self.applied(lines("VALID", capacity=-25)), [MATCHES])

    def test_an_empty_valid_plan(self) -> None:
        """No objects, capacity 0 and no changes."""
        self.assertEqual(self.applied(lines("VALID")), [EMPTY])


if __name__ == "__main__":
    unittest.main()
