"""Profile v2 backup tests for schema, runtime, and fail-closed sources."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
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
)
from dayz_serverman.repositories.backup_sources import copy_verified as real_copy_verified  # noqa: E402
from dayz_serverman.repositories.backups import BackupStorage, BackupStorageError  # noqa: E402
from tests.test_backups import (  # noqa: E402
    FakeProfiles,
    FakeSettings,
    create_runtime_profile,
    record,
)


class BackupProfileV2Tests(unittest.TestCase):
    """Profile-aware backup contracts for schema and source validation."""
    def setUp(self) -> None:
        """Create a Unicode dayz tree with a runtime profile and config."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_backup_v2_")
        self.root = Path(self.temporary.name)
        self.dayz = self.root / "DayZ Unicode"
        config = self.dayz / "Config Files" / "serverDZ.cfg"
        config.parent.mkdir(parents=True)
        config.write_text('hostname = "fixture";\nclass Missions { class DayZ { template = "dayzOffline.chernarusplus"; }; };\n', encoding="utf-8")
        self.runtime = create_runtime_profile(self.dayz)
        mission = self.dayz / "mpmissions" / "dayzOffline.chernarusplus"
        mission.mkdir(parents=True)
        (mission / "init.c").write_text("fixture", encoding="utf-8")
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.settings = FakeSettings(self.dayz, self.backups)

    def tearDown(self) -> None:
        """Remove the temporary backup tree."""
        self.temporary.cleanup()

    def service(self, profile=None, storage=None) -> BackupService:
        """Build a backup service over the test settings and clock."""
        return BackupService(
            FakeProfiles(profile or record()),  # type: ignore[arg-type]
            self.settings,  # type: ignore[arg-type]
            storage or BackupStorage(),
            clock=lambda: datetime(2026, 9, 25, 18, 0, tzinfo=UTC),
            identifier=lambda: "profile-v2",
        )

    def test_manifest_v1_v2_bool_and_future_contracts(self) -> None:
        """Manifest parsing accepts v1 and v2 and rejects booleans and future schemas."""
        entry = ManifestEntry("payload/config.cfg", 1, "a" * 64)
        legacy = BackupManifest(
            "legacy", "main", 1, 1, "2026-09-25T12:00:00Z", (entry,),
            schema_version=1,
        ).signed()
        self.assertEqual(BackupManifest.parse(legacy.to_dict()).schema_version, 1)
        current = BackupManifest(
            "current", "main", 2, 3, "2026-09-25T12:00:00Z", (entry,),
            semantic_profile_digest="b" * 64,
            runtime_profile="profiles\\main",
        ).signed()
        parsed = BackupManifest.parse(current.to_dict())
        self.assertEqual(parsed.semantic_profile_digest, "b" * 64)
        self.assertEqual(parsed.runtime_profile, "profiles\\main")
        for version in (True, False, 3):
            value = current.to_dict()
            value["schema_version"] = version
            with self.subTest(version=version), self.assertRaises(BackupManifestError):
                BackupManifest.parse(value)

    def test_legacy_directory_backup_is_ignored_and_not_restorable(self) -> None:
        """Legacy directory backups are ignored and refuse restore lookup."""
        snapshot = self.backups / "snapshots" / "legacy"
        payload = snapshot / "payload" / "config.cfg"
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b"x")
        manifest = BackupManifest(
            "legacy", "main", 1, 1, "2026-09-25T12:00:00Z",
            (ManifestEntry("payload/config.cfg", 1,
                           "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"),),
            schema_version=1,
        ).signed()
        (snapshot / "manifest.json").write_text(
            json.dumps(manifest.to_dict(), sort_keys=True), encoding="utf-8",
        )
        history = BackupStorage().history(self.backups, "main")
        self.assertEqual(history["backups"], [])
        self.assertEqual(history["legacy_backups"], [])
        storage = BackupStorage()
        with self.assertRaisesRegex(BackupStorageError, "unavailable") as raised:
            storage.verified_manifest(self.backups, "legacy", "main")
        self.assertEqual(raised.exception.code, "BACKUP_SOURCE_INVALID")

    def test_runtime_required_recursed_and_mod_directories_excluded(self) -> None:
        """Runtime copies recurse while mod directories stay out of the archive."""
        mod = self.dayz / "@Never Copy"
        mod.mkdir()
        (mod / "large.pbo").write_bytes(b"mod")
        # A profile without a runtime path must be refused
        with self.assertRaises(BackupStorageError) as raised:
            self.service(record(False, None)).create("main", 3, 4, lambda *_: None)
        self.assertEqual(raised.exception.code, "RUNTIME_PROFILE_UNRESOLVED")
        created = self.service().create("main", 3, 4, lambda *_: None)
        self.assertEqual(created["restore_compatibility"], "COMPATIBLE")
        # The archived manifest must include runtime files but never mods
        with zipfile.ZipFile(self.backups / f"{created['backup_id']}.zip") as archive:
            manifest = json.loads(archive.read("manifest.json"))
        paths = [entry["path"] for entry in manifest["entries"]]
        self.assertTrue(any(path.startswith("runtime-profile/") for path in paths))
        self.assertFalse(any("Never Copy" in path for path in paths))
        self.assertEqual(manifest["semantic_profile_digest"], record().semantic_digest)
        history = self.service().history("main")
        summary = history["backups"][0]
        self.assertEqual(summary["status"], "USABLE")
        self.assertEqual(summary["restore_compatibility"], "COMPATIBLE")

    def test_runtime_change_during_copy_fails_and_cleans_staging(self) -> None:
        """A runtime change during copy fails and removes staging."""
        changed = False

        def mutate(source, target):
            """Change the runtime file while its copy is in progress."""
            nonlocal changed
            if not changed and source.entry_path.startswith("runtime-profile/"):
                changed = True
                source.source.write_text("changed during copy", encoding="utf-8")
            real_copy_verified(source, target)

        with patch("dayz_serverman.repositories.backups.copy_verified", side_effect=mutate):
            with self.assertRaises(BackupStorageError) as raised:
                self.service().create("main", 3, 4, lambda *_: None)
        self.assertEqual(raised.exception.code, "BACKUP_SOURCE_CHANGED")
        self.assertFalse(any(self.backups.glob("*.zip")))
        self.assertFalse(any(self.backups.glob(".staging-*")))

    def test_missing_runtime_and_windows_case_collision_fail_before_destination(self) -> None:
        """Missing runtime paths and Windows case collisions fail before the destination."""
        missing = record(True, "profiles\\missing")
        with self.assertRaises(BackupStorageError) as raised:
            self.service(missing).create("main", 3, 4, lambda *_: None)
        self.assertEqual(raised.exception.code, "BACKUP_SOURCE_INVALID")
        collision_file = self.runtime / "Case.txt"
        collision_file.write_text("same Windows file", encoding="utf-8")
        real_walk = __import__("os").walk

        def colliding_walk(path, followlinks=False):
            """Report case-colliding runtime files during the source walk."""
            if Path(path) == self.runtime.resolve():
                yield str(path), ["nested"], ["Case.txt", "case.TXT", "settings.json"]
                yield str(Path(path) / "nested"), [], ["Állapot.txt"]
                return
            yield from real_walk(path, followlinks=followlinks)

        with patch("dayz_serverman.repositories.backup_sources.os.walk", side_effect=colliding_walk):
            with self.assertRaises(BackupStorageError) as collision:
                self.service().create("main", 3, 4, lambda *_: None)
        self.assertEqual(collision.exception.code, "BACKUP_SOURCE_COLLISION")
        self.assertFalse(any(self.backups.glob("*.zip")))

    def test_runtime_link_is_rejected_when_supported(self) -> None:
        """A linked runtime directory is rejected when links are supported."""
        outside = self.root / "outside"
        outside.mkdir()
        link = self.runtime / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("directory symlink creation is unavailable")
        with self.assertRaises(BackupStorageError) as raised:
            self.service().create("main", 3, 4, lambda *_: None)
        self.assertEqual(raised.exception.code, "PATH_INVALID")

    def test_dayz_root_link_is_rejected_when_supported(self) -> None:
        """A linked dayz root is rejected when links are supported."""
        linked_root = self.root / "linked-dayz"
        try:
            linked_root.symlink_to(self.dayz, target_is_directory=True)
        except OSError:
            self.skipTest("directory symlink creation is unavailable")
        linked_settings = FakeSettings(linked_root, self.backups)
        service = BackupService(
            FakeProfiles(record()), linked_settings, BackupStorage(),  # type: ignore[arg-type]
        )
        with self.assertRaises(BackupStorageError) as raised:
            service.create("main", 3, 4, lambda *_: None)
        self.assertEqual(raised.exception.code, "PATH_INVALID")


if __name__ == "__main__":
    unittest.main()
