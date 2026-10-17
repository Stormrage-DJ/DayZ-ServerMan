"""Same-profile restore of the editor records: one test for each D5 row (D19, leaf 10.10)."""

from __future__ import annotations

import unittest

from dayz_serverman.domain.mission_map_ledger import restore_entry_id
from dayz_serverman.repositories.mission_map_records import OriginalStore
from tests.restore_editor_fixtures import APPLIED, VANILLA, RestoreEditorFixture, sha


class D5RowTests(RestoreEditorFixture):
    """Each destination state of the D5 table, with schema 4 archives of the unwired 10.9 collector."""

    def test_no_original_installs_the_carried_records(self) -> None:
        self.write_all()
        before = self.tree()
        backup = self.backup()
        self.clear_area()
        self.write_mission(VANILLA)
        result = self.restore(backup)
        self.assertEqual(result["journal_state"], "COMMITTED")
        after = self.tree()
        # The originals install as plain originals, byte for byte, with their content
        for key in (self.mission_key, self.runtime_key):
            self.assertEqual(after[f"originals/{key}/original.json"], before[f"originals/{key}/original.json"])
            self.assertNotIn("provenance", OriginalStore(self.area, key).read())
        self.assertEqual(after[f"originals/{self.mission_key}/files/{sha(VANILLA)}"], VANILLA)
        association = f"profiles/main/{self.mission_key}/"
        for name in ("baseline.json", "applied.json"):
            self.assertEqual(after[association + name], before[association + name])
        # The plan gets the revision after the archived one, because no current plan exists
        self.assertEqual(self.envelope(self.folder() / "plan.json")["revision"], 1)
        self.assertEqual(self.plans.load("main")["plan"]["plan_id"],
                         self.envelope(self.folder() / "plan.json")["plan"]["plan_id"])
        # One ledger entry for the carried record, with the derived operation ID and output true (D5)
        entry_id = restore_entry_id("restore-1", "op-3")
        entry = self.envelope(self.area / "ledger" / self.mission_key / f"{entry_id}.json")
        self.assertEqual((entry["operation_kind"], entry["output"], entry["carried_operation_id"], entry["revision"]),
                         ("profile_restore", True, "op-3", 0))
        self.assertEqual(entry["files"][0]["after_sha256"], sha(APPLIED))
        self.assertFalse((self.area / "ledger" / self.runtime_key).exists())
        self.assertIsNone(self.plans.load("main")["read_only_reason"])
        self.assert_clean("restore-1", 4)

    def test_same_manifest_keeps_the_original(self) -> None:
        self.write_all()
        backup = self.backup()
        original = (self.area / "originals" / self.mission_key / "original.json").read_bytes()
        preview = self.restore_service().preview("main", backup)
        self.assertEqual(preview["editor"]["rows"]["original_mission"], "keep")
        self.assertEqual(preview["editor"]["rows"]["original_runtime"], "keep")
        self.restore(backup)
        self.assertEqual((self.area / "originals" / self.mission_key / "original.json").read_bytes(), original)
        self.assertEqual(list((self.area / "originals" / self.mission_key).glob("superseded-*")), [])
        self.assertEqual(list(self.folder().glob("restored-*")), [])

    def test_different_original_not_shared_is_superseded(self) -> None:
        self.write_all()
        backup = self.backup()
        archived = OriginalStore(self.area, self.mission_key).read()
        current = self.replace_original(b"<areaflags>captured later</areaflags>")
        self.restore(backup)
        kept = self.envelope(self.area / "originals" / self.mission_key / "superseded-restore-1.json")
        self.assertEqual(kept["manifest_sha256"], current["manifest_sha256"])
        active = OriginalStore(self.area, self.mission_key).read_revision()
        self.assertEqual(active[0]["manifest_sha256"], archived["manifest_sha256"])
        self.assertEqual(active[1], 1)
        # Nothing is deleted: the content of the replaced original stays
        self.assertTrue((self.area / "originals" / self.mission_key / "files" / sha(b"<areaflags>captured later</areaflags>")).is_file())
        # The archived baseline was built on the archived original, so it installs
        self.assertEqual(self.envelope(self.folder() / "baseline.json")["originals"][0]["manifest_sha256"],
                         archived["manifest_sha256"])

    def test_different_original_shared_keeps_a_restored_copy(self) -> None:
        self.write_all()
        backup = self.backup()
        archived = OriginalStore(self.area, self.mission_key).read()
        current = self.replace_original(b"<areaflags>shared capture</areaflags>")
        # Another profile has an association folder for the mission key
        (self.area / "profiles" / "other" / self.mission_key).mkdir(parents=True)
        preview = self.restore_service().preview("main", backup)
        self.assertEqual(preview["editor"]["rows"]["shared_mission"], ["other"])
        self.restore(backup)
        self.assertEqual(OriginalStore(self.area, self.mission_key).read()["manifest_sha256"], current["manifest_sha256"])
        copy = self.envelope(self.folder() / "restored-mission-restore-1.json")
        self.assertEqual(copy["manifest_sha256"], archived["manifest_sha256"])
        self.assertEqual(list((self.area / "originals" / self.mission_key).glob("superseded-*")), [])
        # The baseline is built again from the active shared original (D5, D19)
        baseline = self.envelope(self.folder() / "baseline.json")
        self.assertEqual(baseline["originals"][0], {"target_key": self.mission_key,
                                                    "manifest_sha256": current["manifest_sha256"]})
        self.assertEqual(baseline["provenance"][-1]["source"], "profile_restore")
        self.assertEqual(baseline["revision"], 1)
        self.assertTrue((self.folder() / "files" / sha(b"<areaflags>shared capture</areaflags>")).is_file())

    def test_backup_without_an_original_invalidates_the_last_applied_record(self) -> None:
        # The backup is from before the first Apply, so the restored files differ from what the Apply wrote
        self.save_plan()
        self.write_mission(VANILLA)
        backup = self.backup(editor=False)
        self.write_mission(APPLIED)
        self.write_original(self.mission_key)
        self.write_records()
        before = self.tree()
        self.restore(backup)
        after = self.tree()
        association = f"profiles/main/{self.mission_key}/"
        self.assertNotIn(association + "applied.json", after)
        self.assertEqual(after[association + "applied.invalidated-20261016T180000Z.json"],
                         before[association + "applied.json"])
        # The original, the baseline and the plan stay; the ledger gets nothing, because nothing is carried
        for name in ("plan.json", "baseline.json"):
            self.assertEqual(after[association + name], before[association + name])
        self.assertEqual(after[f"originals/{self.mission_key}/original.json"],
                         before[f"originals/{self.mission_key}/original.json"])
        self.assertFalse((self.area / "ledger").exists())
        self.assert_clean("restore-1", 4)

    def replace_original(self, data: bytes) -> dict:
        """Replace the active mission original with another capture, as on a later first Apply."""
        folder = self.area / "originals" / self.mission_key
        (folder / "original.json").unlink()
        return self.write_original(self.mission_key, data=data)

    def assert_clean(self, operation_id: str, schema: int) -> None:
        """Assert the retired journal schema and that no staging file or stage folder is left."""
        journal = self.journals.load(self.journals.retired_path_for(operation_id))
        self.assertEqual((journal.schema_version, journal.phase), (schema, "COMMITTED"))
        self.assertFalse(any(self.area.rglob("*.restore-stage")))
        self.assertFalse(any(self.dayz.rglob("*.restore-stage")))
        self.assertFalse(any(self.paths.data.glob(".serverman-*-stage")))


if __name__ == "__main__":
    unittest.main()
