"""Leaf 10.35: the restore and retired cases, a crash at each point with recovery, and the lane kind (AR-3, AR-4)."""

from __future__ import annotations

import shutil
import unittest
from types import SimpleNamespace
from typing import Any

from dayz_serverman.application.mission_map_adoption_coordinator import MissionMapAdoptionCoordinator
from dayz_serverman.application.mission_map_apply_choices import ApplyRefused
from dayz_serverman.application.mission_map_recovery import EDITOR_KIND
from dayz_serverman.application.operations.models import OperationFailure
from dayz_serverman.repositories.mission_map_layout import association_folder, has_invalidated, invalidated_name
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore
from tests.mission_map_adoption_commit_support import CommitFixture
from tests.mission_map_adoption_support import MOMENT
from tests.test_mission_map_preview import tree


def full_tree(root: Any) -> dict[str, bytes | None]:
    """Return every folder (None) and file (bytes) below a folder, by its relative path."""
    return {str(path.relative_to(root)): None if path.is_dir() else path.read_bytes()
            for path in sorted(root.rglob("*"))}


class Crash(Exception):
    """A simulated process stop at one fault point."""


class RestoreCaseTests(CommitFixture):
    """The restore case writes no original and closes the invalidated record (AR-6, Architect 00:21:00, 10.34)."""

    def test_restore_case_then_the_apply_preview_reviews_the_restored_files(self) -> None:
        """The record gets its closed name with its content; Apply then reviews the restored file (R17)."""
        self.applied()
        # A second Apply, so the "files before" of the record hold content that no other record names
        from tests.mission_map_mapgroup_support import zone
        self.save([zone("rich")], 50, expected=0)
        self.apply(self.review())
        key = self.key()
        folder = association_folder(self.area, "main", key)
        applied = AssociationRecordStore(self.area, "main", key, "applied").read()[0]
        baseline = AssociationRecordStore(self.area, "main", key, "baseline").read()[0]
        only_closed = {item["sha256"] for item in applied["files_before"]} - {item["sha256"] for item in
                                                                              baseline["files"]}
        self.assertTrue(only_closed)
        # A restore wrote an older file and invalidated the last-applied record (10.10)
        (folder / "applied.json").rename(folder / invalidated_name(MOMENT, 1))
        self.write("mapgrouppos.xml", b"<restored-older/>")
        self.assertEqual(self.load()["adoption"], {"case": "restore"})
        originals = tree(self.area / "originals")
        self.assertEqual(self.adopt()["case"], "restore")
        closed = folder / invalidated_name(MOMENT, 1).replace("invalidated", "closed")
        self.assertTrue(closed.is_file())
        self.assertFalse(has_invalidated(folder))
        self.assertEqual(tree(self.area / "originals"), originals)
        # The closed record keeps its content: the commit sweep keeps it
        for digest in only_closed:
            self.assertTrue((folder / "files" / digest).is_file())
        load = self.load()
        self.assertEqual((load["read_only_reason"], load["adoption"]), (None, None))
        preview = self.preview.preview("main")
        self.assertIn("mapgrouppos.xml", [change["path"] for change in preview["external_changes"]])


    def test_a_stop_after_the_closing_rename_rolls_forward(self) -> None:
        """A stop after the rename of step 7 recovers to the closed record; a second roll-forward needs no rename."""
        self.applied()
        folder = association_folder(self.area, "main", self.key())
        (folder / "applied.json").rename(folder / invalidated_name(MOMENT, 1))

        def stop(phase: str, _index: int) -> None:
            if phase == "AFTER_RENAME_FOLDER":
                raise Crash(phase)
        self.faults = stop
        with self.assertRaises(Crash):
            self.adopt()
        self.faults = lambda _phase, _index: None
        self.assertFalse(self.recover()["blocked"])
        closed = invalidated_name(MOMENT, 1).replace("invalidated", "closed")
        self.assertEqual([path.name for path in folder.glob("applied.*")], [closed])
        self.assertEqual(self.journals.records(), ())

    def test_both_names_block_the_roll_forward(self) -> None:
        """An invalidated record beside a file with its closed name is no proof: recovery blocks and keeps both."""
        self.applied()
        folder = association_folder(self.area, "main", self.key())
        (folder / "applied.json").rename(folder / invalidated_name(MOMENT, 1))
        closed = folder / invalidated_name(MOMENT, 1).replace("invalidated", "closed")

        def stop(phase: str, _index: int) -> None:
            if phase == "AFTER_COMMITTING":
                closed.write_text("{}", encoding="utf-8")
                raise Crash(phase)
        self.faults = stop
        with self.assertRaises(Crash):
            self.adopt()
        self.faults = lambda _phase, _index: None
        self.assertTrue(self.recover()["blocked"])
        self.assertTrue((folder / invalidated_name(MOMENT, 1)).is_file() and closed.is_file())


class RetiredCaseTests(CommitFixture):
    """The retired case: the history move, the fresh records of the chain end and the D5 step 4 (RQ2)."""

    def setUp(self) -> None:
        """A return to an earlier path: the active key is retired toward the chain end, which has a plan."""
        super().setUp()
        from tests.test_mission_map_adoption_retired import RetiredOriginalTests
        self.helper = RetiredOriginalTests.original.__get__(self)
        self.save_plan_and_return()

    def save_plan_and_return(self) -> None:
        """Build the chain: earlier key retired toward the later key, then the DayZ root moved back."""
        from tests.mission_map_mapgroup_support import zone
        self.save([zone("rich")])
        self.earlier = self.key()
        self.move_root()
        self.later = self.key()
        self.retire("main", self.earlier, self.later)
        self.save([zone("rich")])
        self.save([zone("rich")], expected=0)
        self.move_root(name="DayZServer")

    def test_each_step_and_the_original_outcomes(self) -> None:
        """Installed: the chain-end original is copied; the plan is the next revision; the chain end is retired."""
        self.helper(self.later, "D:\\DayZMoved\\mpmissions\\dayzOffline.chernarusplus")
        self.assertEqual(self.reader.review("main").context.original_step, "installed")
        result = self.adopt()
        self.assertEqual((result["case"], result["history_name"]),
                         ("retired", f"{self.earlier}.history-20261017T120000Z"))
        history = self.area / "profiles" / "main" / result["history_name"]
        self.assertTrue((history / "retired.json").is_file())
        load = self.load()
        self.assertEqual((load["read_only"], load["read_only_reason"], load["revision"]), (False, None, 2))
        from dayz_serverman.repositories.mission_map_records import OriginalStore
        self.assertEqual(OriginalStore(self.area, self.earlier).read()["provenance"]["source_key"], self.later)

    def test_shared_key_keeps_the_current_original_and_a_restored_copy(self) -> None:
        """Shared: the current original stays active; the chain-end original is an inactive copy in the folder."""
        current = self.helper(self.earlier, str(self.mission))
        self.helper(self.later, "D:\\DayZMoved\\mpmissions\\dayzOffline.chernarusplus", "2026-10-15T19:00:00Z")
        self.assertEqual(self.reader.review("main").context.original_step, "shared")
        self.adopt()
        from dayz_serverman.repositories.mission_map_records import OriginalStore, kept_originals
        self.assertEqual(OriginalStore(self.area, self.earlier).read(), current)
        kept = kept_originals(association_folder(self.area, "main", self.earlier))
        self.assertEqual([item["captured_at"] for item in kept], ["2026-10-15T19:00:00Z"])

    def test_unshared_key_supersedes_the_current_original(self) -> None:
        """Active: the chain-end content becomes the original of the key; the current one is kept superseded."""
        current = self.helper(self.earlier, str(self.mission))
        self.helper(self.later, "D:\\DayZMoved\\mpmissions\\dayzOffline.chernarusplus", "2026-10-15T19:00:00Z")
        del self.profiles.records["second"]
        self.assertEqual(self.reader.review("main").context.original_step, "active")
        self.adopt()
        from dayz_serverman.repositories.mission_map_records import OriginalStore, kept_originals
        self.assertEqual(OriginalStore(self.area, self.earlier).read()["captured_at"], "2026-10-15T19:00:00Z")
        self.assertEqual(kept_originals(OriginalStore(self.area, self.earlier).folder), [current])


class CrashTests(CommitFixture):
    """A crash at each fault point of a move with an existing active folder gives all or nothing."""

    def test_crash_at_each_point_and_recovery(self) -> None:
        """Before COMMITTING the start state returns; at or after it recovery finishes the adoption."""
        self.applied()
        self.move_root(keep_old=True)
        association_folder(self.area, "main", self.key()).mkdir(parents=True)
        start = {name: data for name, data in full_tree(self.area).items()}
        points: list[tuple[str, int]] = []
        self.faults = lambda phase, index: points.append((phase, index))
        fingerprint = self.reader.review("main").fingerprint
        self.adoption.adopt("main", fingerprint, operation_id="adopt-run")
        after = full_tree(self.area)
        sides = {"before": 0, "after": 0}
        for point in points:
            with self.subTest(point=point):
                self.reset(start)

                def fault(phase: str, index: int, point: tuple[str, int] = point) -> None:
                    if (phase, index) == point:
                        raise Crash(phase)
                self.faults = fault
                with self.assertRaises(Crash):
                    self.adoption.adopt("main", fingerprint, operation_id="adopt-run")
                self.faults = lambda _phase, _index: None
                committed = self.base_commit.commit_point("adopt-run") is not None
                self.assertFalse(self.recover()["blocked"])
                self.assertEqual(self.journals.records(), ())
                # A rollback may leave empty key folders of new records; no reader takes an empty folder as a record
                expected = after if committed else start
                self.assertEqual(tree(self.area), {name: data for name, data in expected.items() if data is not None})
                self.assertEqual(self.folders(), sorted(name.split("\\")[-1] for name in expected
                                                        if name.count("\\") == 2 and name.startswith("profiles")))
                sides["after" if committed else "before"] += 1
        self.assertGreaterEqual(min(sides.values()), 2, sides)

    def reset(self, start: dict[str, Any]) -> None:
        """Rebuild the editor area of the start state and remove every journal."""
        shutil.rmtree(self.area)
        shutil.rmtree(self.journals.root)
        self.journals.root.mkdir(parents=True)
        for name, data in start.items():
            path = self.area / name
            if data is None:
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)


class Lane:
    """An operation lane that runs the work at once with a recording context."""

    def __init__(self) -> None:
        """Start without a submitted operation."""
        self.kind: str | None = None
        self.blocks: list[tuple[str, str | None]] = []

    def submit(self, kind: str, work: Any, **_fields: Any) -> Any:
        """Run the work with a context and keep the failure."""
        self.kind = kind
        context = SimpleNamespace(operation_id="adopt-lane", block_for_recovery=lambda reason, owner=None:
                                  self.blocks.append((reason, owner)))
        try:
            work(context)
        except OperationFailure as failure:
            self.failure = failure
        return SimpleNamespace(operation_id="adopt-lane", state=SimpleNamespace(value="QUEUED"))


class LaneTests(unittest.TestCase):
    """The lane kind ADOPT_MISSION_MAP, and a refusal raised at or after COMMITTING (AR-4)."""

    def failure(self, point: str | None) -> tuple[Lane, OperationFailure]:
        """Submit an adoption whose service refuses, with the journal at the given point."""
        def refuse(*_args: Any, **_kwargs: Any) -> None:
            raise ApplyRefused("adoption_refused", "refused")
        service = SimpleNamespace(adopt=refuse, commit_point=lambda _operation: point)
        lane = Lane()
        handle = MissionMapAdoptionCoordinator(service, lane).adopt_mission_map(  # type: ignore[arg-type]
            {"profile_id": "main", "preview_fingerprint": "f"})
        self.assertEqual((lane.kind, handle["operation_id"]), ("ADOPT_MISSION_MAP", "adopt-lane"))
        return lane, lane.failure

    def test_refusal_codes_by_commit_point(self) -> None:
        """Before COMMITTING the refusal stays; while active it needs recovery; after retirement it failed."""
        lane, failure = self.failure(None)
        self.assertEqual((failure.code, lane.blocks), ("INVALID_REQUEST", []))
        lane, failure = self.failure("active")
        self.assertEqual((failure.code, failure.recovery_required), ("RECOVERY_REQUIRED", True))
        self.assertEqual(lane.blocks[0][1], EDITOR_KIND)
        lane, failure = self.failure("retired")
        self.assertEqual((failure.code, lane.blocks), ("PUBLICATION_FAILED", []))

    def test_malformed_request_is_refused_before_the_lane(self) -> None:
        """Extra or missing fields never reach the lane."""
        lane = Lane()
        coordinator = MissionMapAdoptionCoordinator(SimpleNamespace(), lane)  # type: ignore[arg-type]
        with self.assertRaises(Exception):
            coordinator.adopt_mission_map({"profile_id": "main"})
        self.assertIsNone(lane.kind)


if __name__ == "__main__":
    unittest.main()
