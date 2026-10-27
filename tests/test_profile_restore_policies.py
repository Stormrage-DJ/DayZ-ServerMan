"""The D5 rules of each destination policy of a restore as a new profile, with the remap (10.37)."""

from __future__ import annotations

import json
import shutil
import unittest

from dayz_serverman.domain.mission_map_records import copy_matches
from dayz_serverman.domain.restore_editor_texts import (
    APPLIED_MISMATCH, CARRIED, RESTORED_COPY, RUNTIME_NO_ORIGINAL, SHARED,
)
from dayz_serverman.repositories.backup_editor_records import parse_record
from dayz_serverman.repositories.mission_map_records import OriginalStore
from tests.profile_restore_policy_fixtures import OTHER, PREVIOUS, VANILLA, PolicyAssertions, PolicyFixture, sha


class AllocateNewTests(PolicyFixture, PolicyAssertions):
    """A new mission key and a new runtime key: every original installs as a restore copy."""

    def test_allocate_new_installs_restore_copies_and_remapped_records(self) -> None:
        self.write_records()
        backup = self.backup()
        before = OriginalStore(self.area, self.mission_key).read()
        preview, result = self.restore(backup, "allocate_new")
        mission_key, runtime_key = self.destination(preview)
        self.assertNotIn(mission_key, (self.mission_key,))
        self.assertEqual(preview["editor_notes"][:1], [CARRIED])
        self.assertIn(RUNTIME_NO_ORIGINAL, preview["editor_notes"])
        # Both originals are copies with the provenance case restore and the archived source
        for key, source in ((mission_key, self.mission_key), (runtime_key, self.runtime_key)):
            copied = OriginalStore(self.area, key).read()
            link = copied["provenance"]
            self.assertEqual((link["case"], link["source_key"], link["operation_id"]), ("restore", source, "pr-1"))
            archived = before if source == self.mission_key else OriginalStore(self.area, self.runtime_key).read()
            self.assertTrue(copy_matches(copied, archived))
        self.assert_remapped(mission_key, runtime_key, applied="install")
        self.assertEqual(json.loads((self.folder(mission_key) / "plan.json").read_text())["revision"], 1)
        # The records of main stay unchanged
        self.assertEqual(OriginalStore(self.area, self.mission_key).read(), before)
        self.assertEqual(self.plans.load("restored")["read_only_reason"], None)
        self.assertEqual(result["profile"]["profile_id"], "restored")

    def test_a_record_that_does_not_match_the_restored_files_is_invalidated(self) -> None:
        self.write_records(after=OTHER)
        preview, _result = self.restore(self.backup(), "allocate_new")
        mission_key, _runtime = self.destination(preview)
        self.assertIn(APPLIED_MISMATCH, preview["editor_notes"])
        folder = self.folder(mission_key)
        self.assertFalse((folder / "applied.json").exists())
        invalidated = list(folder.glob("applied.invalidated-*.json"))
        self.assertEqual(len(invalidated), 1)
        self.assertEqual(parse_record("applied", invalidated[0].read_bytes())["profile_id"], "restored")
        # The ledger entry is added also for an invalidated record, and the load then needs adoption
        self.assertEqual(len(list((self.area / "ledger" / mission_key).iterdir())), 1)
        self.assertEqual(self.plans.load("restored")["read_only_reason"], "adoption_required")


class PreserveOriginalTests(PolicyFixture, PolicyAssertions):
    """The archived mission key, with the D5 table and its shared definition."""

    def test_the_same_key_without_an_original_installs_a_plain_original(self) -> None:
        self.write_records()
        backup = self.backup()
        archived = OriginalStore(self.area, self.mission_key).read()
        self.delete_main(mission=True, records=True)
        preview, _result = self.restore(backup, "preserve_original")
        mission_key, runtime_key = self.destination(preview)
        self.assertEqual(mission_key, self.mission_key)
        # A backup original for the same key is the capture of that location (Architect 07:28:52)
        self.assertEqual(OriginalStore(self.area, mission_key).read(), archived)
        self.assertEqual(OriginalStore(self.area, runtime_key).read()["provenance"]["case"], "restore")
        self.assert_remapped(mission_key, runtime_key, applied="install")

    def test_another_key_after_a_move_of_the_dayz_folder_installs_a_restore_copy(self) -> None:
        self.write_records()
        backup = self.backup()
        archived = OriginalStore(self.area, self.mission_key).read()
        self.delete_main()
        moved = self.root / "moved dayz"
        self.dayz.rename(moved)
        self.dayz, self.settings.value.dayz_root = moved, str(moved)
        shutil.rmtree(moved.joinpath(*archived["mission_root"].split("\\")))
        preview, _result = self.restore(backup, "preserve_original")
        mission_key, runtime_key = self.destination(preview)
        self.assertNotEqual(mission_key, self.mission_key)
        copied = OriginalStore(self.area, mission_key).read()
        self.assertEqual((copied["provenance"]["case"], copied["provenance"]["source_key"]),
                         ("restore", self.mission_key))
        self.assertTrue(copy_matches(copied, archived))
        self.assert_remapped(mission_key, runtime_key, applied="install")

    def test_a_different_unshared_original_is_superseded(self) -> None:
        self.write_records()
        backup = self.backup()
        self.delete_main(mission=True)
        shutil.rmtree(self.area / "profiles")
        shutil.rmtree(self.area / "originals" / self.mission_key)
        current = self.write_original(self.mission_key, "mission", OTHER, "2026-10-15T00:00:00Z")
        preview, _result = self.restore(backup, "preserve_original")
        kept = OriginalStore(self.area, self.mission_key).folder / "superseded-pr-1.json"
        self.assertEqual(parse_record("original", kept.read_bytes()), current)
        active = OriginalStore(self.area, self.mission_key).read()
        self.assertNotIn("provenance", active)
        self.assertEqual(active["files"][0]["sha256"], sha(VANILLA))
        self.assert_remapped(*self.destination(preview), applied="install")

    def test_a_shared_original_stays_and_the_backup_original_is_a_restored_copy(self) -> None:
        self.write_records()
        backup = self.backup()
        # Profile main keeps its association folder for the key, so the target is shared (D2)
        self.delete_main(mission=True)
        shutil.rmtree(self.area / "originals" / self.mission_key)
        current = self.write_original(self.mission_key, "mission", OTHER, "2026-10-15T00:00:00Z")
        preview, _result = self.restore(backup, "preserve_original")
        mission_key, runtime_key = self.destination(preview)
        self.assertEqual(OriginalStore(self.area, mission_key).read(), current)
        copies = list(self.folder(mission_key).glob("restored-mission-*.json"))
        self.assertEqual(len(copies), 1)
        # The baseline is built again from the active original, so it names the current original
        baseline = parse_record("baseline", (self.folder(mission_key) / "baseline.json").read_bytes())
        self.assertEqual(baseline["originals"][0]["manifest_sha256"], current["manifest_sha256"])
        # Row 5 names the profiles of the shared mission, so row 6 is left out (Product Owner 20:58:44)
        self.assertIn(RESTORED_COPY.format(profiles="main (deleted profile)"), preview["editor_notes"])
        self.assertFalse(any(note.startswith(SHARED.split(":")[0]) for note in preview["editor_notes"]))


class ReplaceExistingTests(PolicyFixture, PolicyAssertions):
    """The existing mission key of other profiles, always shared; their records stay unchanged."""

    def test_the_same_original_stays_and_the_records_of_main_stay(self) -> None:
        self.write_records()
        backup = self.backup()
        main = sorted(path.read_bytes() for path in self.folder(self.mission_key, "main").rglob("*") if path.is_file())
        original = OriginalStore(self.area, self.mission_key).read()
        preview, _result = self.restore(backup, "replace_existing")
        mission_key, runtime_key = self.destination(preview)
        self.assertEqual(mission_key, self.mission_key)
        self.assertEqual(OriginalStore(self.area, mission_key).read(), original)
        self.assert_remapped(mission_key, runtime_key, applied="install")
        self.assertEqual(sorted(path.read_bytes() for path in self.folder(self.mission_key, "main").rglob("*")
                                if path.is_file()), main)

    def test_a_different_original_stays_active_and_the_backup_original_is_kept_inactive(self) -> None:
        self.write_records()
        backup = self.backup()
        shutil.rmtree(self.area / "originals" / self.mission_key)
        current = self.write_original(self.mission_key, "mission", PREVIOUS, "2026-10-15T00:00:00Z")
        preview, _result = self.restore(backup, "replace_existing")
        self.assertEqual(OriginalStore(self.area, self.mission_key).read(), current)
        self.assertEqual(len(list(self.folder(self.mission_key).glob("restored-mission-*.json"))), 1)
        self.assertFalse((OriginalStore(self.area, self.mission_key).folder / "superseded-pr-1.json").exists())
        self.assertEqual(preview["storage_policy"], "replace_existing")


if __name__ == "__main__":
    unittest.main()
