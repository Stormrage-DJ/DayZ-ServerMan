"""Restore previous state and Restore original of leaf 8.8: files, records, realization and the ledger."""

from __future__ import annotations

import json
import unittest

from dayz_serverman.domain.mission_map_applied import APPLIED_VERSION
from dayz_serverman.domain.mission_map_restore_texts import SHARED_ORIGINAL_TEXT
from dayz_serverman.repositories.mission_map_owned import WRITABLE_MISSION_FILES
from tests.mission_map_mapgroup_support import zone
from tests.mission_map_restore_support import INFECTED, PLAN, RestoreFixture, sha

# The fields of an applied record that a restore leaves null: it has no plan (D3)
PLAN_FIELDS = ("plan_id", "plan_revision", "configuration_fingerprint", "global_abundance", "plan_objects")


class TwoApplies(RestoreFixture):
    """A first Apply that captures the original, then a second Apply of another plan."""

    def two_applies(self) -> tuple[dict, dict, dict, dict]:
        """Return the mission state before, after the first and after the second Apply, and the second record."""
        before = self.mission_state()
        self.save(PLAN, 25)
        self.run_apply()
        first = self.mission_state()
        self.save([zone("very_rich"), INFECTED], 50, expected=0)
        self.run_apply()
        second = self.mission_state()
        return before, first, second, self.records()[2][0]


class RestorePreviousTests(TwoApplies):
    """Restore previous state writes the "files before" of the last-applied record (R16)."""

    def test_restore_previous_after_an_apply_returns_the_files_before(self) -> None:
        """Files equal the state after the first Apply; the record and the ledger describe the restore."""
        _before, first, second, applied_before = self.two_applies()
        _original, (baseline_before, baseline_revision), _applied = self.records()
        self.events.clear()
        result = self.run_restore("previous")
        changed = [path for path in WRITABLE_MISSION_FILES if first[path] != second[path]]
        self.assertEqual((result["result"], result["files"], result["backups"]), ("COMMITTED", changed, []))
        self.assertEqual(self.mission_state(), first)
        _original, (baseline, revision), (applied, applied_revision) = self.records()
        # The working baseline does not change
        self.assertEqual((baseline, revision), (baseline_before, baseline_revision))
        self.assertEqual(applied_revision, 2)
        self.assertEqual((applied["operation_kind"], applied["operation_id"]), ("restore_previous", "restore0001"))
        self.assertEqual([applied[name] for name in PLAN_FIELDS], [None] * len(PLAN_FIELDS))
        # Realization of the written bytes, and of the state that the restore replaced (Architect 01:18:47)
        self.assertEqual(applied["generated_groups"], applied_before["generated_groups_before"])
        self.assertEqual(applied["generated_groups_before"], applied_before["generated_groups"])
        self.assertTrue(applied["generated_groups"])
        self.assertNotEqual(applied["generated_groups"], applied["generated_groups_before"])
        self.assertEqual([item["path"] for item in applied["files_after"]], list(WRITABLE_MISSION_FILES))
        self.assertEqual({item["path"]: item["sha256"] for item in applied["files_after"]},
                         {path: sha(first[path]) for path in WRITABLE_MISSION_FILES})
        # "Files before" keeps the replaced output with content, so a later restore can return to it
        self.assertEqual([item["path"] for item in applied["files_before"]], changed)
        for item in applied["files_before"]:
            self.assertEqual((self.folder() / "files" / item["sha256"]).read_bytes(), second[item["path"]])
        raw = json.loads((self.folder() / "applied.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], APPLIED_VERSION)
        # The ledger entry is not editor output, so the capture check never matches it (D3)
        entry = self.ledger_entries()["restore0001"]
        self.assertEqual((entry["operation_kind"], entry["output"]), ("restore_previous", False))
        self.assertEqual([item["path"] for item in entry["files"]], changed)
        self.assertEqual(self.events[:4], ["mutex", "stopped", "writer", "association"])
        self.assertFalse(list(self.journals.root.glob("*.json")))
        self.assertFalse(list(self.paths.root.rglob("*.staged-*")))

    def test_restore_previous_twice_returns_to_the_state_before_the_restore(self) -> None:
        """The second restore writes the files before the first one, with the realization of those bytes."""
        _before, _first, second, applied_before = self.two_applies()
        self.run_restore("previous")
        self.run_restore("previous")
        self.assertEqual(self.mission_state(), second)
        applied = self.records()[2][0]
        self.assertEqual(applied["generated_groups"], applied_before["generated_groups"])
        self.assertEqual(applied["generated_groups_before"], applied_before["generated_groups_before"])
        self.assertEqual(len(self.ledger_entries()), 4)

    def test_restore_previous_after_the_first_apply_writes_the_captured_files(self) -> None:
        """The first Apply captured the original, so the restored bytes hold no generated group (`[]`)."""
        before = self.mission_state()
        self.save(PLAN, 25)
        self.run_apply()
        self.run_restore("previous")
        self.assertEqual(self.mission_state(), before)
        applied = self.records()[2][0]
        self.assertEqual(applied["generated_groups"], [])
        self.assertTrue(applied["generated_groups_before"])

    def test_restore_previous_without_a_file_change_writes_nothing(self) -> None:
        """When the files already equal the restore point, the restore proves STOPPED and writes nothing."""
        before = self.mission_state()
        self.save(PLAN, 25)
        self.run_apply()
        # The admin puts the captured files back by hand
        for path in WRITABLE_MISSION_FILES:
            self.write(path, before[path])
        snapshot = self.snapshot()
        self.events.clear()
        result = self.run_restore("previous")
        self.assertEqual((result["result"], result["files"]), ("UNCHANGED", []))
        self.assertEqual(self.events[:2], ["mutex", "stopped"])
        self.assertEqual(self.snapshot(), snapshot)


class RestoreOriginalTests(TwoApplies):
    """Restore original writes the writable files of the protected original: the fallback of R16."""

    def test_restore_original_is_the_fallback(self) -> None:
        """Files equal the original; the record holds `[]`; the baseline keeps its files and provenance."""
        before, _first, second, applied_before = self.two_applies()
        _original, (baseline_before, baseline_revision), _applied = self.records()
        result = self.run_restore("original")
        changed = [path for path in WRITABLE_MISSION_FILES if before[path] != second[path]]
        self.assertEqual((result["result"], result["files"]), ("COMMITTED", changed))
        self.assertEqual(self.mission_state(), before)
        _original, (baseline, revision), (applied, _revision) = self.records()
        self.assertEqual(applied["operation_kind"], "restore_original")
        self.assertEqual([applied[name] for name in PLAN_FIELDS], [None] * len(PLAN_FIELDS))
        self.assertEqual(applied["generated_groups"], [])
        self.assertEqual(applied["generated_groups_before"], applied_before["generated_groups"])
        # Restore original keeps the provenance list and adds its entry; the baseline files stay
        self.assertEqual(revision, baseline_revision + 1)
        self.assertEqual(baseline["files"], baseline_before["files"])
        self.assertEqual(baseline["provenance"][:-1], baseline_before["provenance"])
        entry = baseline["provenance"][-1]
        self.assertEqual((entry["source"], entry["operation_id"], entry["paths"]),
                         ("restore_original", "restore0001", list(WRITABLE_MISSION_FILES)))
        self.assertEqual((applied["baseline_revision"], applied["baseline_manifest_sha256"]),
                         (revision, baseline["manifest_sha256"]))
        ledger = self.ledger_entries()["restore0001"]
        self.assertEqual((ledger["operation_kind"], ledger["output"]), ("restore_original", False))

    def test_restore_original_again_writes_nothing_and_previous_returns(self) -> None:
        """A second Restore original is UNCHANGED; Restore previous state then returns the Apply output."""
        _before, _first, second, applied_before = self.two_applies()
        self.run_restore("original")
        snapshot = self.snapshot()
        self.assertEqual(self.run_restore("original")["result"], "UNCHANGED")
        self.assertEqual(self.snapshot(), snapshot)
        self.run_restore("previous")
        self.assertEqual(self.mission_state(), second)
        self.assertEqual(self.records()[2][0]["generated_groups"], applied_before["generated_groups"])

    def test_restore_original_over_original_files_commits_only_records(self) -> None:
        """After Restore previous to the captured files, Restore original changes no file and adds its record."""
        before = self.mission_state()
        self.save(PLAN, 25)
        self.run_apply()
        self.run_restore("previous")
        entries = len(self.ledger_entries())
        result = self.run_restore("original")
        self.assertEqual((result["result"], result["files"]), ("COMMITTED", []))
        self.assertEqual(self.mission_state(), before)
        applied = self.records()[2][0]
        self.assertEqual((applied["operation_kind"], applied["files_before"]), ("restore_original", []))
        self.assertEqual(applied["generated_groups"], [])
        # No file changed, so no ledger entry (8.7, Architect 01:18:47)
        self.assertEqual(len(self.ledger_entries()), entries)

    def test_apply_after_a_restore_starts_from_the_restored_state(self) -> None:
        """The next preview lists every object as added and no external change; its Apply realizes from `[]`."""
        self.two_applies()
        self.run_restore("original")
        review = self.review()
        self.assertEqual((review.answer["objects"]["previous"], review.answer["external_changes"]), (False, []))
        self.assertEqual([item["id"] for item in review.answer["objects"]["added"]], ["zone", "e1"])
        self.assertEqual(self.run_apply()["result"], "COMMITTED")
        self.assertEqual(self.records()[2][0]["generated_groups_before"], [])


class RestorePreviewTests(TwoApplies):
    """The preview reads only and binds every input of the restore."""

    def test_preview_answer_and_no_write(self) -> None:
        """Mode, replaced operation, affected files, realization and the family only for Restore original."""
        _before, first, second, applied_before = self.two_applies()
        snapshot = self.snapshot()
        answer = self.restorer.preview("main", "previous")
        self.assertEqual(self.snapshot(), snapshot)
        self.assertEqual((answer["mode"], answer["operation_kind"], answer["refused"]),
                         ("previous", "restore_previous", False))
        self.assertEqual(answer["replaces"]["operation_id"], applied_before["operation_id"])
        self.assertIsNone(answer["family"])
        self.assertEqual([item["path"] for item in answer["affected_files"]],
                         [path for path in WRITABLE_MISSION_FILES if first[path] != second[path]])
        self.assertEqual(answer["realization"], {"generated_groups": applied_before["generated_groups_before"],
                                                 "generated_groups_before": applied_before["generated_groups"]})
        original = self.restorer.preview("main", "original")
        self.assertTrue(original["family"]["verified"])
        self.assertEqual(original["realization"], {"generated_groups": [],
                                                   "generated_groups_before": applied_before["generated_groups"]})
        # Profile "second" resolves to the same mission: the target is shared (A4), and the preview says so (D5)
        self.assertEqual([item["profile_id"] for item in original["other_profiles"]], ["second"])
        self.assertEqual((original["original"]["active"], original["original"]["shared"]), (True, True))
        self.assertEqual(original["original"]["text"], SHARED_ORIGINAL_TEXT)
        del self.profiles.records["second"]
        alone = self.restorer.preview("main", "original")
        self.assertEqual((alone["other_profiles"], alone["original"]["shared"], alone["original"]["text"]),
                         ([], False, None))
        self.assertNotEqual(original["fingerprint"], answer["fingerprint"])

    def test_fingerprint_binds_the_files_and_the_records(self) -> None:
        """A file edit, a captured-only edit and a record change: only the first and the last stale the review."""
        self.two_applies()
        fingerprint = self.restore_review("previous").fingerprint
        self.write("env/wolf_territories.xml", b"<territory-type/>")
        self.assertEqual(self.restore_review("previous").fingerprint, fingerprint)
        self.write("areaflags.map", (self.mission / "areaflags.map").read_bytes() + b"\0")
        self.assertNotEqual(self.restore_review("previous").fingerprint, fingerprint)
        self.write("areaflags.map", (self.mission / "areaflags.map").read_bytes()[:-1])
        self.assertEqual(self.restore_review("previous").fingerprint, fingerprint)
        self.run_restore("previous")
        self.assertNotEqual(self.restore_review("previous").fingerprint, fingerprint)


class SharedTargetTests(TwoApplies):
    """Restore original of another profile of the target, before its own first Apply (A4, D3)."""

    def test_profile_without_records_restores_the_shared_original(self) -> None:
        """Profile main is named; every overwritten file differs from the original, so each is backed up first."""
        before, _first, second, _applied = self.two_applies()
        answer = self.restorer.preview("second", "original")
        self.assertEqual([item["profile_id"] for item in answer["other_profiles"]], ["main"])
        self.assertTrue(answer["original"]["shared"])
        changed = [path for path in WRITABLE_MISSION_FILES if before[path] != second[path]]
        self.assertEqual(self.run_restore("original", answer["fingerprint"], "second")["backups"],
                         [{"path": path, "sha256": sha(second[path])} for path in changed])
        self.assertEqual(self.mission_state(), before)
        _original, (baseline, revision), (applied, _revision) = self.records("second")
        self.assertEqual((revision, [entry["source"] for entry in baseline["provenance"]]),
                         (0, ["original", "replaced", "restore_original"]))
        self.assertEqual((applied["generated_groups"], applied["generated_groups_before"]), ([], None))
        # Profile main sees the restore as external changes of its own output
        self.assertEqual(self.review().answer["choices"]["external_changes"], changed)


if __name__ == "__main__":
    unittest.main()
