"""Leaf 10.5, PO-6: the window copies of `map preview` and `map apply` equal the window files.

The CLI lines and the Apply late-change text are the Product Owner texts word for word.

The CLI lines name options, so they are exempt from the window comparison (Product Owner 2026-10-16 14:26:38 in
evidence 10.1). They are pinned here against an independent copy of that entry.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from test_cli_map_wording import STRING, literal, source  # noqa: E402

from dayz_serverman.cli import map_publish_wording as words  # noqa: E402
from dayz_serverman.cli.output import Echo, TypeText, Value, line_text  # noqa: E402
from dayz_serverman.domain.mission_map_preview_texts import TARGET_CHANGED_TEXT  # noqa: E402

# An independent copy of the CLI texts of the Product Owner (2026-10-16 14:26:38 in evidence 10.1)
CLI_TEXTS = {
    "FILE_CHOICE": "Choose for each file: add --keep-change {path} to keep the change, or --replace-file {path} to "
                   "replace the file.",
    "ENCOUNTER_CHOICE": "Apply cannot write these encounters on this mission. Add --exclude-encounter {id} for each "
                        "one to leave it out of this Apply.",
    "MISSING_CHOICE": "Add --keep-change PATH or --replace-file PATH for each file, and --exclude-encounter ID for "
                      "each encounter.",
    "NOT_IN_REVIEW": "{value} is not in the review. Use a path or ID that the review lists.",
    "IN_BOTH": "{path} is in both --keep-change and --replace-file. Choose one.",
    "EXCLUDED": "Excluded in the plan. Apply does not write these encounters.",
}
# Product Owner text 7c of 2026-10-15 15:57:10 in evidence 8.10
LATE_CHANGE = ("Apply stopped because {path} changed after the preview. Nothing was applied, and that file keeps its "
               "current content. Preview the changes again and check them before you apply.")


def joined(text: str, key: str, scope: str | None = None) -> str:
    """Return the one `key: "..." + "..."` entry of the window file, inside `scope: Object.freeze({...})` if named."""
    if scope is not None:
        text = text.split(f"{scope}: Object.freeze({{", 1)[1].split("})", 1)[0]
    found = re.findall(rf"\b{key}:\s*({STRING}(?:\s*\+\s*{STRING})*)", text)
    if len(found) != 1:
        raise AssertionError(f"{key}: expected one entry, found {len(found)}")
    return "".join(json.loads(f'"{part}"') for part in re.findall(STRING, found[0][0]))


class WindowCopyTests(unittest.TestCase):
    """Each window copy is the same string in `map_editor_preview_words.js` or the 7.3 encounter wording."""

    @classmethod
    def setUpClass(cls) -> None:
        """Read the two window files."""
        cls.words = source("map_editor_preview_words.js")
        cls.encounters = source("map_editor_encounter_wording.js")

    def test_headings_and_the_stop_line(self) -> None:
        """The eight Apply headings and the stop-server line of the footer."""
        for key, copy in (("refusals", words.REFUSALS_HEADING), ("notices", words.NOTICES_HEADING),
                          ("external", words.EXTERNAL_HEADING), ("encounterChoices", words.ENCOUNTER_CHOICES_HEADING),
                          ("changes", words.CHANGES_HEADING), ("encounters", words.ENCOUNTERS_HEADING),
                          ("loot", words.LOOT_HEADING), ("files", words.FILES_HEADING)):
            with self.subTest(key=key):
                self.assertEqual(joined(self.words, key, "headings"), copy)
        self.assertEqual(joined(self.words, "apply", "stop"), words.STOP_SERVER)
        self.assertEqual(joined(self.words, "apply", "others"), words.OTHER_PROFILES)

    def test_notes_and_list_labels(self) -> None:
        """The notes and the labels of the lists, with the PO-A1 label of the plan-excluded list."""
        for key, copy in (("bounds", words.BOUNDS_NOT_CONFIRMED), ("replaceNote", words.REPLACE_NOTE),
                          ("linkedLoot", words.LINKED_LOOT), ("noPrevious", words.NO_PREVIOUS),
                          ("added", words.ADDED), ("changed", words.CHANGED), ("removed", words.REMOVED),
                          ("written", words.WRITTEN), ("excluded", words.EXCLUDED),
                          ("lootUnavailable", words.LOOT_UNAVAILABLE), ("lootCapacity", words.LOOT_CAPACITY),
                          ("lootKept", words.LOOT_KEPT), ("affected", words.AFFECTED),
                          ("coordinated", words.COORDINATED)):
            with self.subTest(key=key):
                self.assertEqual(joined(self.words, key), copy)
        self.assertEqual(words.EXCLUDED, CLI_TEXTS["EXCLUDED"])

    def test_texts_with_values(self) -> None:
        """The deleted-profile row, the global value row and the 7.3 reasons of an unavailable encounter."""
        self.assertIn("deletedProfile: (id) => `${id} (deleted profile)`", self.words)
        self.assertEqual(words.DELETED_PROFILE.replace("{profile_id}", "${id}"), "${id} (deleted profile)")
        self.assertIn(f"`{words.GLOBAL.split('{')[0]}${{", self.words)
        self.assertIn(f"`{words.GLOBAL_APPLIED.split('{')[0]}${{", self.words)
        self.assertEqual(literal(self.encounters, "banditReason"), words.BANDIT_REASON)
        self.assertEqual(literal(self.encounters, "wolfBearReason"), words.WOLF_BEAR_REASON)
        self.assertIn("notOffered: (event) => `" + words.NOT_OFFERED.replace("{event}", "${event}") + "`",
                      self.encounters)


class CliTextTests(unittest.TestCase):
    """The CLI lines and the late-change text are the Product Owner texts; options are text to type."""

    def test_cli_lines_word_for_word(self) -> None:
        """Each CLI line equals the independent copy of the Product Owner entry."""
        for name, text in CLI_TEXTS.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(words, name), text)

    def test_late_change_text_of_the_backend(self) -> None:
        """The backend text 7c names the file; the CLI recognizes it by its start."""
        self.assertEqual(TARGET_CHANGED_TEXT, LATE_CHANGE)
        self.assertTrue(TARGET_CHANGED_TEXT.startswith(words.LATE_CHANGE_START))

    def test_filled_lines_type_the_options_with_their_values(self) -> None:
        """An option with its value is text to type; a value alone is data or an echo of the command line."""
        line = words.filled(words.FILE_CHOICE, path="mapgrouppos.xml")
        self.assertEqual(line_text(line), CLI_TEXTS["FILE_CHOICE"].replace("{path}", "mapgrouppos.xml"))
        self.assertEqual([part.text for part in line.parts if isinstance(part, TypeText)],
                         ["--keep-change mapgrouppos.xml", "--replace-file mapgrouppos.xml"])
        line = words.filled(words.MISSING_CHOICE)
        self.assertEqual([part.text for part in line.parts if isinstance(part, TypeText)],
                         ["--keep-change PATH", "--replace-file PATH", "--exclude-encounter ID"])
        line = words.filled(words.IN_BOTH, path=Echo("a.xml"))
        self.assertEqual((line.parts[0], line_text(line)), (Echo("a.xml"), CLI_TEXTS["IN_BOTH"].format(path="a.xml")))
        line = words.filled(words.ENCOUNTER_CHOICE, id="e1")
        self.assertIn(TypeText("--exclude-encounter e1"), line.parts)
        self.assertEqual(words.filled("{value}", value="x").parts, (Value("x"),))


if __name__ == "__main__":
    unittest.main()
