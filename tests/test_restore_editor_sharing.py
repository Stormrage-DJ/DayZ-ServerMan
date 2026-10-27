"""The shared check before a D5 supersede, and the schema 4 acceptance of the same-profile restore (leaf 10.10)."""

from __future__ import annotations

import unittest
from pathlib import Path

from dayz_serverman.application.mission_map_sharing import other_targets
from dayz_serverman.domain.backup_entries import RESTORE_COMPATIBLE, restore_compatibility
from dayz_serverman.repositories.backup_catalog import list_catalog
from dayz_serverman.repositories.backups import BackupStorageError
from dayz_serverman.repositories.mission_map_layout import TargetClass
from dayz_serverman.repositories.mission_map_records import OriginalStore
from dayz_serverman.repositories.selected_backup import SelectedBackups
from tests.restore_editor_fixtures import BANDITS, RestoreEditorFixture, other_profile

FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"


class SharedTargetTests(RestoreEditorFixture):
    """A supersede needs proof that no other profile resolves to the target (Architect ruling (a))."""

    def restore_with_a_later_runtime_original(self) -> tuple[dict, dict, dict]:
        """Back up, capture another runtime original and restore; return both originals and the preview rows."""
        self.write_all()
        backup = self.backup()
        archived = OriginalStore(self.area, self.runtime_key).read()
        (self.area / "originals" / self.runtime_key / "original.json").unlink()
        later = self.write_original(self.runtime_key, "runtime", b'{"bandits": 2}')
        rows = self.restore_service().preview("main", backup)["editor"]["rows"]
        self.restore(backup)
        return archived, later, rows

    def assert_supersede_refused(self, archived: dict, later: dict, rows: dict, sharing: list[str]) -> None:
        """Assert that the later runtime original stays active and the backup original is a restored copy."""
        folder = self.area / "originals" / self.runtime_key
        self.assertEqual(OriginalStore(self.area, self.runtime_key).read()["manifest_sha256"], later["manifest_sha256"])
        self.assertEqual(list(folder.glob("superseded-*")), [])
        copy = self.envelope(self.folder() / "restored-runtime-restore-1.json")
        self.assertEqual(copy["manifest_sha256"], archived["manifest_sha256"])
        self.assertEqual((rows["original_runtime"], rows["shared_runtime"]), ("restored_copy", sharing))

    def test_a_runtime_target_that_another_profile_resolves_to_is_not_superseded(self) -> None:
        self.profiles.others.append(other_profile("other", "profiles\\main", None))
        self.assert_supersede_refused(*self.restore_with_a_later_runtime_original(), ["other"])

    def test_a_runtime_target_that_cannot_be_resolved_counts_as_shared(self) -> None:
        # An existing folder outside the DayZ root cannot be proven separate (fail closed)
        (self.dayz.parent / "outside").mkdir()
        self.profiles.others.append(other_profile("other", "..\\outside", None))
        self.assert_supersede_refused(*self.restore_with_a_later_runtime_original(), ["other"])

    def test_a_runtime_target_of_no_other_profile_is_superseded(self) -> None:
        # A missing folder is proven separate, and a profile without a runtime folder has no target
        self.profiles.others += [other_profile("gone", "profiles\\gone", None), other_profile("bare", None, None)]
        archived, later, rows = self.restore_with_a_later_runtime_original()
        self.assertEqual((rows["original_runtime"], rows["shared_runtime"]), ("supersede", []))
        kept = self.envelope(self.area / "originals" / self.runtime_key / "superseded-restore-1.json")
        self.assertEqual(kept["manifest_sha256"], later["manifest_sha256"])
        self.assertEqual(OriginalStore(self.area, self.runtime_key).read()["manifest_sha256"],
                         archived["manifest_sha256"])

    def test_the_helper_resolves_both_keys_of_every_other_profile(self) -> None:
        root = self.dayz.resolve()
        (root / "mpmissions" / "other").mkdir()
        records = [self.profiles.main, other_profile("same", "profiles\\main"),
                   other_profile("own", None, "mpmissions\\other"), other_profile("missing", None, "mpmissions\\none"),
                   other_profile("inferred", None, None)]
        (root / "Config Files" / "serverDZ.cfg").write_text("no template\n", encoding="utf-8")
        found = other_targets(root, records, "main")
        self.assertEqual(found.mission["same"], self.mission_key)
        self.assertNotEqual(found.mission["own"], self.mission_key)
        # A missing explicit folder has no target; an unreadable inferred mission cannot be resolved
        self.assertNotIn("missing", found.mission)
        self.assertIsNone(found.mission["inferred"])
        self.assertEqual(found.runtime, {"same": self.runtime_key})
        self.assertEqual(found.sharing(TargetClass.MISSION, self.mission_key), ("inferred", "same"))
        self.assertEqual(found.sharing(TargetClass.RUNTIME, self.runtime_key), ("same",))
        self.assertNotIn("main", found.to_dict()["mission"])


class SchemaFourTests(RestoreEditorFixture):
    """The same-profile restore, the catalog and the selected backup accept schema 4."""

    def test_schema_4_is_compatible_and_complete(self) -> None:
        self.write_all()
        backup = self.backup()
        manifest = self.restore_service()._backups.verified_manifest(self.paths.backups, backup, "main")
        self.assertEqual((manifest.schema_version, restore_compatibility(manifest)[0]), (4, RESTORE_COMPATIBLE))
        entry = next(item for item in list_catalog(self.paths.backups)["backups"] if item["backup_id"] == backup)
        self.assertEqual((entry["can_restore_profile"], entry["reconstruction_reason"]),
                         (True, "Complete profile metadata is available."))
        selected = SelectedBackups().inspect(str(self.paths.backups / f"{backup}.zip"))
        self.assertTrue(selected["can_restore_profile"])

    def test_a_restore_without_the_editor_part_refuses_schema_4(self) -> None:
        self.write_all()
        backup = self.backup()
        with self.assertRaises(BackupStorageError) as raised:
            self.restore_service(editor=False).preview("main", backup)
        self.assertEqual(raised.exception.code, "PENDING_RUNTIME_PROFILE_SUPPORT")
        self.assertEqual(BANDITS, (self.runtime / "AI_Bandits" / "DynamicAIB.json").read_bytes())

    def test_open_editor_windows_follow_both_restore_kinds(self) -> None:
        script = (FRONTEND / "map_editor_follow.js").read_text(encoding="utf-8")
        kinds = script.split("const mapFollowKinds = Object.freeze([", 1)[1].split("]);", 1)[0]
        for kind in ("RESTORE_BACKUP", "RESTORE_PROFILE_FROM_BACKUP", "APPLY_MISSION_MAP"):
            self.assertIn(f'"{kind}"', kinds)


if __name__ == "__main__":
    unittest.main()
