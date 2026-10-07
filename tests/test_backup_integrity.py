"""Manifest integrity tests before and after backup publication."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import warnings
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.backups import BackupService  # noqa: E402
from dayz_serverman.domain.backups import (  # noqa: E402
    BackupManifest,
    BackupManifestError,
    ManifestEntry,
    entry_path_key,
)
from dayz_serverman.repositories.backups import BackupStorage, BackupStorageError  # noqa: E402
from tests.test_backups import FakeProfiles, FakeSettings, create_runtime_profile, record  # noqa: E402


class BackupIntegrityTests(unittest.TestCase):
    """Manifest ordering, re-read, and filtering integrity contracts."""
    def setUp(self) -> None:
        """Create the dayz fixture tree and one backup destination."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_backup_integrity_")
        self.root = Path(self.temporary.name)
        self.dayz = self.root / "DáyZ"
        config = self.dayz / "Config Files" / "serverDZ.cfg"
        config.parent.mkdir(parents=True)
        config.write_text('hostname = "Test";\nclass Missions { class DayZ { template = "dayzOffline.chernarusplus"; }; };\n', encoding="utf-8")
        create_runtime_profile(self.dayz)
        mission = self.dayz / "mpmissions" / "dayzOffline.chernarusplus"
        mission.mkdir(parents=True)
        (mission / "init.c").write_text("fixture", encoding="utf-8")
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.settings = FakeSettings(self.dayz, self.backups)
        self.service = BackupService(
            FakeProfiles(record()), self.settings, BackupStorage(),
            clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
            identifier=lambda: "integrity",
        )

    def tearDown(self) -> None:
        """Remove the temporary backup tree."""
        self.temporary.cleanup()

    def test_manifest_uses_windows_casefold_order_and_rejects_collisions(self) -> None:
        """Manifest entries sort under Windows case folding and reject collisions."""
        paths = ("payload/Z.cfg", "payload/ä.cfg", "payload/É.cfg")
        entries = tuple(ManifestEntry(path, 1, "a" * 64) for path in sorted(paths, key=entry_path_key))
        manifest = BackupManifest(
            "ordered", "main", 3, 4, "2026-09-25T12:34:56.000Z", entries,
            semantic_profile_digest="c" * 64, runtime_profile="profiles\\main",
        ).signed()
        self.assertEqual(BackupManifest.parse(manifest.to_dict()).entries, entries)
        for pair in (("payload/A.cfg", "payload/a.cfg"), ("payload/Ä.cfg", "payload/ä.cfg")):
            with self.subTest(pair=pair):
                collision = BackupManifest(
                    "collision", "main", 3, 4, "2026-09-25T12:34:56.000Z",
                    tuple(ManifestEntry(path, 1, "b" * 64) for path in pair),
                    semantic_profile_digest="c" * 64, runtime_profile="profiles\\main",
                ).signed()
                with self.assertRaisesRegex(BackupManifestError, "Windows-safe"):
                    BackupManifest.parse(collision.to_dict())

    def test_persisted_manifest_is_reread_before_and_after_publication(self) -> None:
        """Staged and published manifests are re-read and tampering is caught."""
        def tamper_staging(phase: str) -> None:
            """Rewrite the staged manifest revision before publication."""
            if phase == "WRITE_MANIFEST":
                path = next(self.backups.glob(".staging-*/manifest.json"))
                value = json.loads(path.read_text(encoding="utf-8"))
                value["profile_revision"] = 99
                path.write_text(json.dumps(value), encoding="utf-8")

        # Tamper the staged manifest before the storage re-reads it
        staging_service = BackupService(
            FakeProfiles(record()), self.settings, BackupStorage(phase_hook=tamper_staging),
            clock=lambda: datetime(2026, 9, 25, tzinfo=UTC), identifier=lambda: "stage-tamper",
        )
        with self.assertRaisesRegex(BackupStorageError, "staged backup") as staging_error:
            staging_service.create("main", 3, 4, lambda _phase, _percent: None)
        self.assertEqual(staging_error.exception.code, "BACKUP_INTEGRITY_FAILED")
        self.assertFalse(any(self.backups.glob("*.zip")))

        # Tamper the archive after the atomic replace completes
        real_replace = os.replace
        def tamper_after_replace(source: object, target: object) -> None:
            """Append a bogus manifest entry after the atomic replace."""
            real_replace(source, target)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(Path(target), "a") as archive:
                    archive.writestr("manifest.json", '{"schema_version":2}')

        with patch("dayz_serverman.repositories.backups.replace_file", side_effect=tamper_after_replace):
            with self.assertRaisesRegex(BackupStorageError, "published backup ZIP") as raised:
                self.service.create("main", 3, 4, lambda _phase, _percent: None)
        self.assertEqual(raised.exception.code, "RECOVERY_REQUIRED")
        history = self.service.history("main")
        self.assertEqual(history["backups"], [])
        self.assertEqual(history["diagnostics"][0]["scope"], "DESTINATION")

    def test_other_profile_is_filtered_before_payload_verification(self) -> None:
        """Foreign profile archives are filtered out with destination diagnostics."""
        storage = BackupStorage()
        source = storage.source(self.dayz, "Config Files\\serverDZ.cfg")
        storage.create(
            self.backups, "other-backup", "other", 1, 4,
            "2026-09-25T12:34:56.000Z", "d" * 64, "profiles\\main",
            (source,), lambda _phase, _percent: None,
        )
        archive = self.backups / "other-backup.zip"
        temporary = self.backups / "other-backup.rewrite"
        with zipfile.ZipFile(archive, "r") as source_zip, zipfile.ZipFile(temporary, "w") as output:
            for info in source_zip.infolist():
                data = source_zip.read(info)
                if info.filename == source.entry_path:
                    data = b"tampered other profile"
                output.writestr(info, data)
        temporary.replace(archive)
        (self.backups / "unassignable.zip").write_text("{broken", encoding="utf-8")

        # Foreign and unreadable archives surface as destination diagnostics
        main_history = storage.history(self.backups, "main")
        self.assertEqual(main_history["backups"], [])
        self.assertEqual(len(main_history["diagnostics"]), 1)
        self.assertEqual(main_history["diagnostics"][0]["scope"], "DESTINATION")
        self.assertNotIn("profile_id", main_history["diagnostics"][0])

        other_history = storage.history(self.backups, "other")
        scopes = {(item["scope"], item.get("profile_id")) for item in other_history["diagnostics"]}
        self.assertEqual(scopes, {("DESTINATION", None), ("PROFILE", "other")})


if __name__ == "__main__":
    unittest.main()
