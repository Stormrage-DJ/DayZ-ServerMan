"""Leaf 10.35 follow-up: a kept original of the new key, the files-after check, one commit step, the kind text.

Rulings: Architect 2026-10-17 11:42:47 and Product Owner 11:41:56 and 11:43:27 in evidence 10.35.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from dayz_serverman.application.activity_wording import KIND_ERROR_TEXTS
from dayz_serverman.composition import build_composition
from dayz_serverman.domain import mission_map_adoption_texts as texts
from dayz_serverman.repositories.mission_map_journal import MapGroup, MapJournal
from dayz_serverman.repositories.mission_map_layout import MISSION_FILE_SET, association_folder
from dayz_serverman.repositories.mission_map_ledger import LedgerStore
from dayz_serverman.repositories.mission_map_publication import MissionMapPublicationError
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore, OriginalStore
from tests import test_mission_map_adoption_lane as lane
from tests.mission_map_adoption_commit_support import CommitFixture
from tests.mission_map_preview_support import sha

ROOT = Path(__file__).resolve().parents[1]
# The kind text of a failure after the commit point (Product Owner 2026-10-17 11:41:56 in evidence 10.35)
FAILED_TEXT = ("Adoption reached its commit point, but a later step failed. No mission file changed. The map editor "
               "records can have changed. Preview the adoption again to see what is left to adopt.")


class KeptOriginalTests(CommitFixture):
    """A path change into a key that already has its own original keeps that original (A4)."""

    def own_original(self, source: str, key: str, files: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Write an original of the new key: the files of the source original, or the given ones."""
        found = OriginalStore(self.area, source).read()
        record = {name: found[name] for name in ("target_class", "mission_root", "terrain_id", "captured_at",
                                                   "profile_id", "operation_id")}
        record.update(target_key=key, display_path=str(self.mission), files=files or found["files"])
        contents = {} if files else OriginalStore(self.area, source).stored_contents(found)
        return OriginalStore(self.area, key).create(record, contents)

    def test_same_files_go_on_as_a_move(self) -> None:
        """The same files: the original stays, nothing is copied, and the last apply moves as usual."""
        operation = self.applied()
        source = self.key()
        old = self.move_root()
        key = self.key()
        own = self.own_original(source, key)
        answer = self.reader.review("main").answer
        paths = {"old_path": str(old), "new_path": str(self.mission.resolve())}
        self.assertEqual((answer["case"], answer["kept_original"]), ("move", "same"))
        self.assertEqual(answer["records"][0], texts.ROW_KEPT["same"].format(**paths))
        self.assertTrue(answer["records"][0].endswith(f"same files as the saved original of {old}."))
        self.adopt()
        self.assertEqual(OriginalStore(self.area, key).read(), own)
        baseline = AssociationRecordStore(self.area, "main", key, "baseline").read()[0]
        self.assertEqual(baseline["originals"], [{"target_key": key, "manifest_sha256": own["manifest_sha256"]}])
        self.assertEqual(AssociationRecordStore(self.area, "main", key, "applied").read()[0]["operation_id"],
                         operation)

    def test_different_files_keep_the_original_and_drop_a_mismatched_record(self) -> None:
        """Different files: the original stays, the baseline comes from it, and a mismatched record is dropped."""
        operation = self.applied()
        source = self.key()
        # One written file is back at its original bytes, so the files after of the last apply do not match
        path = LedgerStore(self.area).read(source, operation)["files"][0]["path"]
        saved = next(item for item in OriginalStore(self.area, source).read()["files"] if item["path"] == path)
        self.write(path, OriginalStore(self.area, source).contents.read(saved["sha256"]))
        old = self.move_root()
        key = self.key()
        absent = [{"path": name, "existed": False, "sha256": None, "size": None} for name in MISSION_FILE_SET]
        own = self.own_original(source, key, absent)
        answer = self.reader.review("main").answer
        paths = {"old_path": str(old), "new_path": str(self.mission.resolve())}
        self.assertEqual((answer["case"], answer["refused"], answer["kept_original"]), ("move", False, "different"))
        self.assertEqual(answer["records"][0], texts.ROW_KEPT["different"].format(**paths))
        self.assertIn("so the working baseline is built again from it", answer["records"][0])
        self.adopt()
        self.assertEqual(OriginalStore(self.area, key).read(), own)
        baseline = AssociationRecordStore(self.area, "main", key, "baseline").read()[0]
        self.assertEqual(baseline["originals"], [{"target_key": key, "manifest_sha256": own["manifest_sha256"]}])
        self.assertEqual({item["existed"] for item in baseline["files"]}, {False})
        self.assertIsNone(AssociationRecordStore(self.area, "main", key, "applied").read())


class RecordsOnlySeamTests(CommitFixture):
    """The public records-only commit refuses a journal with a mission file before the caller's preflight."""

    def test_a_dayz_side_group_is_refused(self) -> None:
        """A group means a mission file; the records-only commit never writes one."""
        group = MapGroup("mission", "areaflags.map", "t", "s", None, False, None, True, "0" * 64)
        journal = MapJournal("adopt-group", "adopt", "main", "mpmissions", self.key(), None, None,
                             str(self.root), None, None, None, None, 1, 1, [group], [])
        calls: list[str] = []
        with self.assertRaises(MissionMapPublicationError) as caught:
            self.base_commit.commit_records(journal, [], revalidate=lambda: None,
                                            preflight=lambda *_args: calls.append("preflight"))
        self.assertEqual((caught.exception.code, calls), ("PATH_INVALID", []))


class RetiredFilesAfterTests(lane.RetiredCaseTests):
    """The chain-end last apply moves only when its files after equal the current files (D5)."""

    test_each_step_and_the_original_outcomes = None  # type: ignore[assignment]
    test_shared_key_keeps_the_current_original_and_a_restored_copy = None  # type: ignore[assignment]
    test_unshared_key_supersedes_the_current_original = None  # type: ignore[assignment]

    def chain_end_applied(self) -> None:
        """Write a last apply at the chain end whose files after name the current mapgrouppos.xml."""
        current = (self.mission / "mapgrouppos.xml").read_bytes()
        AssociationRecordStore(self.area, "main", self.later, "applied").save({
            "profile_id": "main", "mission_key": self.later, "mission_root": "mpmissions\\dayzOffline.chernarusplus",
            "operation_id": "op0007", "operation_kind": "apply", "applied_at": "2026-10-16T10:00:00Z",
            "plan_id": "plan01", "plan_revision": 1, "configuration_fingerprint": "c" * 64, "baseline_revision": 0,
            "baseline_manifest_sha256": "d" * 64,
            "files_after": [{"target_key": self.later, "path": "mapgrouppos.xml", "existed": True,
                             "sha256": sha(current), "owned_sha256": None}],
            "files_before": [{"target_key": self.later, "path": "mapgrouppos.xml", "existed": True,
                              "sha256": sha(b"old"), "size": 3}],
            "excluded_encounters": []}, {sha(b"old"): b"old"}, None)
        self.helper(self.later, "D:\\DayZMoved\\mpmissions\\dayzOffline.chernarusplus")

    def test_matching_files_carry_the_record(self) -> None:
        """Equal files after: the fresh folder gets the last apply of the chain end."""
        self.chain_end_applied()
        self.adopt()
        found = AssociationRecordStore(self.area, "main", self.earlier, "applied").read()
        self.assertEqual(found[0]["operation_id"], "op0007")

    def test_a_file_changed_at_the_other_path_drops_the_record(self) -> None:
        """A changed file: no last apply in the fresh folder, and the next Apply preview reviews the file."""
        self.chain_end_applied()
        self.write("mapgrouppos.xml", b"<changed-meanwhile/>")
        self.adopt()
        self.assertIsNone(AssociationRecordStore(self.area, "main", self.earlier, "applied").read())
        self.assertTrue((association_folder(self.area, "main", self.earlier) / "baseline.json").is_file())
        preview = self.preview.preview("main")
        self.assertIn("mapgrouppos.xml", [change["path"] for change in preview["external_changes"]])


class SharedCommitStepTests(unittest.TestCase):
    """One commit object and one lock set (point 5; Architect 2026-10-17 13:41:03 in evidence 10.31, O2)."""

    def test_composition_shares_the_commit_step_and_the_locks(self) -> None:
        """Apply, Restore original, Restore previous state, the zone toggle and adoption share one commit step."""
        with tempfile.TemporaryDirectory(prefix="serverman_adopt_composition_") as name:
            composition = build_composition(Path(name) / "Manager")
            self.addCleanup(composition.operations.shutdown, 2)
            handlers = composition.bridge._handlers
            apply_commit = handlers["apply_mission_map"].__self__._service._commit
            adoption = handlers["adopt_mission_map"].__self__.adoption
            # One restore handler serves both modes, Restore original and Restore previous state, over one service
            restore = handlers["restore_mission_map"].__self__._service
            toggle = handlers["apply_medical_feature"].__self__._service._editor
            commits = [apply_commit, adoption._commit.base, restore._commit, toggle._commit]
            self.assertEqual({id(commit) for commit in commits}, {id(apply_commit)})
            saves = handlers["save_mission_map_plan"].__self__._plans._locks
            locks = [apply_commit.locks, adoption.reader._plans._locks, saves]
            self.assertEqual({id(item) for item in locks}, {id(apply_commit.locks)})


class KindTextTests(unittest.TestCase):
    """PUBLICATION_FAILED under ADOPT_MISSION_MAP has the Product Owner text in both catalogues."""

    def test_both_catalogues(self) -> None:
        """The backend catalogue and operation_messages.js hold the same text."""
        self.assertEqual(KIND_ERROR_TEXTS["ADOPT_MISSION_MAP"]["PUBLICATION_FAILED"], FAILED_TEXT)
        script = (ROOT / "runnable" / "src" / "frontend" / "operation_messages.js").read_text(encoding="utf-8")
        block = script.split("  ADOPT_MISSION_MAP: Object.freeze({", 1)[1].split("}),", 1)[0]
        self.assertIn(f'PUBLICATION_FAILED: "{FAILED_TEXT}"', block)


if __name__ == "__main__":
    unittest.main()
