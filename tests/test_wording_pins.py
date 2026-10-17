"""Pin the Product Owner texts of task 10 against independent literal copies (QA-10.31-F3).

Each production constant must equal its copy in `wording_pins.py`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.cli import help_wording as help_texts  # noqa: E402
from dayz_serverman.cli import map_wording as cli_texts  # noqa: E402
from dayz_serverman.domain import mission_map_adoption_texts as adoption_texts  # noqa: E402
from dayz_serverman.domain import restore_editor_texts as restore_texts  # noqa: E402
from tests import wording_pins as pins  # noqa: E402


class WordingPinTests(unittest.TestCase):
    """A changed word in any listed text fails one of these tests."""

    def check(self, module: object, copies: dict) -> None:
        """Compare each copy with the constant of the same name."""
        self.assertTrue(copies)
        for name, text in copies.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(module, name), text)

    def test_restore_editor_sentences(self) -> None:
        """The backup and restore editor sentences: rows, runtime forms, skips, corrupt and set-aside sentences."""
        self.check(restore_texts, pins.RESTORE_EDITOR)

    def test_adoption_texts(self) -> None:
        """ADOPT-7, 8, 12, 13, 15, 16, 18, 24, 25, 28 and 29 with their route variants and rows."""
        self.check(adoption_texts, pins.ADOPTION)

    def test_cli_texts(self) -> None:
        """The map show lines, the export refusals, the plural left-out form and the adoption lines."""
        self.check(cli_texts, pins.CLI)

    def test_help_texts(self) -> None:
        """The command texts of the map commands and the option texts of the two file choices."""
        for name, text in pins.CLI_HELP.items():
            with self.subTest(command=name):
                self.assertEqual(help_texts.COMMAND_TEXTS[name], text)
        for name, text in pins.CLI_OPTIONS.items():
            with self.subTest(option=name):
                self.assertEqual(help_texts.OPTION_TEXTS[name], text)


if __name__ == "__main__":
    unittest.main()
