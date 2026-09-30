"""Mission discovery and custom-selection safety tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.application.mission_catalog import (  # noqa: E402
    MissionCatalog,
    MissionCatalogError,
)
from dayz_serverman.domain.profiles import ProfileValidationError  # noqa: E402


class MissionCatalogTests(unittest.TestCase):
    """Discover installed missions and reject unsafe custom choices."""

    def setUp(self) -> None:
        """Create a disposable DayZ-like installation."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_missions_")
        self.root = Path(self.temporary.name) / "DayZ"
        self.missions = self.root / "mpmissions"
        self.missions.mkdir(parents=True)
        self.catalog = MissionCatalog()

    def tearDown(self) -> None:
        """Remove the disposable installation."""
        self.temporary.cleanup()

    def test_lists_installed_missions_in_stable_order(self) -> None:
        """Return official and custom mission directories without hard-coding names."""
        (self.missions / "Pripyat.Custom").mkdir()
        (self.missions / "dayzOffline.chernarusplus").mkdir()
        (self.missions / "readme.txt").write_text("not a mission", encoding="utf-8")

        choices = self.catalog.list(str(self.root))

        self.assertEqual(
            [item.relative_path for item in choices],
            [r"mpmissions\dayzOffline.chernarusplus", r"mpmissions\Pripyat.Custom"],
        )
        self.assertEqual([item.display_name for item in choices], [
            "dayzOffline.chernarusplus", "Pripyat.Custom",
        ])

    def test_validates_existing_custom_mission(self) -> None:
        """Accept an installed custom mission as a canonical relative choice."""
        (self.missions / "Pripyat").mkdir()

        choice = self.catalog.validate(str(self.root), r"mpmissions\Pripyat")

        self.assertEqual(choice.relative_path, r"mpmissions\Pripyat")
        self.assertEqual(choice.source, "custom")

    def test_rejects_missing_and_escaping_custom_paths(self) -> None:
        """Do not accept missing content or paths outside the DayZ root."""
        with self.assertRaisesRegex(ProfileValidationError, "installed mission"):
            self.catalog.validate(str(self.root), r"mpmissions\missing")
        with self.assertRaisesRegex(ProfileValidationError, "traversing"):
            self.catalog.validate(str(self.root), r"mpmissions\..\elsewhere")

    def test_missing_mission_root_returns_empty_catalog(self) -> None:
        """An installation without mpmissions has no selectable missions."""
        other = Path(self.temporary.name) / "Empty DayZ"
        other.mkdir()
        self.assertEqual(self.catalog.list(str(other)), ())

    def test_missing_dayz_root_is_actionable(self) -> None:
        """Reject an unavailable installation instead of inventing choices."""
        with self.assertRaisesRegex(MissionCatalogError, "unavailable"):
            self.catalog.list(str(self.root / "missing"))


if __name__ == "__main__":
    unittest.main()
