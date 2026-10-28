"""Backup retention: a new backup keeps the newest verified backups of its profile and deletes the older ones."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.backups import BackupService, MISSION_INVENTORY, retention_notice  # noqa: E402
from dayz_serverman.application.coordinator import ApplicationCoordinator  # noqa: E402
from dayz_serverman.application.settings import (  # noqa: E402
    SettingsService, SettingsValidationError, normalize_settings,
)
from dayz_serverman.bridge.facade import ApplicationCallError  # noqa: E402
from dayz_serverman.domain.models import DEFAULT_BACKUPS_TO_KEEP, SettingsInput, VersionedDocument  # noqa: E402
from dayz_serverman.repositories.backups import BackupStorage  # noqa: E402
from dayz_serverman.repositories.migration_payload_identity import (  # noqa: E402
    MigrationPayloadIdentityError, _validate_settings,
)

from tests.backup_fixtures import FakeProfiles, FakeSettings, create_runtime_profile, record


class RetentionTests(unittest.TestCase):
    """Each create prunes the older backups of the same profile beyond the kept number."""

    def setUp(self) -> None:
        """Create a DayZ tree with one mission and a portable backup folder."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_retention_")
        self.root = Path(self.temporary.name)
        self.dayz = self.root / "DayZ"
        self.backups = self.root / "Manager" / "backups"
        self.backups.mkdir(parents=True)
        server = self.dayz / "Config Files" / "serverDZ.cfg"
        server.parent.mkdir(parents=True)
        server.write_text('class Missions { class DayZ { template = "dayzOffline.chernarusplus"; }; };\n',
                          encoding="utf-8")
        create_runtime_profile(self.dayz)
        mission = self.dayz / "mpmissions" / "dayzOffline.chernarusplus"
        for item in MISSION_INVENTORY:
            target = mission.joinpath(*item.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"fixture:{item}\n", encoding="utf-8")
        self.minute = 0

    def tearDown(self) -> None:
        """Remove the temporary tree."""
        self.temporary.cleanup()

    def service(self, keep: int) -> BackupService:
        """Return a backup service whose clock moves one minute per backup."""
        def clock() -> datetime:
            self.minute += 1
            return datetime(2026, 10, 28, 10, 0, tzinfo=UTC) + timedelta(minutes=self.minute)
        return BackupService(FakeProfiles(record()), FakeSettings(self.dayz, self.backups, keep=keep),  # type: ignore[arg-type]
                             BackupStorage(), clock=clock, local_timezone=UTC)

    def create(self, keep: int) -> dict:
        """Create one backup of the profile `main`."""
        return self.service(keep).create("main", 3, 4, lambda _phase, _percent: None)

    def archives(self) -> list[str]:
        """Return the backup identifiers in the folder, oldest first."""
        return sorted(path.stem for path in self.backups.glob("*.zip"))

    def test_the_newest_three_stay_by_default(self) -> None:
        """The fourth and fifth backups delete the oldest ones and name them in the result."""
        results = [self.create(DEFAULT_BACKUPS_TO_KEEP) for _ in range(5)]
        ids = [result["backup_id"] for result in results]
        self.assertEqual(self.archives(), ids[2:])
        self.assertEqual([result["pruned_backups"] for result in results], [[], [], [], [ids[0]], [ids[1]]])
        self.assertEqual(results[3]["backups_to_keep"], 3)
        self.assertEqual(results[3]["prune_failures"], [])

    def test_a_lower_number_deletes_several_at_once(self) -> None:
        """Lowering the number deletes every older backup beyond it with the next backup."""
        ids = [self.create(5)["backup_id"] for _ in range(4)]
        result = self.create(1)
        self.assertEqual(self.archives(), [result["backup_id"]])
        self.assertEqual(sorted(result["pruned_backups"]), ids)

    def test_other_profiles_and_foreign_files_stay(self) -> None:
        """Archives of another profile, a ZIP without a manifest, and a renamed archive are never deleted."""
        first = self.create(1)["backup_id"]
        (self.backups / "notes.zip").write_bytes(b"not a zip")
        renamed = self.backups / "kept-by-hand.zip"
        (self.backups / f"{first}.zip").rename(renamed)
        other = self.backups / "pripyat_2026-10-01_10-00-00.zip"
        other.write_bytes(renamed.read_bytes().replace(b'"main"', b'"pripyat"'))
        self.create(1)
        self.create(1)
        self.assertTrue((self.backups / "notes.zip").is_file())
        self.assertTrue(renamed.is_file())
        self.assertTrue(other.is_file())

    def test_a_corrupt_backup_does_not_count_as_kept(self) -> None:
        """A backup that fails verification does not count, so one more verified backup stays."""
        ids = [self.create(2)["backup_id"] for _ in range(2)]
        # Rewrite one payload file of the newer backup; its manifest digest no longer matches
        corrupt = self.backups / f"{ids[1]}.zip"
        with zipfile.ZipFile(corrupt) as archive:
            members = [(info, archive.read(info)) for info in archive.infolist()]
        with zipfile.ZipFile(corrupt, "w") as archive:
            for info, content in members:
                changed = info.filename.endswith("serverDZ.cfg")
                archive.writestr(info, content.replace(b"Missions", b"MISSIONS") if changed else content)
        newest = self.create(2)["backup_id"]
        self.assertEqual(self.archives(), sorted([ids[0], ids[1], newest]))

    def test_a_failed_deletion_keeps_the_backup_and_is_reported(self) -> None:
        """An archive that cannot be deleted stays; the new backup still succeeds and names it."""
        first = self.create(1)["backup_id"]
        with patch.object(Path, "unlink", side_effect=PermissionError("in use")):
            result = self.create(1)
        self.assertEqual((result["pruned_backups"], result["prune_failures"]), ([], [first]))
        self.assertIn(first, self.archives())
        self.assertIn("could not be deleted", retention_notice(result))


class RetentionNoticeTests(unittest.TestCase):
    """The notice after a backup names what retention deleted."""

    def test_notice_for_create_and_stop_results(self) -> None:
        """A create result and the `backup` result of a stop give the same text; nothing deleted gives none."""
        result = {"backups_to_keep": 3, "pruned_backups": ["main_a", "main_b"], "prune_failures": []}
        text = "Deleted the older backups beyond the newest 3 of this profile: main_a, main_b."
        self.assertEqual(retention_notice(result), text)
        self.assertEqual(retention_notice({"backup": result}), text)
        self.assertIsNone(retention_notice({"backups_to_keep": 3, "pruned_backups": [], "prune_failures": []}))
        self.assertIsNone(retention_notice(None))


class RetentionSettingTests(unittest.TestCase):
    """The number is a validated setting with a default for records stored before it existed."""

    def test_range_is_validated(self) -> None:
        """Whole numbers from 1 to 100 pass; zero, 101, text and booleans are refused."""
        self.assertEqual(normalize_settings(SettingsInput(backups_to_keep=1)).backups_to_keep, 1)
        self.assertEqual(normalize_settings(SettingsInput()).backups_to_keep, 3)
        for value in (0, 101, "3", True, 2.0):
            with self.subTest(value=value), self.assertRaises(SettingsValidationError):
                normalize_settings(SettingsInput(backups_to_keep=value))  # type: ignore[arg-type]

    def test_older_records_load_with_the_default(self) -> None:
        """A stored record without the field loads as 3; a stored invalid number is refused."""
        service = SettingsService.__new__(SettingsService)
        self.assertEqual(service._from_document(VersionedDocument(1, 5, {})).backups_to_keep, 3)
        self.assertEqual(service._from_document(VersionedDocument(1, 5, {"backups_to_keep": 7})).backups_to_keep, 7)
        with self.assertRaises(SettingsValidationError):
            service._from_document(VersionedDocument(1, 5, {"backups_to_keep": 0}))

    def test_save_request_validates_the_number(self) -> None:
        """The bridge refuses an invalid number before an operation is queued."""
        coordinator = ApplicationCoordinator.__new__(ApplicationCoordinator)
        for value in (0, 101, "3"):
            with self.subTest(value=value), self.assertRaises(ApplicationCallError):
                coordinator.save_settings({"dayz_root": None, "steamcmd_root": None, "custom_backup_root": None,
                                           "backups_to_keep": value, "expected_revision": 1})

    def test_migration_payload_accepts_the_field(self) -> None:
        """A settings payload with a valid number passes; an invalid number is refused."""
        fields = {"dayz_root": None, "dayz_executable": None, "steamcmd_root": None, "steamcmd_executable": None,
                  "workshop_content_root": None, "custom_backup_root": None, "last_validated_paths": {},
                  "steam_account_name": None, "steam_authentication_mode": None}
        _validate_settings(1, json.loads(json.dumps({**fields, "backups_to_keep": 5})))
        _validate_settings(1, json.loads(json.dumps(fields)))
        with self.assertRaises(MigrationPayloadIdentityError):
            _validate_settings(1, {**fields, "backups_to_keep": 0})


if __name__ == "__main__":
    unittest.main()
