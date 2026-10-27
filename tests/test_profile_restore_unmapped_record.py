"""A carried last-applied record with a target outside the archived association (Architect 2026-10-17 07:28:52, 10.37).

The restore writes it under the dated invalidated name in the new association, with ledger entries only for the
targets that it can map. The load then reports `adoption_required`, and the sweep keeps its content.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from dayz_serverman.repositories.backup_editor_records import parse_record
from dayz_serverman.repositories.mission_map_commit_records import CommitRecords
from dayz_serverman.repositories.mission_map_journal_store import MissionMapJournalRepository
from dayz_serverman.repositories.mission_map_recovery_cleanup import cleanup_orphans
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore
from tests.profile_restore_policy_fixtures import APPLIED, OTHER, PREVIOUS, TIME, PolicyFixture, sha

# A target key of no archived original and no baseline
FOREIGN = "f6" * 16


class UnmappedRecordTests(PolicyFixture):
    """The record is kept as invalidated, so Apply needs adoption."""

    def test_an_unmappable_record_is_invalidated_and_keeps_its_content(self) -> None:
        self.write_records()
        store = AssociationRecordStore(self.area, "main", self.mission_key, "applied")
        record, revision = store.read()  # type: ignore[misc]
        foreign_after = {"target_key": FOREIGN, "path": "AI_Bandits/DynamicAIB.json", "existed": True,
                         "sha256": sha(OTHER), "owned_sha256": sha(OTHER)}
        foreign_before = {"target_key": FOREIGN, "path": "AI_Bandits/DynamicAIB.json", "existed": True,
                          "sha256": sha(OTHER), "size": len(OTHER)}
        fields = {name: value for name, value in record.items() if name != "manifest_sha256"}
        fields.update(operation_id="op-4", applied_at=TIME, files_after=[*record["files_after"], foreign_after],
                      files_before=[*record["files_before"], foreign_before])
        store.save(fields, {sha(PREVIOUS): PREVIOUS, sha(OTHER): OTHER}, revision)
        preview, _result = self.restore(self.backup(), "allocate_new")
        mission_key, _runtime_key = self.destination(preview)
        folder = self.folder(mission_key)
        self.assertFalse((folder / "applied.json").exists())
        [invalidated] = folder.glob("applied.invalidated-*.json")
        kept = parse_record("applied", invalidated.read_bytes())
        # The association is the new one; the target that cannot be mapped keeps its archived key
        self.assertEqual((kept["profile_id"], kept["mission_key"]), ("restored", mission_key))
        self.assertEqual({item["target_key"] for item in kept["files_after"]}, {mission_key, FOREIGN})
        self.assertEqual(kept["files_after"][0]["sha256"], sha(APPLIED))
        # Ledger entries go only to the destination targets that the restore maps
        ledger = self.area / "ledger"
        self.assertEqual([path.name for path in ledger.iterdir()], [mission_key])
        self.assertEqual(self.plans.load("restored")["read_only_reason"], "adoption_required")
        # A later sweep and the orphan cleanup keep the content that only this record names
        content = folder / "files" / sha(OTHER)
        self.assertTrue(content.is_file())
        journal = SimpleNamespace(records=[SimpleNamespace(target_path=self.paths.relative(folder / "baseline.json"))])
        CommitRecords(self.paths, MissionMapJournalRepository(self.paths.operations / "j"),  # type: ignore[arg-type]
                      lambda *_: None).sweep(journal)  # type: ignore[arg-type]
        cleanup_orphans(self.paths, self.locks)
        self.assertTrue(content.is_file())


if __name__ == "__main__":
    unittest.main()
