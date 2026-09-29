"""Settings diagnostics tests for path roles, validation, and revisions."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.adapters.windows.diagnostics import WindowsPathDiagnostics  # noqa: E402
from dayz_serverman.application.settings import (  # noqa: E402
    SettingsValidationError,
    normalize_settings,
)
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.models import (  # noqa: E402
    PathRole,
    PathStatus,
    RevisionConflict,
    SettingsInput,
)


class SettingsDiagnosticsTests(unittest.TestCase):
    """Path diagnostics contracts for settings validation and persistence."""
    def setUp(self) -> None:
        """Create a manager composition under a temporary root."""
        self.temporary = tempfile.TemporaryDirectory(prefix="ServerMan settings ő ")
        self.root = Path(self.temporary.name)
        self.composition = build_composition(self.root / "Manager Root")

    def tearDown(self) -> None:
        """Remove the temporary tree."""
        self.temporary.cleanup()

    def fixture_settings(self, custom_backup: bool = False) -> SettingsInput:
        """Create a valid settings input with all fixture paths present."""
        dayz = self.root / "DayZ Server ő"
        steamcmd = self.root / "Steam CMD"
        workshop = self.root / "Workshop Content"
        backup = self.root / "Custom Backups"
        # Create the fixture directories before the settings are saved
        for directory in (dayz, steamcmd, workshop, backup):
            directory.mkdir(exist_ok=True)
        dayz_executable = dayz / "DayZServer_x64.exe"
        steam_executable = steamcmd / "steamcmd.exe"
        dayz_executable.write_bytes(b"fixture")
        steam_executable.write_bytes(b"fixture")
        return SettingsInput(
            dayz_root=str(dayz),
            dayz_executable=str(dayz_executable),
            steamcmd_root=str(steamcmd),
            steamcmd_executable=str(steam_executable),
            workshop_content_root=str(workshop),
            custom_backup_root=str(backup) if custom_backup else None,
        )

    def test_unconfigured_roles_are_actionable_and_default_backup_is_ready(self) -> None:
        """Unconfigured roles are actionable and the default backup root is ready."""
        diagnostics = {item.role: item for item in self.composition.settings.diagnostics()}
        self.assertEqual(diagnostics[PathRole.DAYZ_ROOT].status, PathStatus.UNCONFIGURED)
        self.assertIn("Select", diagnostics[PathRole.DAYZ_ROOT].action)
        self.assertEqual(diagnostics[PathRole.BACKUP_ROOT].status, PathStatus.READY)
        self.assertEqual(
            Path(diagnostics[PathRole.BACKUP_ROOT].configured_path),
            self.composition.paths.backups,
        )

    def test_save_normalizes_unicode_paths_and_persists_revision(self) -> None:
        """Saving normalizes Unicode paths and persists the new revision."""
        saved = self.composition.settings.save(self.fixture_settings(), None)
        self.assertEqual(saved.revision, 0)
        self.assertTrue(Path(saved.dayz_root).is_absolute())
        self.assertIn("ő", saved.dayz_root)
        self.assertTrue(
            all(item.status == PathStatus.READY for item in self.composition.settings.diagnostics(saved))
        )
        self.assertEqual(self.composition.settings.load(), saved)

    def test_relative_external_path_is_rejected_without_cwd_fallback(self) -> None:
        """Relative paths are rejected without falling back to the working directory."""
        with self.assertRaisesRegex(SettingsValidationError, "must be absolute"):
            self.composition.settings.save(SettingsInput(dayz_root="DayZServer"), None)

    def test_executable_must_remain_inside_its_configured_root(self) -> None:
        """An executable outside its configured root is rejected."""
        values = self.fixture_settings()
        outside = self.root / "outside.exe"
        outside.write_bytes(b"fixture")
        with self.assertRaisesRegex(SettingsValidationError, "inside dayz_root"):
            self.composition.settings.save(
                SettingsInput(**{**values.__dict__, "dayz_executable": str(outside)}),
                None,
            )

    def test_distinct_drive_paths_normalize_without_cross_role_fallback(self) -> None:
        """Distinct drive paths normalize without cross-role fallbacks."""
        values = normalize_settings(
            SettingsInput(
                dayz_root=r"D:\Game Servers\DayZ ő",
                dayz_executable=r"D:\Game Servers\DayZ ő\DayZServer_x64.exe",
                steamcmd_root=r"E:\Tools\Steam CMD",
                steamcmd_executable=r"E:\Tools\Steam CMD\steamcmd.exe",
                workshop_content_root=r"F:\Workshop\221100",
                custom_backup_root=r"G:\Backups\DayZ",
            )
        )
        self.assertEqual(Path(values.dayz_root).drive.casefold(), "d:")
        self.assertEqual(Path(values.steamcmd_root).drive.casefold(), "e:")
        self.assertEqual(Path(values.workshop_content_root).drive.casefold(), "f:")
        self.assertEqual(Path(values.custom_backup_root).drive.casefold(), "g:")

    def test_missing_and_moved_are_distinct(self) -> None:
        """Missing and moved paths report distinct statuses."""
        saved = self.composition.settings.save(self.fixture_settings(), None)
        dayz_root = Path(saved.dayz_root)
        # Rename the configured root so diagnostics report MOVED
        moved_root = dayz_root.with_name("Moved Away")
        dayz_root.rename(moved_root)
        diagnostics = {item.role: item for item in self.composition.settings.diagnostics(saved)}
        self.assertEqual(diagnostics[PathRole.DAYZ_ROOT].status, PathStatus.MOVED)
        self.assertEqual(diagnostics[PathRole.DAYZ_EXECUTABLE].status, PathStatus.MOVED)

        # A never-validated root reports MISSING
        missing = SettingsInput(dayz_root=str(self.root / "Never Validated"))
        unsaved = normalize_settings(missing)
        fresh = saved.__class__(
            revision=None,
            dayz_root=unsaved.dayz_root,
            dayz_executable=None,
            steamcmd_root=None,
            steamcmd_executable=None,
            workshop_content_root=None,
            custom_backup_root=None,
            last_validated_paths={},
        )
        fresh_diagnostics = {item.role: item for item in self.composition.settings.diagnostics(fresh)}
        self.assertEqual(fresh_diagnostics[PathRole.DAYZ_ROOT].status, PathStatus.MISSING)

    def test_wrong_kind_and_non_writable_are_reported(self) -> None:
        """Wrong kinds and non-writable roots are reported explicitly."""
        values = self.fixture_settings()
        file_as_root = self.root / "not-a-directory"
        file_as_root.write_text("fixture", encoding="utf-8")
        directory_as_executable = self.root / "not-a-file"
        directory_as_executable.mkdir()
        settings = self.composition.settings.save(values, None)
        # Swap in a file root and a directory executable
        altered = settings.__class__(
            **{
                **settings.__dict__,
                "dayz_root": str(file_as_root),
                "dayz_executable": str(directory_as_executable),
            }
        )
        adapter = WindowsPathDiagnostics(writable_check=lambda path: False)
        diagnostics = {item.role: item for item in adapter.inspect(altered, self.composition.paths.backups)}
        self.assertEqual(diagnostics[PathRole.DAYZ_ROOT].status, PathStatus.NOT_DIRECTORY)
        self.assertEqual(diagnostics[PathRole.DAYZ_EXECUTABLE].status, PathStatus.NOT_FILE)
        self.assertEqual(diagnostics[PathRole.STEAMCMD_ROOT].status, PathStatus.NOT_WRITABLE)

    def test_network_and_reparse_paths_are_rejected_without_guessing(self) -> None:
        """Network and reparse paths are rejected without guessing."""
        saved = self.composition.settings.save(self.fixture_settings(custom_backup=True), None)
        # Simulate network and reparse points without real mounts
        adapter = WindowsPathDiagnostics(
            network_check=lambda path: path.name == "Custom Backups",
            reparse_check=lambda path: path.name == "Workshop Content",
        )
        diagnostics = {item.role: item for item in adapter.inspect(saved, self.composition.paths.backups)}
        self.assertEqual(diagnostics[PathRole.BACKUP_ROOT].status, PathStatus.UNSUPPORTED_NETWORK)
        self.assertEqual(
            diagnostics[PathRole.WORKSHOP_CONTENT_ROOT].status,
            PathStatus.UNSUPPORTED_REPARSE,
        )
        self.assertIn("Choose", diagnostics[PathRole.BACKUP_ROOT].action)

    def test_revision_conflict_preserves_saved_settings(self) -> None:
        """A revision conflict preserves the saved settings."""
        first = self.composition.settings.save(self.fixture_settings(), None)
        with self.assertRaises(RevisionConflict):
            self.composition.settings.save(self.fixture_settings(custom_backup=True), 99)
        self.assertEqual(self.composition.settings.load(), first)

    def test_backup_change_never_moves_or_deletes_default_backups(self) -> None:
        """Changing the backup root never moves default backups."""
        marker = self.composition.paths.backups / "existing.zip"
        marker.write_text("preserve", encoding="utf-8")
        first = self.composition.settings.save(self.fixture_settings(), None)
        changed = self.composition.settings.save(self.fixture_settings(custom_backup=True), first.revision)
        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
        self.assertEqual(
            self.composition.settings.backup_root(changed),
            Path(changed.custom_backup_root),
        )


if __name__ == "__main__":
    unittest.main()
