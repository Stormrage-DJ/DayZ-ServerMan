"""Find undefined names in first-party Python before they fail at run time."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

try:
    from pyflakes import api, messages
except ImportError:  # pragma: no cover - depends on the development tools
    api = messages = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# The application source, the starter scripts, and the tests
SOURCE_GLOBS = (
    (PROJECT_ROOT / "runnable" / "src" / "python", "**/*.py"),
    (PROJECT_ROOT / "runnable", "*.py"),
    (PROJECT_ROOT / "tests", "**/*.py"),
)
INSTALL_HINT = "pyflakes is missing: run pip install -r requirements-dev.txt"


class _Collector:
    """Collect pyflakes results in the shape of a pyflakes reporter."""

    def __init__(self) -> None:
        """Start with no findings."""
        self.findings: list[str] = []

    def flake(self, message: object) -> None:
        """Keep the messages that name an undefined variable, export, or star import."""
        # A star import hides undefined names, so it fails the check as well
        if isinstance(message, (messages.UndefinedName, messages.UndefinedExport, messages.UndefinedLocal,
                                messages.ImportStarUsed, messages.ImportStarUsage)):
            self.findings.append(str(message))

    def syntaxError(self, filename: str, msg: str, lineno: int, offset: int, text: str) -> None:
        """Report a file that does not parse."""
        self.findings.append(f"{filename}:{lineno}: syntax error: {msg}")

    def unexpectedError(self, filename: str, msg: str) -> None:
        """Report a file that cannot be read."""
        self.findings.append(f"{filename}: {msg}")


def _source_files() -> list[Path]:
    """Return every first-party Python file, without caches."""
    files = {path for root, pattern in SOURCE_GLOBS for path in root.glob(pattern)}
    return sorted(path for path in files if "__pycache__" not in path.parts)


class UndefinedNameTests(unittest.TestCase):
    """Verify that no first-party module uses a name it never defines or imports."""

    def setUp(self) -> None:
        """Require pyflakes in CI and skip without it on a local machine."""
        if api is None:
            # GitHub Actions sets CI, and its workflow installs the development tools
            if os.environ.get("CI") == "true":
                self.fail(INSTALL_HINT)
            self.skipTest(INSTALL_HINT)

    def test_checker_reports_an_undefined_name(self) -> None:
        """Verify the filter keeps an undefined name and drops an unused import."""
        collector = _Collector()
        api.check("import os\n\ndef broken():\n    return missing_name\n", "sample.py", collector)
        self.assertEqual(len(collector.findings), 1)
        self.assertIn("undefined name 'missing_name'", collector.findings[0])

    def test_first_party_python_has_no_undefined_names(self) -> None:
        """Verify every first-party Python file defines or imports each name it uses."""
        files = _source_files()
        self.assertGreater(len(files), 100)
        collector = _Collector()
        # Check each file separately so every finding names its file and line
        for path in files:
            api.checkPath(str(path), collector)
        self.assertEqual(collector.findings, [])


if __name__ == "__main__":
    unittest.main()
