"""Profile backups carry the editor records of the active association as plain files with digests (10.9, D18)."""

from __future__ import annotations

import json
import unittest
import zipfile
from unittest.mock import patch

from dayz_serverman.domain.backups import RESTORE_COMPATIBLE, restore_compatibility
from dayz_serverman.repositories import backup_editor_records
from dayz_serverman.repositories.backups import BackupStorageError
from dayz_serverman.repositories.backup_verification import read_manifest, verify_directory
from dayz_serverman.repositories.mission_map_records import OriginalStore
from tests.backup_editor_fixtures import (
    BANDITS, MISSION_ROOT, PREVIOUS, REPLACED, VANILLA, EditorBackupFixture, sha,
)


class EditorRecordBackupTests(EditorBackupFixture):
    """Round trip, the carried set, the schema 3 cases and the association lock."""

    def expected_paths(self) -> set[str]:
        """Return every editor entry of the fixture records."""
        association = "editor/association/"
        mission, runtime = f"editor/originals/{self.mission_key}/", f"editor/originals/{self.runtime_key}/"
        return {association + "plan.json", association + "baseline.json", association + "applied.json",
                *(association + "contents/" + sha(data) for data in (VANILLA, BANDITS, REPLACED, PREVIOUS)),
                mission + "original.json", mission + "contents/" + sha(VANILLA),
                runtime + "original.json", runtime + "contents/" + sha(BANDITS)}

    def editor_paths(self, manifest) -> set[str]:
        """Return the editor entry paths of a manifest."""
        return {entry.path for entry in manifest.entries if entry.path.startswith("editor/")}

    def test_round_trip_carries_the_records_and_their_contents(self) -> None:
        self.write_all()
        result = self.create()
        manifest = self.storage.verified_manifest(self.backups, result["backup_id"], "main")
        self.assertEqual(manifest.schema_version, 4)
        self.assertEqual(self.editor_paths(manifest), self.expected_paths())
        mission, runtime = OriginalStore(self.area, self.mission_key).read(), OriginalStore(self.area, self.runtime_key).read()
        self.assertEqual(manifest.editor, {
            "version": 1, "profile_id": "main",
            "association": {"mission_key": self.mission_key, "mission_root": MISSION_ROOT,
                            "records": ["plan", "baseline", "applied"]},
            "originals": sorted(({name: item[name] for name in ("target_class", "target_key", "manifest_sha256",
                                                                 "display_path", "mission_root")}
                                 for item in (mission, runtime)), key=lambda item: item["target_key"])})
        # The extracted records are the stored bytes, and the inventory check passes on the extracted tree
        folder = self.association_folder()
        with self.storage.open_verified(self.backups, result["backup_id"], "main") as (directory, extracted):
            self.assertEqual(extracted, manifest)
            verify_directory(directory, read_manifest(directory, manifest.backup_id))
            for name in ("plan.json", "baseline.json", "applied.json"):
                self.assertEqual((directory / "editor" / "association" / name).read_bytes(), (folder / name).read_bytes())
            original = self.area / "originals" / self.runtime_key / "original.json"
            self.assertEqual((directory / "editor" / "originals" / self.runtime_key / "original.json").read_bytes(),
                             original.read_bytes())
            self.assertEqual((directory / "editor" / "association" / "contents" / sha(REPLACED)).read_bytes(),
                             REPLACED)
        # The payload is unchanged, and this build restores the editor entries of schema 4 (10.10)
        self.assertTrue(any(entry.path.startswith("payload/") for entry in manifest.entries))
        self.assertEqual(restore_compatibility(manifest)[0], RESTORE_COMPATIBLE)
        self.assertEqual(result["restore_compatibility"], RESTORE_COMPATIBLE)
        self.assertIs(result["editor_records_omitted"], False)

    def test_digests_come_from_the_archived_bytes(self) -> None:
        self.write_all()
        archive = self.archive(self.create())
        with zipfile.ZipFile(archive) as stored:
            manifest = json.loads(stored.read("manifest.json"))
            for entry in manifest["entries"]:
                if entry["path"].startswith("editor/"):
                    self.assertEqual(sha(stored.read(entry["path"])), entry["sha256"])
        # The plan keeps its background reference as stored; no image file travels
        plan = json.loads((self.association_folder() / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["plan"]["background"]["path"], "C:\\maps\\map.png")
        self.assertFalse(any(entry["path"].endswith(".png") for entry in manifest["entries"]))

    def test_only_the_active_association_and_its_originals_travel(self) -> None:
        self.write_all()
        folder = self.association_folder()
        # Unused content, an invalidated and a set-aside record, another association, another original, a ledger
        (folder / "files" / ("0" * 64)).write_bytes(b"unused")
        (folder / "applied.invalidated-20261016T120000Z.json").write_text("{}", encoding="utf-8")
        (folder / "plan.invalid-20261016T120000Z.json").write_text("{}", encoding="utf-8")
        other = self.area / "profiles" / "main" / ("e" * 32)
        other.mkdir(parents=True)
        (other / "notes.txt").write_text("other association", encoding="utf-8")
        self.write_original("f" * 32)
        ledger = self.area / "ledger" / self.mission_key
        ledger.mkdir(parents=True)
        (ledger / "op-3.json").write_text("{}", encoding="utf-8")
        manifest = self.storage.verified_manifest(self.backups, self.create()["backup_id"], "main")
        self.assertEqual(self.editor_paths(manifest), self.expected_paths())

    def test_without_editor_records_the_backup_stays_at_schema_3(self) -> None:
        # Unbound, bound without records, and bound with originals only: schema 3 without an editor entry
        self.write_original(self.mission_key)
        for service in (self.backup_service(), self.service):
            manifest = self.storage.verified_manifest(self.backups, self.create(service)["backup_id"], "main")
            self.assertEqual(manifest.schema_version, 3)
            self.assertIsNone(manifest.editor)
            self.assertEqual(self.editor_paths(manifest), set())
            self.assertNotIn("editor", manifest.to_dict())

    def test_a_plan_alone_makes_schema_4(self) -> None:
        self.save_plan()
        manifest = self.storage.verified_manifest(self.backups, self.create()["backup_id"], "main")
        self.assertEqual(manifest.schema_version, 4)
        self.assertEqual(manifest.editor["association"]["records"], ["plan"])
        self.assertEqual(manifest.editor["originals"], [])

    def test_a_profile_in_the_path_change_state_stays_at_schema_3(self) -> None:
        self.write_all()
        # An old folder of the same mission root puts the profile in the path-change state
        old = self.area / "profiles" / "main" / ("a" * 32)
        old.mkdir(parents=True)
        (old / "plan.json").write_text(json.dumps({"schema_version": 1, "revision": 0, "plan": {
            "association": {"mission_root": MISSION_ROOT}}}), encoding="utf-8")
        self.assertIsNotNone(self.plans.load("main")["path_change"])
        manifest = self.storage.verified_manifest(self.backups, self.create()["backup_id"], "main")
        self.assertEqual(manifest.schema_version, 3)
        # A retired active folder is the path-change state too
        (old / "plan.json").unlink()
        (self.association_folder() / "retired.json").write_text(json.dumps({
            "schema_version": 1, "revision": 0, "operation_id": "op-9", "retired_at": "2026-10-16T12:00:00Z",
            "new_mission_key": "a" * 32}), encoding="utf-8")
        manifest = self.storage.verified_manifest(self.backups, self.create()["backup_id"], "main")
        self.assertEqual(manifest.schema_version, 3)

    def test_the_collector_reads_under_the_association_lock(self) -> None:
        self.write_all()
        held = []
        collect = backup_editor_records.collect_editor_records

        def spy(area, association, keys):
            """Record whether the association lock is held, then collect."""
            held.append(self.locks.lock_for("main", self.mission_key).locked())
            self.assertEqual(set(keys), {self.mission_key, self.runtime_key})
            return collect(area, association, keys)

        with patch("dayz_serverman.application.backups.collect_editor_records", spy):
            self.create()
        self.assertEqual(held, [True])
        self.assertFalse(self.locks.lock_for("main", self.mission_key).locked())

    def test_a_record_that_differs_from_the_store_read_refuses(self) -> None:
        self.write_all()
        with patch.object(backup_editor_records.AssociationRecordStore, "read", return_value=None):
            with self.assertRaises(BackupStorageError) as caught:
                self.create()
        self.assertEqual(caught.exception.code, "BACKUP_SOURCE_CHANGED")


if __name__ == "__main__":
    unittest.main()
