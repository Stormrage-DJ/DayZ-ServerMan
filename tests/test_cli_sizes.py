"""Criterion 22: every file of the CLI package and the new wording module stays at or below 300 lines (task 2.5)."""

from __future__ import annotations

import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "runnable" / "src" / "python" / "dayz_serverman"
# Completion gate of the source-file size rule
LINE_LIMIT = 300


class CliSizeTests(unittest.TestCase):
    """The size gate of design 6.1 for `cli/` and the Python phase wording of 11.1."""

    def test_cli_files_are_at_most_300_lines(self) -> None:
        """Every Python file under `cli/`, also in `cli/commands/`, has at most 300 lines."""
        paths = sorted((PACKAGE / "cli").rglob("*.py"))
        self.assertGreaterEqual(len(paths), 10)
        for path in paths + [PACKAGE / "application" / name for name in ("phase_wording.py", "field_wording.py")]:
            with self.subTest(path=path.relative_to(PACKAGE).as_posix()):
                lines = len(path.read_text(encoding="utf-8").splitlines())
                self.assertLessEqual(lines, LINE_LIMIT, f"{path.name} has {lines} lines")


if __name__ == "__main__":
    unittest.main()
