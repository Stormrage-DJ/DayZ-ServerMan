"""Apply of leaf 8.7: publication, records, the applied record version 2 and reapply without change."""

from __future__ import annotations

import json
import unittest

from dayz_serverman.domain.mission_map_applied import APPLIED_VERSION
from dayz_serverman.domain.mission_map_plan import configuration_fingerprint
from dayz_serverman.repositories.mission_map_layout import MISSION_FILE_SET, ledger_folder
from dayz_serverman.repositories.mission_map_owned import WRITABLE_MISSION_FILES
from tests.mission_map_apply_support import ApplyFixture
from tests.mission_map_mapgroup_support import zone
from tests.test_mission_map_infected import encounter

# One included infected encounter whose event the mission offers
INFECTED = encounter("e1", x=600.0, z=600.0, radius=40.0)
# Files that no Apply names: the captured-only files (Architect 2026-10-14 12:16:06)
CAPTURED_ONLY = {"env/wolf_territories.xml", "env/bear_territories.xml"}


class FirstApplyTests(ApplyFixture):
    """The first Apply captures the original, creates the baseline and writes the candidates."""

    def test_first_apply_publishes_the_reviewed_candidates_and_records(self) -> None:
        """Files equal the candidates; original, baseline, applied version 2 and ledger agree with them."""
        self.save([zone("rich"), INFECTED], 25)
        review = self.review()
        before = dict(review.inputs.managed)
        self.events.clear()
        result = self.run_apply(fingerprint=review.fingerprint)
        self.assertEqual(result["result"], "COMMITTED")
        contents = review.candidates.contents()
        changed = [path for path in WRITABLE_MISSION_FILES if contents[path] != before[path]]
        self.assertEqual(result["files"], changed)
        self.assertEqual(changed, ["mapgroupproto.xml", "mapgrouppos.xml", "mapgroupdirt.xml",
                                   "env/zombie_territories.xml"])
        for path in WRITABLE_MISSION_FILES:
            self.assertEqual((self.mission / path).read_bytes() if (self.mission / path).exists() else None,
                             contents[path], path)
        original, (baseline, revision), (applied, applied_revision) = self.records()
        self.assertEqual([item["path"] for item in original["files"]], list(MISSION_FILE_SET))
        self.assertEqual({item["path"]: item["sha256"] for item in original["files"]},
                         {path: self.sha_or_none(before[path]) for path in MISSION_FILE_SET})
        self.assertEqual((revision, [item["source"] for item in baseline["provenance"]]), (0, ["original"]))
        self.assertEqual(applied_revision, 0)
        # The applied record stores the plan, its global value and the realization (6.9 ruling, D3)
        plan = self.plans.load("main")["plan"]
        self.assertEqual((applied["global_abundance"], applied["plan_objects"]), (25, plan["objects"]))
        self.assertEqual(applied["configuration_fingerprint"], configuration_fingerprint(plan))
        self.assertEqual([item["name"] for item in applied["generated_groups"]],
                         [clone.name for clone in review.candidates.mapgroups.clones])
        self.assertTrue(applied["generated_groups"] and applied["generated_groups"][0]["detached"])
        # The commit captured the original, which holds no reserved element (D8)
        self.assertEqual(applied["generated_groups_before"], [])
        self.assertEqual([item["path"] for item in applied["files_after"]], list(WRITABLE_MISSION_FILES))
        self.assertEqual([item["path"] for item in applied["files_before"]], changed)
        raw = json.loads((self.folder() / "applied.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], APPLIED_VERSION)
        # The external versions are stored before the write: the "files before" content
        for item in applied["files_before"]:
            if item["existed"]:
                self.assertEqual((self.folder() / "files" / item["sha256"]).read_bytes(), before[item["path"]])
        entries = list(ledger_folder(self.area, self.key()).glob("*.json"))
        self.assertEqual(len(entries), 1)
        ledger = json.loads(entries[0].read_text(encoding="utf-8"))
        self.assertEqual([item["path"] for item in ledger["files"]], changed)
        # The lock order of the commit step: lane caller, mutex, STOPPED, writer, association
        self.assertEqual(self.events[:4], ["mutex", "stopped", "writer", "association"])
        self.assertFalse(list(self.journals.root.glob("*.json")))

    def test_no_record_or_journal_names_a_captured_only_file(self) -> None:
        """Groups, ledger items, "files before" and "files after" never name a wolf or bear file."""
        self.write("env/bear_territories.xml", b"<territory-type/>")
        self.save([zone("rich"), INFECTED], 25)
        self.run_apply()
        _original, _baseline, (applied, _revision) = self.records()
        named = {item["path"] for item in applied["files_after"] + applied["files_before"]}
        entry = json.loads(next(ledger_folder(self.area, self.key()).glob("*.json")).read_text(encoding="utf-8"))
        named |= {item["path"] for item in entry["files"]}
        completed = next((self.journals.root / "completed").glob("*.json"))
        journal = json.loads(completed.read_text(encoding="utf-8"))
        named |= {group["relative_path"] for group in journal["groups"]}
        self.assertFalse(named & CAPTURED_ONLY)
        # A wolf edit after the first Apply gives no affected file and the next Apply does not write it
        self.write("env/wolf_territories.xml", b"<territory-type><edited/></territory-type>")
        self.assertEqual(self.run_apply()["result"], "UNCHANGED")
        self.assertEqual((self.mission / "env/wolf_territories.xml").read_bytes(),
                         b"<territory-type><edited/></territory-type>")

    @staticmethod
    def sha_or_none(data: bytes | None) -> str | None:
        """Return SHA-256 of bytes, or None."""
        import hashlib
        return None if data is None else hashlib.sha256(data).hexdigest()


class ReapplyTests(ApplyFixture):
    """An unchanged reapply writes nothing; a plan change without a file change writes only records."""

    def test_unchanged_reapply_changes_nothing(self) -> None:
        """The second Apply of the same plan writes no file and no record, and it still proves STOPPED."""
        self.save([zone("rich"), INFECTED], 25)
        self.run_apply()
        before = self.snapshot()
        self.events.clear()
        result = self.run_apply()
        self.assertEqual((result["result"], result["files"]), ("UNCHANGED", []))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.events, ["mutex", "stopped", "release mutex"])

    def test_plan_change_without_a_file_change_commits_records_only(self) -> None:
        """A zone without placements changes no file; the applied record names the new plan, no ledger entry."""
        self.save([zone("rich"), INFECTED], 25)
        self.run_apply()
        files = self.snapshot()[0]
        _original, (_baseline, baseline_revision), (first, _revision) = self.records()
        empty = dict(zone("normal", 50.0, 50.0, 10.0), id="empty")
        self.save([zone("rich"), INFECTED, empty], 25, expected=0)
        result = self.run_apply()
        self.assertEqual((result["result"], result["files"]), ("COMMITTED", []))
        self.assertEqual(self.snapshot()[0], files)
        _original, (_baseline, revision), (applied, applied_revision) = self.records()
        self.assertEqual((revision, applied_revision, applied["plan_revision"]), (baseline_revision, 1, 1))
        # Only plan fields change, so the restore point of the replaced record stays (Architect 01:18:47)
        self.assertEqual((applied["files_before"], applied["files_after"]), (first["files_before"], first["files_after"]))
        self.assertEqual(applied["generated_groups_before"], first["generated_groups_before"])
        for item in applied["files_before"]:
            self.assertTrue((self.folder() / "files" / item["sha256"]).is_file())
        self.assertEqual(len(list(ledger_folder(self.area, self.key()).glob("*.json"))), 1)
        objects = self.review().answer["objects"]
        self.assertEqual((objects["previous"], objects["added"], objects["changed"]), (True, [], []))
        self.assertEqual(self.run_apply()["result"], "UNCHANGED")


if __name__ == "__main__":
    unittest.main()
