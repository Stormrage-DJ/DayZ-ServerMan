"""Profile repository tests for schema two, v1 migration, and fail-closed storage."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.domain.models import RevisionConflict  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput  # noqa: E402
from dayz_serverman.repositories.profiles import (  # noqa: E402
    ProfileNotFound, ProfileRepository, ProfileStorageError,
)
from dayz_serverman.repositories.profile_migration import (  # noqa: E402
    migrate_v1_document, serialize_profile_document,
)
from tests.profile_fixtures import profile_payload  # noqa: E402


def v1_payload() -> dict[str, object]:
    """Build a schema-one profile payload with workshop mods for migration tests."""
    value = profile_payload(runtime_profile=None)
    value.pop("runtime_profile")
    value["mods"] = [
        {"workshop_id": "1559212036", "directory": "@Community Framework"},
        {"workshop_id": "1564026768", "directory": "@Community Online Tools"},
    ]
    return {"schema_version": 1, "revision": 4, **value}


class ProfileRepositoryTests(unittest.TestCase):
    """Contract: profiles persist as schema two with lossless, guarded v1 migration."""
    def setUp(self) -> None:
        """Point the repository at a temporary profiles root."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_profiles_")
        self.root = Path(self.temporary.name) / "data" / "profiles"
        self.repository = ProfileRepository(self.root)

    def tearDown(self) -> None:
        """Remove the temporary profiles root."""
        self.temporary.cleanup()

    def test_create_read_list_update_and_semantic_reload_use_schema_two(self) -> None:
        """Create, read, list, update, and reload with schema two semantics."""
        # Create a schema-two record and inspect the serialized document
        created = self.repository.save(ProfileInput.parse(profile_payload()), None)
        payload = json.loads((self.root / "livonia-main.json").read_text(encoding="utf-8"))
        self.assertEqual((created.revision, payload["schema_version"]), (0, 2))
        # Listing and loading agree with the created record
        self.assertEqual(self.repository.list(), (created,))
        self.assertEqual(self.repository.load("livonia-main").semantic_digest, created.semantic_digest)
        # Updates advance the revision and reject the earlier revision
        updated = self.repository.save(
            ProfileInput.parse(profile_payload(display_name="Updated Profile")), 0,
        )
        self.assertEqual(updated.revision, 1)
        with self.assertRaises(RevisionConflict):
            self.repository.save(updated.values, 0)

    def test_v1_migration_is_lossless_idempotent_and_keeps_revision(self) -> None:
        """Migrate a v1 document losslessly, idempotently, and with its revision kept."""
        # Write a schema-one document directly
        self.root.mkdir(parents=True)
        path = self.root / "livonia-main.json"
        path.write_text(json.dumps(v1_payload()), encoding="utf-8")
        # Loading migrates the document while keeping its revision
        migrated = self.repository.load("livonia-main")
        self.assertEqual(migrated.revision, 4)
        self.assertIsNone(migrated.values.runtime_profile)
        self.assertEqual(
            [(mod.launch_scope, mod.source.kind, mod.source.workshop_id, mod.directory)
             for mod in migrated.values.mods],
            [
                ("client", "workshop", "1559212036", "@Community Framework"),
                ("client", "workshop", "1564026768", "@Community Online Tools"),
            ],
        )
        # A second load must be byte-stable and idempotent
        first_bytes = path.read_bytes()
        self.assertEqual(self.repository.load("livonia-main"), migrated)
        self.assertEqual(path.read_bytes(), first_bytes)

    def test_interrupted_migration_recovers_from_valid_staging(self) -> None:
        """Recover an interrupted migration from its valid staging file."""
        self.root.mkdir(parents=True)
        path = self.root / "livonia-main.json"
        path.write_text(json.dumps(v1_payload()), encoding="utf-8")

        def interrupt(phase: str, _path: Path) -> None:
            """Raise a synthetic interruption after the temporary file is synced."""
            if phase == "after_temp_fsync":
                raise OSError("synthetic interruption")

        # The interrupted migration leaves the source at version one
        with self.assertRaises(ProfileStorageError):
            ProfileRepository(self.root, interrupt).load("livonia-main")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 1)
        # A subsequent load completes the migration from the staging file
        recovered = ProfileRepository(self.root).load("livonia-main")
        self.assertEqual(recovered.revision, 4)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 2)
        self.assertFalse((self.root / ".livonia-main.json.migration.tmp").exists())

    def test_future_corrupt_and_unrelated_temp_evidence_fail_closed(self) -> None:
        """Fail closed on future, corrupt, and unrelated temporary evidence."""
        # Corrupt and future documents both fail closed with recovery required
        self.root.mkdir(parents=True)
        path = self.root / "livonia-main.json"
        for value in ("{broken", json.dumps({"schema_version": 99, "revision": 0, **profile_payload()})):
            path.write_text(value, encoding="utf-8")
            with self.assertRaises(ProfileStorageError) as raised:
                self.repository.load("livonia-main")
            self.assertTrue(raised.exception.recovery_required)
        # Unrelated temporary evidence must also fail the listing closed
        path.write_text(json.dumps({"schema_version": 2, "revision": 0, **profile_payload()}), encoding="utf-8")
        (self.root / ".livonia-main.json.other.tmp").write_text("{}", encoding="utf-8")
        with self.assertRaises(ProfileStorageError):
            self.repository.list()

    def test_conflicting_migration_stage_never_replaces_authoritative_v2(self) -> None:
        """Refuse a conflicting migration stage over an authoritative v2 record."""
        # Stage a conflicting migration beside the authoritative record
        saved = self.repository.save(ProfileInput.parse(profile_payload()), None)
        path = self.root / "livonia-main.json"
        original = path.read_bytes()
        staged = json.loads(original)
        staged["display_name"] = "Conflicting"
        (self.root / ".livonia-main.json.migration.tmp").write_text(
            json.dumps(staged), encoding="utf-8",
        )
        # The conflict must fail closed and leave the v2 bytes untouched
        with self.assertRaises(ProfileStorageError) as raised:
            self.repository.load(saved.values.profile_id)
        self.assertTrue(raised.exception.recovery_required)
        self.assertEqual(path.read_bytes(), original)

    def test_v1_recovery_rejects_every_non_deterministic_staged_variant(self) -> None:
        """Reject every non-deterministic staged v1 variant."""
        # Mutate one staged field per variant and expect rejection
        variants = {
            "display": lambda value: value.update(display_name="Tampered"),
            "runtime": lambda value: value.update(runtime_profile="profiles\\guessed"),
            "mod": lambda value: value["mods"][0].update(launch_scope="server"),
            "extra": lambda value: value["extra_arguments"].append("-tampered"),
            "schema-bool": lambda value: value.update(schema_version=True),
        }
        for name, mutate in variants.items():
            with self.subTest(name=name):
                self.root.mkdir(parents=True, exist_ok=True)
                path = self.root / "livonia-main.json"
                source = v1_payload()
                path.write_text(json.dumps(source), encoding="utf-8")
                staged = migrate_v1_document(source)
                mutate(staged)
                temporary = self.root / ".livonia-main.json.migration.tmp"
                temporary.write_text(serialize_profile_document(staged), encoding="utf-8")
                with self.assertRaises(ProfileStorageError):
                    self.repository.load("livonia-main")
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), source)
                temporary.unlink()

        # A staged file without canonical serialization must also be rejected
        path = self.root / "livonia-main.json"
        source = v1_payload()
        path.write_text(json.dumps(source), encoding="utf-8")
        expected = migrate_v1_document(source)
        temporary = self.root / ".livonia-main.json.migration.tmp"
        temporary.write_text(json.dumps(expected), encoding="utf-8")
        with self.assertRaises(ProfileStorageError):
            self.repository.load("livonia-main")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), source)

    def test_boolean_schema_version_is_never_accepted_as_version_one(self) -> None:
        """Never accept a boolean schema version as version one."""
        self.root.mkdir(parents=True)
        value = v1_payload()
        value["schema_version"] = True
        (self.root / "livonia-main.json").write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ProfileStorageError) as raised:
            self.repository.load("livonia-main")
        self.assertTrue(raised.exception.recovery_required)

    def test_delete_requires_current_revision(self) -> None:
        """Require the current revision to delete a profile."""
        # Deleting with a stale revision must be refused
        self.repository.save(ProfileInput.parse(profile_payload()), None)
        with self.assertRaises(RevisionConflict):
            self.repository.delete("livonia-main", 5)
        # Deleting with the current revision removes and hides the record
        self.assertEqual(self.repository.delete("livonia-main", 0), "livonia-main")
        with self.assertRaises(ProfileNotFound):
            self.repository.load("livonia-main")


if __name__ == "__main__":
    unittest.main()
