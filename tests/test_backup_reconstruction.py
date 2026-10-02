"""Complete metadata and empty-world archive contracts."""

import copy
import json
import zipfile

import unittest
import tests.test_backups as fixtures
from dayz_serverman.domain.backups import BackupManifest, BackupManifestError
from dayz_serverman.repositories.backup_archives import verify_archive
from dayz_serverman.repositories.backups import BackupStorage


class ReconstructionTests(unittest.TestCase):
    """Use the realistic backup fixture for additional schema-three boundaries."""

    def setUp(self):
        """Build the realistic backup fixture without inheriting its unrelated test cases."""
        fixtures.BackupTests.setUp(self)
        self.addCleanup(self.temporary.cleanup)

    def create(self):
        """Capture a production archive using the shared fixture's phase assertions."""
        return fixtures.BackupTests.create(self)

    def test_definition_and_empty_directories_round_trip(self):
        """The archive restores complete input values and existing empty world/runtime trees."""
        mission = self.dayz / "mpmissions" / "dayzOffline.chernarusplus"
        (mission / "storage_1" / "empty").mkdir(parents=True)
        (self.dayz / "profiles" / "main" / "empty").mkdir()
        result = self.create()
        storage = BackupStorage()
        with storage.open_verified(self.backups, result["backup_id"], "main") as (directory, manifest):
            self.assertEqual(manifest.schema_version, 3)
            self.assertEqual(manifest.reconstruction["profile"], dict(self.service._profiles.record.fields()))
            self.assertTrue(manifest.reconstruction["selected_storage_present"])
            self.assertTrue((directory / manifest.reconstruction["mission_prefix"] / "storage_1" / "empty").is_dir())
            self.assertTrue((directory / "runtime-profile" / "empty").is_dir())

    def test_reconstruction_contradictions_and_directory_collisions_rejected(self):
        """Metadata identity, presence and file/directory conflicts are independently checked."""
        result = self.create()
        manifest = BackupStorage().verified_manifest(self.backups, result["backup_id"], "main")
        for key, value in (("profile_schema_version", True), ("instance_id", 0),
                           ("selected_storage_present", True), ("config_entry", "payload/other.cfg")):
            raw = copy.deepcopy(manifest.to_dict())
            raw["reconstruction"][key] = value
            with self.subTest(key=key), self.assertRaises(BackupManifestError):
                BackupManifest.parse(raw)
        raw = manifest.to_dict()
        raw["directories"] = sorted([*raw["directories"], manifest.entries[0].path], key=str.casefold)
        with self.assertRaises(BackupManifestError):
            BackupManifest.parse(raw)

    def test_resigned_configuration_contradiction_is_not_usable(self):
        """A valid metadata signature cannot hide a different instance in config bytes."""
        result = self.create()
        path = self.backups / (result["backup_id"] + ".zip")
        original = BackupStorage().verified_manifest(self.backups, result["backup_id"], "main")
        raw = copy.deepcopy(original.to_dict())
        raw["reconstruction"]["instance_id"] = 9
        raw["reconstruction"]["selected_storage_relative"] = "storage_9"
        malformed = BackupManifest.parse(raw).signed()
        with self.assertRaisesRegex(ValueError, "configuration disagrees"):
            verify_archive(path, malformed)

    def test_catalog_survives_deleted_profiles_and_corrupt_neighbors(self):
        """Catalog uses configured storage alone and reports individual corrupt archives."""
        result = self.create()
        self.service._profiles = None
        (self.backups / "broken.zip").write_bytes(b"broken")
        catalog = self.service.catalog()
        self.assertEqual(catalog["backups"][0]["backup_id"], result["backup_id"])
        self.assertTrue(catalog["backups"][0]["can_restore_profile"])
        self.assertEqual(catalog["diagnostics"][0]["backup_id"], "broken")
