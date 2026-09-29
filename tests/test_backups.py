"""Backup service tests for naming, inventory, guards, and history."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import warnings
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.backups import BackupService, MISSION_INVENTORY  # noqa: E402
from dayz_serverman.application.operations.models import OperationCancelled  # noqa: E402
from dayz_serverman.domain.backups import (  # noqa: E402
    BackupManifest,
    BackupManifestError,
    ManifestEntry,
    manifest_digest,
)
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord  # noqa: E402
from dayz_serverman.repositories.backups import BackupStorage, BackupStorageError  # noqa: E402


class FakeProfiles:
    """Profile port stub serving one fixed record."""
    def __init__(self, record: ProfileRecord) -> None:
        """Store the record returned by every read."""
        self.record = record

    def read(self, _profile_id: object) -> ProfileRecord:
        """Return the stored record regardless of the requested id."""
        return self.record


class FakeSettings:
    """Settings stub exposing a fixed root and backup destination."""
    def __init__(self, dayz: Path, default: Path, custom: Path | None = None) -> None:
        """Build the settings value from the dayz root and destination paths."""
        self.value = SimpleNamespace(
            dayz_root=str(dayz), revision=4,
            custom_backup_root=str(custom) if custom else None,
        )
        self.default = default

    def load(self) -> object:
        """Return the fixed settings value."""
        return self.value

    def backup_root(self, settings: object | None = None) -> Path:
        """Return the custom backup root or the default destination."""
        current = settings or self.value
        return Path(current.custom_backup_root) if current.custom_backup_root else self.default


def record(mission: bool = True, runtime_profile: str | None = "profiles\\main") -> ProfileRecord:
    """Build the fixed profile record used by the backup tests."""
    return ProfileRecord(3, ProfileInput(
        "main", "Máin Profile", "DayZServer_x64.exe", "Config Files\\serverDZ.cfg",
        "mpmissions\\dayzOffline.chernarusplus" if mission else None,
        2302, (), (), runtime_profile,
    ))


def create_runtime_profile(dayz: Path, relative: str = "profiles\\main") -> Path:
    """Create the runtime profile tree with nested Unicode content."""
    runtime = dayz.joinpath(*relative.split("\\"))
    (runtime / "nested").mkdir(parents=True, exist_ok=True)
    (runtime / "settings.json").write_text('{"fixture":true}\n', encoding="utf-8")
    (runtime / "nested" / "Állapot.txt").write_text("runtime\n", encoding="utf-8")
    return runtime


class BackupTests(unittest.TestCase):
    """Backup creation, guard, and history contracts for the default destination."""
    def setUp(self) -> None:
        """Create the portable manager, dayz tree, and mission inventory."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_backup_")
        self.root = Path(self.temporary.name)
        self.dayz = self.root / "DáyZ Root With Spaces"
        self.backups = self.root / "Portable Manager" / "backups"
        self.backups.mkdir(parents=True)
        # Seed the server config and the runtime profile
        server = self.dayz / "Config Files" / "serverDZ.cfg"
        server.parent.mkdir(parents=True)
        server.write_text("hostname = Máin;\n", encoding="utf-8")
        create_runtime_profile(self.dayz)
        # Seed every required mission inventory item
        mission = self.dayz / "mpmissions" / "dayzOffline.chernarusplus"
        for item in MISSION_INVENTORY:
            target = mission.joinpath(*item.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"fixture:{item}\n", encoding="utf-8")
        self.settings = FakeSettings(self.dayz, self.backups)
        self.service = BackupService(
            FakeProfiles(record()),  # type: ignore[arg-type]
            self.settings,  # type: ignore[arg-type]
            BackupStorage(),
            clock=lambda: datetime(2026, 9, 25, 12, 34, 56, tzinfo=UTC),
            identifier=lambda: "fixture001",
            local_timezone=timezone(timedelta(hours=2)),
        )

    def tearDown(self) -> None:
        """Remove the temporary manager and dayz tree."""
        self.temporary.cleanup()

    def create(self, service: BackupService | None = None) -> dict[str, object]:
        """Create one backup and assert the progress phase order."""
        phases: list[str] = []
        result = (service or self.service).create("main", 3, 4,
            lambda phase, _percent: phases.append(phase))
        self.assertEqual(phases, ["DISCOVER", "STAGE", "HASH", "WRITE_MANIFEST", "VERIFY", "PUBLISH"])
        return result

    def test_manifest_is_strict_deterministic_and_digest_bound(self) -> None:
        """Manifest parsing is strict, deterministic, and digest bound."""
        entry = ManifestEntry("payload/config.cfg", 3, "a" * 64)
        manifest = BackupManifest(
            "backup-1", "main", 3, 4, "2026-09-25T12:34:56.000Z", (entry,),
            semantic_profile_digest="b" * 64, runtime_profile="profiles\\main",
        ).signed()
        self.assertEqual(manifest.manifest_digest, manifest_digest(manifest.unsigned_dict()))
        self.assertEqual(BackupManifest.parse(manifest.to_dict()), manifest)
        # Duplicate entries must be refused on parse
        malformed = manifest.to_dict()
        malformed["entries"] = [entry.to_dict(), entry.to_dict()]
        with self.assertRaises(BackupManifestError):
            BackupManifest.parse(malformed)

    def test_default_backup_is_human_named_zip_with_exact_inventory(self) -> None:
        """The default backup is a human-named ZIP with the exact inventory."""
        result = self.create()
        self.assertEqual(result["backup_id"], "main_2026-09-25_14-34-56")
        final = self.backups / f"{result['backup_id']}.zip"
        self.assertTrue(final.is_file())
        with zipfile.ZipFile(final) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        # The manifest must list config, mission, and runtime entries
        expected = ["payload/Config Files/serverDZ.cfg"] + [
            "payload/mpmissions/dayzOffline.chernarusplus/" + item
            for item in MISSION_INVENTORY
        ]
        expected += ["runtime-profile/nested/Állapot.txt", "runtime-profile/settings.json"]
        self.assertEqual([entry["path"] for entry in manifest["entries"]], sorted(expected, key=str.casefold))
        history = self.service.history("main")
        self.assertEqual(history["destination_kind"], "default")
        self.assertEqual([item["backup_id"] for item in history["backups"]], [result["backup_id"]])
        self.assertEqual(history["diagnostics"], [])

    def test_custom_root_and_root_change_never_move_prior_backup(self) -> None:
        """Changing the custom root never moves or hides prior backups."""
        custom = self.root / "Bäckups On Other Drive"
        custom.mkdir()
        # A custom root receives the new snapshot
        self.settings.value.custom_backup_root = str(custom)
        result = self.create()
        original = custom / f"{result['backup_id']}.zip"
        self.assertTrue(original.is_file())
        second = self.root / "Second Root"
        second.mkdir()
        # Switching roots must leave the first snapshot in place
        self.settings.value.custom_backup_root = str(second)
        self.assertEqual(self.service.history("main")["backups"], [])
        self.assertTrue(original.is_file())

    def test_missing_required_source_fails_before_destination_mutation(self) -> None:
        """A missing required source fails before any destination write."""
        (self.dayz / "mpmissions" / "dayzOffline.chernarusplus" / "init.c").unlink()
        with self.assertRaisesRegex(BackupStorageError, "required backup source"):
            self.service.create("main", 3, 4, lambda _phase, _percent: None)
        self.assertFalse(any(self.backups.glob("*.zip")))

    def test_tamper_incomplete_and_future_entries_are_diagnostics_not_usable(self) -> None:
        """Tampered, incomplete, and future archives become diagnostics only."""
        result = self.create()
        final = self.backups / f"{result['backup_id']}.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(final, "a") as archive:
                archive.writestr("payload/Config Files/serverDZ.cfg", "tampered")
        # Add a partial file and an archive with a future schema
        (self.backups / ".interrupted.partial").write_bytes(b"incomplete")
        future = self.backups / "future.zip"
        with zipfile.ZipFile(future, "w") as archive:
            archive.writestr("manifest.json", '{"schema_version":99}')
        # All three artifacts must surface as unusable destination diagnostics
        history = self.service.history("main")
        self.assertEqual(history["backups"], [])
        self.assertEqual(sorted(item["code"] for item in history["diagnostics"]),
                         ["CORRUPT", "FUTURE_SCHEMA"])
        self.assertTrue(all(item["usable"] is False for item in history["diagnostics"]))
        self.assertEqual({item["scope"] for item in history["diagnostics"]},
                         {"DESTINATION"})

    def test_cancellation_and_failures_before_publish_leave_no_snapshot_or_staging(self) -> None:
        """Cancellation before publish leaves no snapshot or staging debris."""
        for phase in ("DISCOVER", "STAGE", "HASH", "WRITE_MANIFEST", "VERIFY"):
            with self.subTest(phase=phase):
                target = self.root / f"cancel-{phase}"
                target.mkdir()
                settings = FakeSettings(self.dayz, target)
                service = BackupService(FakeProfiles(record()), settings, BackupStorage(),
                    clock=lambda: datetime(2026, 9, 25, tzinfo=UTC), identifier=lambda: phase.lower())
                def checkpoint(current: str, _percent: int) -> None:
                    """Cancel the operation once the named phase is reached."""
                    if current == phase:
                        raise OperationCancelled
                with self.assertRaises(OperationCancelled):
                    service.create("main", 3, 4, checkpoint)
                self.assertFalse(any(target.glob("*.zip")))
                self.assertFalse(any(target.glob(".staging-*")))
                self.assertFalse(any(target.glob("*.partial")))

    def test_interruption_at_each_prepublication_phase_leaves_no_usable_snapshot(self) -> None:
        """An interruption before publish leaves no usable snapshot."""
        for phase in ("DISCOVER", "STAGE", "HASH", "WRITE_MANIFEST", "VERIFY"):
            with self.subTest(phase=phase):
                target = self.root / f"failure-{phase}"
                target.mkdir()
                def interrupt(current: str, stop: str = phase) -> None:
                    """Raise an I/O error once the named phase starts."""
                    if current == stop:
                        raise OSError("synthetic interruption")
                storage = BackupStorage(phase_hook=interrupt)
                service = BackupService(FakeProfiles(record()), FakeSettings(self.dayz, target),
                    storage, clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
                    identifier=lambda: phase.lower())
                with self.assertRaises(OSError):
                    service.create("main", 3, 4, lambda _phase, _percent: None)
                self.assertFalse(any(target.glob("*.zip")))
                self.assertFalse(any(target.glob(".staging-*")))
                self.assertFalse(any(target.glob("*.partial")))

    def test_space_write_unc_ads_and_link_guards_fail_closed(self) -> None:
        """Space, write, ADS, and link guards fail closed before publication."""
        storage = BackupStorage(disk_usage=lambda _path: SimpleNamespace(free=0))
        service = BackupService(FakeProfiles(record()), self.settings, storage,
            clock=lambda: datetime(2026, 9, 25, tzinfo=UTC), identifier=lambda: "space")
        # Space and path guards must fail before any copy starts
        with self.assertRaisesRegex(BackupStorageError, "enough free space"):
            service.create("main", 3, 4, lambda _phase, _percent: None)
        with self.assertRaisesRegex(BackupStorageError, "Alternate data streams"):
            BackupStorage().source(self.dayz, "Config Files\\serverDZ.cfg:secret")
        with self.assertRaisesRegex(BackupStorageError, "invalid"):
            BackupStorage().source(self.dayz, "..\\outside.cfg")
        with self.assertRaisesRegex(BackupStorageError, "absolute local path"):
            BackupStorage().history(Path(r"\\server\share"), "main")
        with patch(
            "dayz_serverman.repositories.backup_sources.os.access",
            side_effect=lambda path, _mode: Path(path) != self.backups.resolve(),
        ):
            with self.assertRaisesRegex(BackupStorageError, "readable and writable"):
                self.service.create("main", 3, 4, lambda _phase, _percent: None)
        # An unwritable destination and a failing copy must fail closed too
        with patch("dayz_serverman.repositories.backups.copy_verified", side_effect=OSError("synthetic write")):
            with self.assertRaises(OSError):
                self.service.create("main", 3, 4, lambda _phase, _percent: None)
        self.assertFalse(any(self.backups.glob("*.zip")))
        self.assertFalse(any(self.backups.glob(".staging-*")))

    def test_symlink_source_is_rejected_when_supported(self) -> None:
        """A linked source file is rejected when links are supported."""
        link = self.dayz / "linked.cfg"
        try:
            link.symlink_to(self.dayz / "Config Files" / "serverDZ.cfg")
        except OSError:
            self.skipTest("symlink creation is unavailable")
        with self.assertRaisesRegex(BackupStorageError, "links or reparse"):
            BackupStorage().source(self.dayz, "linked.cfg")

    def test_cwd_and_manager_relocation_do_not_change_selected_roots(self) -> None:
        """Relocating the working directory never changes the selected roots."""
        prior = Path.cwd()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        try:
            # Run a backup from an unrelated working directory
            os.chdir(elsewhere)
            result = self.create()
        finally:
            os.chdir(prior)
        self.assertTrue((self.backups / f"{result['backup_id']}.zip").is_file())

    def test_same_second_backups_receive_readable_collision_suffixes(self) -> None:
        """Backups in the same second receive readable collision suffixes."""
        first = self.create()
        second = self.create()
        self.assertEqual(first["backup_id"], "main_2026-09-25_14-34-56")
        self.assertEqual(second["backup_id"], "main_2026-09-25_14-34-56_02")
        self.assertTrue((self.backups / f"{second['backup_id']}.zip").is_file())


if __name__ == "__main__":
    unittest.main()
