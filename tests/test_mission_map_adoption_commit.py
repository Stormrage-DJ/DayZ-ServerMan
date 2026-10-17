"""Leaf 10.35: the adoption commit of a true move, a copy after a path change and a copy after a capture refusal."""

from __future__ import annotations

import unittest

from dayz_serverman.application.mission_map_apply_choices import ApplyRefused
from dayz_serverman.application.mission_map_preview import PreviewStale
from dayz_serverman.domain import mission_map_adoption_texts as texts
from dayz_serverman.repositories.mission_map_commit_records import CommitRecords, RecordUpdate
from dayz_serverman.repositories.mission_map_journal import MapJournal
from dayz_serverman.repositories.mission_map_layout import association_folder, history_name
from dayz_serverman.repositories.mission_map_ledger import LedgerStore
from dayz_serverman.repositories.mission_map_plans import retired_marker
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore, OriginalStore
from tests.mission_map_adoption_commit_support import CommitFixture
from tests.mission_map_adoption_support import MOMENT
from tests.mission_map_preview_support import sha
from tests.mission_map_mapgroup_support import zone
from tests.test_mission_map_preview import tree


class MoveTests(CommitFixture):
    """A true move copies the records to the new key and retires the old folder of this profile (OP-3, OP-5)."""

    def test_true_move(self) -> None:
        """The new key gets the original, the plan, the baseline, the last apply and the repeated output."""
        operation = self.applied()
        source = self.key()
        self.move_root()
        key = self.key()
        source_records = tree(association_folder(self.area, "main", source))
        # A source-key output entry whose after digest equals an unchanged file of the original is not repeated
        original = OriginalStore(self.area, source).read()
        unchanged = next(item for item in original["files"] if item["existed"] and tree(self.mission).get(
            item["path"].replace("/", "\\")) is not None and sha(tree(self.mission)[item["path"].replace(
                "/", "\\")]) == item["sha256"])
        LedgerStore(self.area).create({
            "operation_id": "op-revert", "operation_kind": "apply", "recorded_at": "2026-10-10T12:00:00Z",
            "profile_id": "main", "target_class": "mission", "target_key": source, "display_path": "x",
            "mission_root": "mpmissions\\x", "output": True, "carried_operation_id": None,
            "files": [{"path": unchanged["path"], "before_sha256": "0" * 64, "after_sha256": unchanged["sha256"],
                       "baseline_sha256": None, "owned_before_sha256": None, "owned_after_sha256": None,
                       "owned_baseline_sha256": None}]})
        original_revalidate = self.reader.revalidate

        def revalidate(*args: object) -> object:
            self.events.append("revalidate")
            return original_revalidate(*args)
        self.reader.revalidate = revalidate  # type: ignore[method-assign]
        result = self.adopt()
        self.assertEqual((result["case"], result["result"], result["history_name"]), ("move", "COMMITTED", None))
        # The lock order: the mutex, then the review again twice; no STOPPED proof although the server runs (AR-3)
        self.assertEqual(self.events, ["mutex", "revalidate", "revalidate", "mutex released"])
        moved = OriginalStore(self.area, key).read()
        kept = OriginalStore(self.area, source).read()
        self.assertEqual(moved["provenance"], {"case": "move", "source_key": source, "operation_id": "adopt-op1",
                                               "source_manifest_sha256": kept["manifest_sha256"]})
        # The copied original keeps the capture fields of its source (Architect 2026-10-16 16:49:30, evidence 10.8)
        for name in ("captured_at", "profile_id", "operation_id", "files"):
            self.assertEqual(moved[name], kept[name])
        baseline = AssociationRecordStore(self.area, "main", key, "baseline").read()[0]
        applied = AssociationRecordStore(self.area, "main", key, "applied").read()[0]
        self.assertEqual(baseline["originals"], [{"target_key": key, "manifest_sha256": moved["manifest_sha256"]}])
        self.assertEqual((applied["operation_id"], applied["mission_key"], applied["baseline_manifest_sha256"]),
                         (operation, key, baseline["manifest_sha256"]))
        self.assertEqual({item["target_key"] for item in applied["files_after"]}, {key})
        entry = LedgerStore(self.area).read(key, "adopt-op1")
        source_entry = LedgerStore(self.area).read(source, operation)
        self.assertEqual((entry["operation_kind"], entry["output"]), ("adopt", True))
        self.assertEqual(entry["files"], source_entry["files"])
        self.assertNotIn(unchanged["path"], [item["path"] for item in entry["files"]])
        # The old folder keeps its files and is retired; the source key is unchanged
        self.assertEqual(retired_marker(association_folder(self.area, "main", source))["new_mission_key"], key)
        after = tree(association_folder(self.area, "main", source))
        self.assertEqual({name: data for name, data in after.items() if name != "retired.json"}, source_records)
        load = self.load()
        self.assertEqual((load["read_only"], load["read_only_reason"], load["revision"], load["adoption"]),
                         (False, None, 1, None))
        # Apply works on the new key: the preview captures nothing and refuses nothing for the moved files
        self.assertEqual(self.preview.preview("main")["refusals"], [])


class PreflightTests(CommitFixture):
    """The adoption preflight checks the original kinds; only an adopt journal carries a last apply of the source."""

    def test_preflight_calls_check_original_kinds(self) -> None:
        """The preflight checks every original kind before the first write (ruling (d), evidence 10.8)."""
        self.applied()
        self.move_root()
        from unittest import mock
        from dayz_serverman.application import mission_map_adoption_commit as module
        with mock.patch.object(module, "check_original_kinds", wraps=module.check_original_kinds) as spy:
            self.adopt()
        self.assertEqual(spy.call_count, 1)
        self.assertEqual([kind for kind, *_rest in spy.call_args.args[2]], ["copied_original"])

    def test_only_an_adopt_journal_keeps_the_operation_of_a_last_apply(self) -> None:
        """A restore journal refuses a last-applied record of another operation; an adopt journal takes it."""
        self.applied()
        key = self.key()
        found = AssociationRecordStore(self.area, "main", key, "applied").read()
        folder = association_folder(self.area, "main", key)
        contents = {item["sha256"]: (folder / "files" / item["sha256"]).read_bytes()
                    for item in found[0]["files_before"] if item["existed"]}
        update = RecordUpdate("applied", self.paths.relative(folder / "applied.json"), found[0], found[1], contents)
        for kind, refused in (("restore_original", True), ("adopt", False)):
            journal = MapJournal(f"op-{kind.replace('_', '-')}", kind, "main", found[0]["mission_root"], key, None,
                                 None, str(self.root), None, None, None, None, 1, 1, [], [])
            records = CommitRecords(self.paths, self.journals, lambda _phase, _index: None)
            with self.subTest(kind=kind):
                if refused:
                    with self.assertRaisesRegex(ValueError, "operation differs"):
                        records.stage(journal, [update])
                else:
                    records.stage(journal, [update])
                    self.assertEqual([entry.kind for entry in journal.records], ["applied"])
                    self.assertTrue(records.rollback(journal))


class PathChangeCopyTests(CommitFixture):
    """A copy after a path change: the plan of this profile is the next revision (D-1, PQ-1)."""

    def test_copy_after_a_path_change(self) -> None:
        """The copy gets its original and baseline, no last apply; the source and its ledger stay unchanged."""
        operation = self.applied()
        # The source plan names the source original, as the editor saves it after an Apply (D3)
        plan = self.load()["plan"]
        plan["records"]["originals"] = [OriginalStore(self.area, self.key()).identity()]
        self.plans.save("main", plan, self.load()["revision"])
        source = self.key()
        self.move_root(keep_old=True)
        key = self.key()
        before = (tree(OriginalStore(self.area, source).folder), tree(self.area / "ledger" / source))
        self.assertEqual(self.adopt()["case"], "copy")
        copied = OriginalStore(self.area, key).read()
        self.assertEqual(copied["provenance"]["case"], "copy")
        self.assertIsNone(AssociationRecordStore(self.area, "main", key, "applied").read())
        baseline = AssociationRecordStore(self.area, "main", key, "baseline").read()[0]
        self.assertEqual(baseline["provenance"][0]["source"], "original")
        self.assertEqual(LedgerStore(self.area).read(key, "adopt-op1")["files"],
                         LedgerStore(self.area).read(source, operation)["files"])
        self.assertEqual((tree(OriginalStore(self.area, source).folder), tree(self.area / "ledger" / source)), before)
        self.assertEqual(retired_marker(association_folder(self.area, "main", source))["new_mission_key"], key)
        load = self.load()
        self.assertEqual((load["read_only"], load["revision"], load["plan"]["association"]["mission_key"]),
                         (False, 2, key))
        self.assertEqual(load["plan"]["records"], {"originals": [], "baseline": None, "applied": None})

    def test_an_existing_active_folder_goes_to_its_history_name(self) -> None:
        """D-2: the existing folder of the active key is kept under its dated name; the fresh folder is active."""
        self.applied()
        self.move_root(keep_old=True)
        key = self.key()
        folder = association_folder(self.area, "main", key)
        folder.mkdir(parents=True)
        (folder / "notes.txt").write_text("kept", encoding="utf-8")
        result = self.adopt()
        name = history_name(key, MOMENT)
        self.assertEqual(result["history_name"], name)
        self.assertEqual((folder.parent / name / "notes.txt").read_text(encoding="utf-8"), "kept")
        self.assertEqual(sorted(path.name for path in folder.glob("*.json")), ["baseline.json", "plan.json"])
        self.assertFalse(any(path.name.endswith(".staged-adopt-op1") for path in folder.parent.iterdir()))


class CaptureCopyTests(CommitFixture):
    """A copy after a capture refusal: the folder of this profile is completed and its plan stays (D-2)."""

    def test_copy_after_a_capture_refusal(self) -> None:
        """The copy key gets an original, a baseline and the repeated output; nothing is retired."""
        self.applied()
        source = self.key()
        copy = self.copy_mission()
        key = self.key(copy.name)
        plan = self.load("third")
        self.assertEqual(self.adopt("third")["case"], "copy")
        self.assertEqual(OriginalStore(self.area, key).read()["provenance"]["source_key"], source)
        self.assertIsNotNone(AssociationRecordStore(self.area, "third", key, "baseline").read())
        self.assertIsNotNone(LedgerStore(self.area).read(key, "adopt-op1"))
        after = self.load("third")
        self.assertEqual((after["revision"], after["plan"]), (plan["revision"], plan["plan"]))
        self.assertIsNone(retired_marker(association_folder(self.area, "main", source)))
        # The capture check now passes for the copy: no refusal and no adoption offer
        preview = self.preview.preview("third")
        self.assertEqual((preview["refusals"], preview["capture"]["adoption"]), ([], None))


class RecordsOnlyTests(CommitFixture):
    """A path change before the first Apply: only the plan and the retirement (Architect 09:30:36, 10.13)."""

    def test_records_only_then_first_apply_captures(self) -> None:
        """No original, baseline or ledger entry is written; the first Apply at the new key captures the original."""
        self.save([zone("rich")])
        source = self.key()
        self.move_root()
        key = self.key()
        self.adopt()
        self.assertIsNone(OriginalStore(self.area, key).read())
        self.assertFalse((self.area / "ledger" / key).exists())
        self.assertEqual(sorted(path.name for path in association_folder(self.area, "main", key).iterdir()),
                         ["plan.json"])
        self.assertIsNotNone(retired_marker(association_folder(self.area, "main", source)))
        self.assertEqual(self.load()["revision"], 1)
        review = self.review()
        self.assertEqual(review.answer["capture"]["result"], "passed")
        self.apply(review)
        self.assertEqual(OriginalStore(self.area, key).read()["target_key"], key)


class RefusalTests(CommitFixture):
    """Refusals and a stale review write nothing."""

    def test_any_other_difference_refuses_also_a_move(self) -> None:
        """A changed file that equals neither the source original nor its output refuses with ADOPT-25."""
        self.applied()
        old = self.move_root()
        self.write("mapgrouppos.xml", b"<changed/>")
        before = tree(self.area)
        with self.assertRaises(ApplyRefused) as caught:
            self.adopt()
        self.assertEqual((caught.exception.reason, caught.exception.path), ("adoption_refused", "mapgrouppos.xml"))
        self.assertEqual(str(caught.exception), texts.FILES_DIFFER_TEXT["move"].format(path="mapgrouppos.xml",
                                                                                       old_path=str(old)))
        self.assertEqual(tree(self.area), before)

    def test_a_stale_review_refuses_with_adopt_28(self) -> None:
        """A change after the review gives the ADOPT-28 text; nothing is written, also under the guard."""
        self.applied()
        self.move_root(keep_old=True)
        fingerprint = self.reader.review("main").fingerprint
        self.write("mapgrouppos.xml", self.mission.joinpath("mapgrouppos.xml").read_bytes() + b" ")
        before = tree(self.area)
        with self.assertRaises(PreviewStale) as caught:
            self.adopt(fingerprint=fingerprint)
        self.assertEqual(str(caught.exception), texts.ADOPTION_STALE_TEXT)
        self.assertEqual(tree(self.area), before)
        # A change between the review in the lane and the guard is found by the revalidation under the mutex
        self.write("mapgrouppos.xml", self.mission.joinpath("mapgrouppos.xml").read_bytes()[:-1])
        reviewed = self.reader.review("main").fingerprint
        self.faults = lambda _phase, _index: None
        original = self.reader.revalidate
        self.reader.revalidate = lambda *_args: (_ for _ in ()).throw(PreviewStale(texts.ADOPTION_STALE_TEXT))
        with self.assertRaises(PreviewStale):
            self.adopt(fingerprint=reviewed)
        self.reader.revalidate = original
        self.assertEqual(tree(self.area), before)
        self.assertEqual(self.events, ["mutex", "mutex released"])


if __name__ == "__main__":
    unittest.main()
