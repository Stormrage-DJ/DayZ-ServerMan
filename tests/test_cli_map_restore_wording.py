"""Leaf 10.32, PO-6 and PO-7: the window copies of `map restore` and `map recover` equal the window files.

The restore texts are in `map_editor_preview_words.js`; 6a, 6b and the start of the late-change text are in
`map_editor_publish_words.js`. The 8.8 texts stay in the backend and are not copied.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from test_cli_map_publish_wording import joined  # noqa: E402
from test_cli_map_wording import source  # noqa: E402

from dayz_serverman.cli import map_restore_wording as words  # noqa: E402
from dayz_serverman.domain.mission_map_restore_texts import TARGET_CHANGED_TEXT  # noqa: E402


class WindowCopyTests(unittest.TestCase):
    """Each copy is the same string in the window file."""

    @classmethod
    def setUpClass(cls) -> None:
        """Read the two window files."""
        cls.words = source("map_editor_preview_words.js")
        cls.publish = source("map_editor_publish_words.js")

    def test_headings_stop_line_and_labels(self) -> None:
        """The five restore headings, the stop-server line, the intro line and the two list labels."""
        for key, copy in (("restoreRefusals", words.REFUSALS_HEADING), ("restoreNotices", words.NOTICES_HEADING),
                          ("writes", words.WRITES_HEADING), ("overwritten", words.OVERWRITTEN_HEADING),
                          ("kept", words.KEPT_HEADING)):
            with self.subTest(key=key):
                self.assertEqual(joined(self.words, key, "headings"), copy)
        self.assertEqual(joined(self.words, "restore", "stop"), words.STOP_SERVER)
        self.assertEqual(joined(self.words, "previous", "others"), words.OTHER_PROFILES)
        self.assertEqual(joined(self.words, "restoreAffected"), words.AFFECTED)
        self.assertEqual(joined(self.words, "restoreCoordinated"), words.COORDINATED)

    def test_texts_with_values(self) -> None:
        """What the restore writes, the change names and the not-overwritten line, with their placeholders."""
        start = "The restore writes the mission files as they were before the last recorded "
        self.assertIn(f'"{start}" +\n    `change: ${{mapPreviewWordsFixed.changes[kind] ?? kind}}, '
                      "${mapPreviewWordsTime(time)}.`", self.words)
        self.assertEqual(words.PREVIOUS_WRITES, start + "change: {change}, {time}.")
        self.assertIn("originalWrites: (time) => `" + words.ORIGINAL_WRITES.replace("{time}", "${mapPreviewWordsTime(time)}")
                      + "`", self.words)
        self.assertIn("notOverwritten: (path) => `" + words.NOT_OVERWRITTEN.replace("{path}", "${path}") + "`",
                      self.words)
        for kind, name in words.CHANGES.items():
            with self.subTest(kind=kind):
                self.assertEqual(joined(self.words, kind, "changes"), name)

    def test_recover_texts_and_the_late_change_start(self) -> None:
        """6a and 6b word for word (PO-7); the CLI recognizes the 8.8 late-change text by its start."""
        self.assertEqual(joined(self.publish, "cleared"), words.CLEARED)
        self.assertEqual(joined(self.publish, "deferred"), words.DEFERRED)
        self.assertIn(f'const mapPublishRestoreStopped = "{words.LATE_CHANGE_START}";', self.publish)
        self.assertTrue(TARGET_CHANGED_TEXT.startswith(words.LATE_CHANGE_START))



class CliTextTests(unittest.TestCase):
    """The CLI line of an invalid mode is the Product Owner text (2026-10-17 02:14:51 in evidence 10.32)."""

    def test_invalid_mode_line_word_for_word(self) -> None:
        """The filled line equals an independent copy; it is exempt from the window comparison (PO-6)."""
        original, previous = words.INVALID_MODE_COMMANDS
        self.assertEqual(words.INVALID_MODE_LINE.format(original=original, previous=previous),
                         "Type map restore original or map restore previous.")


if __name__ == "__main__":
    unittest.main()
