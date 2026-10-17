"""Leaf 10.13: each refusal of the adoption review, a stale fingerprint and the copy of an adopted copy."""

from __future__ import annotations

import shutil
import unittest

from dayz_serverman.application.mission_map_adoption import MissionMapAdoption
from dayz_serverman.application.mission_map_adoption_coordinator import MissionMapAdoptionCoordinator
from dayz_serverman.application.mission_map_preview import PreviewStale
from dayz_serverman.bridge.contracts import ErrorCode
from dayz_serverman.bridge.facade import ApplicationCallError
from dayz_serverman.domain import mission_map_adoption_texts as texts
from dayz_serverman.repositories.mission_map_layout import TargetClass, original_folder, target_key
from dayz_serverman.repositories.mission_map_ledger import LedgerStore
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore, OriginalStore
from tests.mission_map_adoption_support import AdoptionFixture
from tests.mission_map_mapgroup_support import zone
from tests.mission_map_preview_support import APPLIED_AT, WOLVES, sha

# A managed file that the simulated Apply does not change, so it equals the source original
UNTOUCHED = "env/wolf_territories.xml"
# The managed file of the runtime profile class and its content at capture time
BANDITS, BANDITS_DATA = "AI_Bandits/DynamicAIB.json", b'{"groups": []}'


class FilesDifferTests(AdoptionFixture):
    """ADOPT-25: a managed file equals neither the source original nor a source-key output (OP-4)."""

    def test_copy_variant_names_the_file(self) -> None:
        """A changed file of a copy refuses with the copy text; the other files still match."""
        self.applied()
        copy = self.copy_mission()
        (copy / UNTOUCHED).write_bytes(b"<territory-type/>")
        answer = self.answer("third")
        self.assertEqual((answer["case"], answer["refused"]), ("copy", True))
        self.assertEqual([(item["reason"], item["file"]) for item in answer["refusals"]],
                         [("adoption_refused", UNTOUCHED)])
        self.assertEqual(answer["refusals"][0]["message"],
                         texts.FILES_DIFFER_TEXT["copy"].format(path=UNTOUCHED, old_path=str(self.mission)))
        self.assertIn("cannot show that this mission is a copy", answer["refusals"][0]["message"])
        # A refused answer keeps what happened and the records, as the window shows them collapsed
        self.assertIsNotNone(answer["happened"])

    def test_move_variant_and_a_removed_file(self) -> None:
        """A moved mission with a changed and a removed file refuses with the move text, once for each file."""
        self.applied()
        old = self.move_root()
        (self.mission / UNTOUCHED).write_bytes(b"<territory-type/>")
        (self.mission / "mapgrouppos.xml").unlink()
        answer = self.answer()
        self.assertEqual((answer["case"], answer["refused"]), ("move", True))
        self.assertEqual([item["file"] for item in answer["refusals"]], ["mapgrouppos.xml", UNTOUCHED])
        self.assertEqual(answer["refusals"][1]["message"],
                         texts.FILES_DIFFER_TEXT["move"].format(path=UNTOUCHED, old_path=str(old)))
        self.assertIn("cannot show that the files moved with the mission", answer["refusals"][1]["message"])

    def test_an_absent_file_matches_only_an_absent_original_file(self) -> None:
        """An output entry that removed a file has a null after digest; a removed file never matches it (D2)."""
        operation = self.applied()
        source = self.key()
        entry = dict(LedgerStore(self.area).read(source, operation), operation_id="op9999", files=[
            {"path": UNTOUCHED, "before_sha256": sha(WOLVES), "after_sha256": None, "baseline_sha256": sha(WOLVES),
             "owned_before_sha256": self.owned(UNTOUCHED, WOLVES), "owned_after_sha256": None,
             "owned_baseline_sha256": self.owned(UNTOUCHED, WOLVES)}])
        entry.pop("manifest_sha256")
        LedgerStore(self.area).create(entry)
        self.move_root()
        (self.mission / UNTOUCHED).unlink()
        answer = self.answer()
        self.assertEqual([item["file"] for item in answer["refusals"]], [UNTOUCHED])
        self.assertEqual(answer["ledger"], [operation])

    def test_runtime_class_is_compared(self) -> None:
        """The old folder names a runtime key with an original: a changed provider file refuses with its name."""
        self.applied()
        source = self.key()
        self.runtime_original(source)
        self.move_root()
        self.assertFalse(self.answer()["refused"])
        (self.root / "profile_main" / BANDITS).write_bytes(b'{"groups": [1]}')
        answer = self.answer()
        self.assertEqual([item["file"] for item in answer["refusals"]], [BANDITS])
        self.assertIn(f"runtime:{BANDITS}", answer["inputs"]["files"])

    def runtime_original(self, source: str) -> None:
        """Capture the runtime profile folder of profile main and name its key in the baseline of the old folder."""
        folder = self.root / "profile_main"
        (folder / BANDITS).parent.mkdir(parents=True)
        (folder / BANDITS).write_bytes(BANDITS_DATA)
        key = target_key(TargetClass.RUNTIME, folder.resolve())
        original = OriginalStore(self.area, key).create(
            {"target_class": "runtime", "target_key": key, "display_path": str(folder), "mission_root": "profile_main",
             "terrain_id": "chernarusplus", "captured_at": APPLIED_AT, "profile_id": "main", "operation_id": "op0001",
             "files": [self.entry(BANDITS, BANDITS_DATA, None)]}, {sha(BANDITS_DATA): BANDITS_DATA})
        store = AssociationRecordStore(self.area, "main", source, "baseline")
        record, revision = store.read()
        contents = {item["sha256"]: store.contents.read(item["sha256"]) for item in record["files"] if item["existed"]}
        record = dict(record, target_keys=[source, key], originals=[
            *record["originals"], {"target_key": key, "manifest_sha256": original["manifest_sha256"]}])
        record.pop("manifest_sha256")
        store.save(record, contents, revision)


class NoSourceTests(AdoptionFixture):
    """ADOPT-24 and `nothing_to_adopt`."""

    def test_a_last_apply_record_without_an_original(self) -> None:
        """An old folder with a last-applied record but no original, which only damage gives, refuses (ADOPT-24)."""
        self.applied()
        shutil.rmtree(original_folder(self.area, self.key()))
        self.move_root()
        self.assertEqual(self.load()["adoption"], {"case": "copy"})
        answer = self.answer()
        self.assertFalse(answer["records_only"])
        self.assertEqual([(item["reason"], item["message"]) for item in answer["refusals"]],
                         [("adoption_refused", texts.NO_SOURCE_TEXT)])

    def test_output_whose_source_has_no_original(self) -> None:
        """Editor output of a key without an original refuses the capture, offers nothing and refuses adoption."""
        self.applied()
        shutil.rmtree(original_folder(self.area, self.key()))
        self.copy_mission()
        capture = self.preview.preview("third")["capture"]
        self.assertEqual((capture["reason"], capture["adoption_required"], capture["adoption"]),
                         ("editor_output", False, None))
        answer = self.answer("third")
        self.assertEqual((answer["case"], answer["route"], answer["source"]), (None, None, None))
        self.assertEqual([item["message"] for item in answer["refusals"]], [texts.NO_SOURCE_TEXT])

    def test_nothing_to_adopt(self) -> None:
        """A mission whose records belong to its path: no offer, and the review refuses without texts."""
        self.applied()
        self.assertIsNone(self.load()["adoption"])
        answer = self.answer()
        self.assertEqual([(item["reason"], item["message"]) for item in answer["refusals"]],
                         [("nothing_to_adopt", texts.NOTHING_TO_ADOPT_TEXT)])
        self.assertEqual((answer["case"], answer["happened"], answer["records"], answer["success"]),
                         (None, None, [], None))


class StaleTests(AdoptionFixture):
    """ADOPT-28: a changed mission file or record makes the reviewed fingerprint stale."""

    def test_stale_fingerprint(self) -> None:
        """The same state passes; a file change and a record change each raise the ADOPT-28 text."""
        self.applied()
        copy = self.copy_mission()
        fingerprint = self.answer("third")["fingerprint"]
        self.assertEqual(self.reader.revalidate("third", fingerprint).fingerprint, fingerprint)
        (copy / UNTOUCHED).write_bytes(b"<territory-type/>")
        with self.assertRaises(PreviewStale) as raised:
            self.reader.revalidate("third", fingerprint)
        self.assertEqual(str(raised.exception), texts.ADOPTION_STALE_TEXT)
        fingerprint = self.answer("third")["fingerprint"]
        self.save([zone("rich")], identifier="third", expected=0)
        self.assertRaises(PreviewStale, self.reader.revalidate, "third", fingerprint)

    def test_the_bridge_handler_maps_errors(self) -> None:
        """The owner handler returns the answer; an unknown parameter is refused before the review."""
        self.applied()
        handler = MissionMapAdoptionCoordinator(MissionMapAdoption(self.reader))
        self.assertEqual(list(handler.handlers()), ["preview_mission_map_adoption", "adopt_mission_map"])
        answer = handler.preview_mission_map_adoption({"profile_id": "main"})
        self.assertEqual(answer["refusals"][0]["reason"], "nothing_to_adopt")
        with self.assertRaises(ApplicationCallError) as raised:
            handler.preview_mission_map_adoption({"profile_id": "main", "extra": 1})
        self.assertEqual(raised.exception.code, ErrorCode.INVALID_REQUEST)


class CopyOfCopyTests(AdoptionFixture):
    """The repeated output entries of an adopted copy let a copy of that copy be recognized (D2, OP-5)."""

    def test_copy_of_an_adopted_copy(self) -> None:
        """The first source lost its original; the adopted copy key offers adoption and passes the comparison."""
        operation = self.applied()
        first = self.key()
        copy = self.copy_mission()
        copy_key = target_key(TargetClass.MISSION, copy.resolve())
        self.adopted_copy(first, copy_key, copy, operation)
        # The first original goes, as after the deletion of its folder; its ledger entry stays (D2)
        shutil.rmtree(original_folder(self.area, first))
        second = self.copy_mission("fourth", source=copy, name="copy2.chernarusplus")
        capture = self.preview.preview("fourth")["capture"]
        self.assertEqual((capture["adoption"], capture["match"]["target_key"]), ({"case": "copy"}, copy_key))
        answer = self.answer("fourth")
        self.assertEqual((answer["case"], answer["route"], answer["refused"]), ("copy", "capture", False))
        self.assertEqual((answer["source"]["mission_key"], answer["ledger"]), (copy_key, ["adopt-1"]))
        self.assertIn(f"a copy of the saved original of {copy}", answer["records"][0])
        self.assertTrue(str(second.resolve()) in answer["happened"])


if __name__ == "__main__":
    unittest.main()
