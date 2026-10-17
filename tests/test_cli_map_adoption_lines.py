"""Leaf 10.13: the adoption lines of `map show`, `map preview` and `map apply` (Product Owner 2026-10-16 14:35:24).

`map show` prints the ADOPT-6 status line and its CLI line also while the plan stays editable. It prints the
adoption line only when no shown read-only text names adoption (Product Owner 20:58:44 in evidence 10.10).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.cli import map_wording as words  # noqa: E402
from dayz_serverman.cli.commands.map_publish import _check_state  # noqa: E402
from dayz_serverman.cli.map_render import show_blocks  # noqa: E402
from dayz_serverman.cli.map_review_render import review_blocks  # noqa: E402
from dayz_serverman.cli.output import CliFailure, TypeText, render_blocks  # noqa: E402

# The Product Owner texts of the five adoption lines, word for word
LINES = {
    "move": "Adoption is needed: this mission moved. Run map adopt to review it.",
    "copy": "Adoption is needed: this mission is a copy of another mission path. Run map adopt to review it.",
    "retired": "Adoption is needed: this mission returned to an earlier path. Run map adopt to review it.",
    "restore": "Adoption is needed: a restore removed the last Apply record of this profile for this mission. Run "
               "map adopt to review it.",
}
CAPTURE_LINE = ("Adoption is needed: the files of this mission match map editor output for another mission path. Run "
                "map adopt to review it.")
CAPTURE_MESSAGE = "Original capture refused: editor output from operation op0001 was found."


def show(reason: str | None, read_only: bool, case: str | None) -> list[str]:
    """Return the text lines of `map show` for a load answer without a plan."""
    loaded = {"mission_name": "dayzOffline.chernarusplus", "state": "MISSING", "read_only": read_only,
              "read_only_reason": reason, "adoption": None if case is None else {"case": case}, "plan": None,
              "capabilities": {}}
    return render_blocks(show_blocks(None, loaded)).splitlines()


def apply_answer(adoption: dict[str, Any] | None) -> dict[str, Any]:
    """Return a refused Apply answer with a loot refusal, the capture refusal and a stale-records refusal."""
    return {"refused": True, "refusals": [{"reason": "limits", "message": "A loot limit refuses.", "file": None},
                                          {"reason": "editor_output", "message": CAPTURE_MESSAGE, "file": None},
                                          {"reason": "records_stale", "message": "Editor records changed.",
                                           "file": None}],
            "capabilities": {}, "capture": {"result": "refused", "reason": "editor_output", "adoption": adoption}}


class ShowLineTests(unittest.TestCase):
    """The adoption line of `map show` for each case and each read-only text."""

    def test_texts_are_the_product_owner_texts(self) -> None:
        """The four case lines and the capture line, with `map adopt` as text to type."""
        self.assertEqual(words.ADOPTION_LINES, LINES)
        self.assertEqual(words.CAPTURE_ADOPTION_LINE, CAPTURE_LINE)
        line = words.adoption_line({"case": "move"})
        self.assertIn(TypeText("map adopt"), line.parts)
        self.assertIsNone(words.adoption_line(None))
        self.assertIsNone(words.adoption_line({"case": "move"}, capture=True))

    def test_a_read_only_text_that_names_adoption_has_no_adoption_line(self) -> None:
        """The path-change states print ADOPT-4 or ADOPT-5 and their CLI line only."""
        for reason, case in (("mission_path_changed", "move"), ("mission_path_changed", "copy"),
                             ("returned_to_earlier_path", "retired")):
            with self.subTest(reason=reason, case=case):
                lines = show(reason, True, case)
                self.assertIn(words.READ_ONLY_TEXTS[reason], lines)
                self.assertIn("Run map adopt.", lines)
                self.assertFalse(any(line.startswith("Adoption is needed") for line in lines))

    def test_adopt_6_shows_while_the_plan_stays_editable(self) -> None:
        """The restore case prints the ADOPT-6 status line and "Run map adopt." only."""
        lines = show("adoption_required", False, "restore")
        place = lines.index(words.READ_ONLY_TEXTS["adoption_required"])
        self.assertEqual(lines[place + 1], "Run map adopt.")
        self.assertTrue(lines[place].startswith("Apply is not possible: a restore removed the last Apply record"))
        self.assertNotIn(LINES["restore"], lines)

    def test_the_adoption_line_follows_a_text_that_does_not_name_adoption(self) -> None:
        """Invalid saved data with an invalidated record, and an editable copy, print the adoption line."""
        lines = show("invalid_saved_data", True, "restore")
        place = lines.index(words.READ_ONLY_TEXTS["invalid_saved_data"])
        self.assertEqual(lines[place:place + 3], [words.READ_ONLY_TEXTS["invalid_saved_data"], "Run map set-aside.",
                                                  LINES["restore"]])
        editable = show(None, False, "copy")
        self.assertEqual(editable[editable.index("Plan state: No saved plan") + 1], LINES["copy"])
        self.assertFalse(any(line.startswith("Adoption is needed") for line in show(None, False, None)))


class PublishLineTests(unittest.TestCase):
    """The adoption line of `map preview` and `map apply` for `capture.adoption`."""

    def test_preview_prints_the_line_after_the_capture_refusal(self) -> None:
        """The line follows the capture refusal in the "Apply is not possible" section."""
        lines = render_blocks(review_blocks(None, {}, apply_answer({"case": "copy"}))).splitlines()
        place = lines.index(f"  {CAPTURE_MESSAGE}")
        self.assertEqual(lines[place + 1], f"  {CAPTURE_LINE}")
        self.assertEqual(lines.index("  A loot limit refuses."), place - 1)
        self.assertEqual(lines[place + 2], "  Editor records changed.")
        without = render_blocks(review_blocks(None, {}, apply_answer(None)))
        self.assertNotIn("Adoption is needed", without)

    def test_apply_exits_with_the_line_after_the_refusal(self) -> None:
        """A refused review exits 3 before the question; the message ends with the adoption line."""
        with self.assertRaises(CliFailure) as raised:
            _check_state(apply_answer({"case": "copy"}))
        self.assertEqual(str(raised.exception), f"A loot limit refuses. {CAPTURE_LINE}")
        self.assertEqual((raised.exception.code, raised.exception.exit_code), ("INVALID_REQUEST", 3))
        with self.assertRaises(CliFailure) as raised:
            _check_state(apply_answer(None))
        self.assertEqual(str(raised.exception), "A loot limit refuses.")


if __name__ == "__main__":
    unittest.main()
