"""Cover the adoption record kinds of 10.34: plan, retired marker and history rename, and the chain end (D2, 10.2)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dayz_serverman.application.mission_map_plans import MissionMapPlanService
from dayz_serverman.domain.mission_map_records import sealed
from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.repositories.mission_map_commit_records import CommitRecords, RecordUpdate, parse_record
from dayz_serverman.repositories.mission_map_journal import MapJournal
from dayz_serverman.repositories.mission_map_journal_paths import journal_paths_safe, record_targets
from dayz_serverman.repositories.mission_map_journal_store import parse_journal
from dayz_serverman.repositories.mission_map_layout import (
    PLAN_FILE, association_folder, history_name, profile_folder,
)
from dayz_serverman.repositories.mission_map_plans import MissionMapPlanStore, retired_marker
from dayz_serverman.repositories.mission_map_commit_originals import restored_copy_update
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore, OriginalStore, RecordConflict
from dayz_serverman.repositories.mission_map_retired import (
    history_update, path_change_updates, retired_update,
)
from tests.test_mission_map_journal import JournalFixture
from tests.test_mission_map_plan import stored_plan
from tests.test_mission_map_plan_service import Profiles, profile
from tests.test_mission_map_records import OUTPUT, VANILLA, baseline, original, sha


# Fixed adoption time, operation and the relative mission root of the fixture mission
MOMENT = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
OPERATION = "op-adopt-1"
ROOT = "mpmissions\\dayzOffline.chernarusplus"
# Old association folders of the fixture profile: a matching old path and a moved chain end
OLD, END = "1" * 32, "2" * 32


class AdoptionFixture(JournalFixture):
    """A manager root with a stale active folder, an old folder of the same mission root and a plan service."""

    def setUp(self) -> None:
        """Create the folders; the old plan has revision 2, so the moved plan gets revision 3."""
        super().setUp()
        self.area = self.paths.mission_map
        self.key = self.mission_key()
        self.build()

    def build(self) -> None:
        """Write the records of the path-change state: the active folder exists (D-2) and an old folder matches."""
        self.active = association_folder(self.area, "main", self.key)
        AssociationRecordStore(self.area, "main", self.key, "baseline").save(
            dict(baseline("main", self.key), mission_root=ROOT), {sha(VANILLA): VANILLA}, None)
        self.save_plan(self.key, 1)
        self.save_plan(OLD, 3)

    def save_plan(self, key: str, count: int, root: str = ROOT) -> None:
        """Save a plan of the association count times, so its revision is count - 1."""
        store = MissionMapPlanStore(association_folder(self.area, "main", key), association=("main", key))
        for revision in range(count):
            store.save(self.plan(key, root), None if revision == 0 else revision - 1, MOMENT)

    def plan(self, key: str, root: str = ROOT) -> dict[str, Any]:
        """Return a valid stored plan of the fixture profile for one mission key."""
        association = {"profile_id": "main", "mission_root": root, "mission_key": key, "runtime_profile_key": None}
        return dict(stored_plan(), association=association)

    def new_journal(self) -> MapJournal:
        """Return a PREPARED records-only journal of the active association."""
        return MapJournal(OPERATION, "restore_original", "main", ROOT, self.key, None, None, str(self.dayz),
                          None, None, None, None, 2, 7, [], [])

    def plan_update(self, revision: int = 3) -> RecordUpdate:
        """Return the plan of the active key with the revision after the old read revision (D-1)."""
        target = self.paths.relative(self.active / PLAN_FILE)
        return RecordUpdate("plan", target, {"plan": self.plan(self.key)}, revision=revision)

    def updates(self, journal: MapJournal) -> list[RecordUpdate]:
        """Return the updates of a copy after a path change: history rename, retired old folder, plan."""
        return path_change_updates(self.paths, journal, [OLD], MOMENT) + [self.plan_update()]

    def tree(self) -> dict[str, bytes | None]:
        """Return every folder (None) and file (bytes) of the editor area by its relative path."""
        return {path.relative_to(self.area).as_posix(): None if path.is_dir() else path.read_bytes()
                for path in sorted(self.area.rglob("*"))}

    def service(self) -> MissionMapPlanService:
        """Return a plan service of the fixture profile on the fixture mission."""
        settings = SimpleNamespace(load=lambda: SimpleNamespace(dayz_root=str(self.dayz), revision=1))
        return MissionMapPlanService(Profiles(profile("main", ROOT)), settings, self.area,  # type: ignore[arg-type]
                                     clock=lambda: MOMENT)

    def commit(self, journal: MapJournal, updates: list[RecordUpdate]) -> CommitRecords:
        """Stage the updates and roll them forward as the commit point does."""
        records = CommitRecords(self.paths, self.repository, lambda _phase, _index: None)
        records.stage(journal, updates)
        journal.phase, journal.publication_started = "COMMITTING", True
        self.assertTrue(records.roll_forward(journal))
        records.sweep(journal)
        return records


class RecordKindTests(AdoptionFixture):
    """Each kind commits after the commit point and reverses before it; nothing is deleted."""

    def test_path_change_copy_commits_each_kind(self) -> None:
        """The active folder moves to its history name, the old folder is retired and the plan gets revision 3."""
        before = self.tree()
        self.assertTrue(self.service().load("main")["read_only"])
        journal = self.new_journal()
        records = CommitRecords(self.paths, self.repository, lambda _phase, _index: None)
        records.stage(journal, self.updates(journal))
        history = profile_folder(self.area, "main") / history_name(self.key, MOMENT)
        sibling = self.active.with_name(f"{self.key}.staged-{OPERATION}")
        # Order: the sibling plan, the history move, the sibling move, then the marker
        self.assertEqual([(entry.kind, Path(entry.target_path).name) for entry in journal.records],
                         [("plan", "plan.json"), ("rename", history.name), ("rename", self.key),
                          ("retired", "retired.json")])
        # The journal was durable before the sibling folder existed, and it names every path
        self.assertEqual(self.repository.load(self.repository.path_for(OPERATION)).records, journal.records)
        # Before the commit point nothing moved: the active folder is unchanged, the plan stages beside it
        self.assertFalse(history.exists())
        self.assertEqual({name: data for name, data in self.tree().items() if f".staged-{OPERATION}" not in name},
                         before)
        self.assertEqual(sorted(path.name for path in sibling.iterdir()), [f"plan.json.staged-{OPERATION}"])
        self.assertIsNone(retired_marker(association_folder(self.area, "main", OLD)))
        self.assertTrue(journal_paths_safe(journal, self.dayz, self.paths))
        self.assertEqual(parse_journal(journal.to_dict(), OPERATION).to_dict(), journal.to_dict())
        journal.phase, journal.publication_started = "COMMITTING", True
        self.assertTrue(records.roll_forward(journal))
        self.assertEqual([entry.state for entry in journal.records], ["PUBLISHED"] * 4)
        self.assertFalse(sibling.exists())
        # The history folder keeps every file of the former active folder byte for byte
        after = self.tree()
        moved = {name.replace(self.key, history.name, 1): data for name, data in before.items()
                 if name.startswith(f"profiles/main/{self.key}")}
        self.assertEqual({name: after[name] for name in moved}, moved)
        self.assertEqual(retired_marker(association_folder(self.area, "main", OLD)),
                         {"operation_id": OPERATION, "retired_at": "2026-10-16T12:00:00Z", "new_mission_key": self.key})
        # The plan-service trigger ends: the active plan is editable at revision 3, and a save goes on from it
        load = self.service().load("main")
        self.assertEqual((load["read_only"], load["path_change"], load["revision"], load["state"]),
                         (False, None, 3, "VALID"))
        self.assertEqual(self.service().save("main", load["plan"], 3)["revision"], 4)
        # A roll-forward again is idempotent, and the cleanup after the commit reverses nothing
        self.assertTrue(records.roll_forward(journal))
        journal.phase, journal.resolved, journal.result = "COMMITTED", True, "COMMITTED"
        self.assertTrue(records.rollback(journal))
        self.assertTrue(history.is_dir())

    def test_rollback_reverses_the_rename(self) -> None:
        """Before the commit point, a rollback removes the sibling folder; the active folder never moved."""
        before = self.tree()
        journal = self.new_journal()
        records = CommitRecords(self.paths, self.repository, lambda _phase, _index: None)
        records.stage(journal, self.updates(journal))
        self.assertTrue(records.rollback(journal))
        self.assertEqual(self.tree(), before)
        self.assertTrue(records.rollback(journal))
        self.assertEqual(self.tree(), before)

    def test_reverse_and_roll_forward_need_proof(self) -> None:
        """An unknown file in the sibling folder blocks the reverse step and stays; a taken name blocks a move."""
        journal = self.new_journal()
        records = CommitRecords(self.paths, self.repository, lambda _phase, _index: None)
        records.stage(journal, self.updates(journal))
        history = profile_folder(self.area, "main") / history_name(self.key, MOMENT)
        sibling = self.active.with_name(f"{self.key}.staged-{OPERATION}")
        # Past the commit point a history name that another folder took is no proof, and nothing moves
        history.mkdir()
        journal.phase, journal.publication_started = "COMMITTING", True
        self.assertFalse(records.roll_forward(journal))
        self.assertEqual([entry.state for entry in journal.records], ["PUBLISHED", "READY", "READY", "READY"])
        self.assertTrue(self.active.is_dir() and sibling.is_dir())
        history.rmdir()
        # A lost sibling folder is no proof either: the second move fails after the first one
        renamed = sibling.with_name("moved-away")
        sibling.rename(renamed)
        self.assertFalse(records.roll_forward(journal))
        self.assertTrue(history.is_dir())
        renamed.rename(sibling)
        self.assertTrue(records.roll_forward(journal))
        # Before the commit point an unknown file in the sibling folder blocks the reverse step and stays
        later = MapJournal("op-later", "restore_original", "main", ROOT, self.key, None, None, str(self.dayz),
                           None, None, None, None, 2, 7, [], [])
        moment = datetime(2026, 10, 16, 13, tzinfo=timezone.utc)
        records.stage(later, [history_update(self.paths, later, moment), self.plan_update(revision=4)])
        unknown = self.active.with_name(f"{self.key}.staged-op-later") / "notes.txt"
        unknown.write_text("admin note", encoding="utf-8")
        self.assertFalse(records.rollback(later))
        self.assertTrue(unknown.exists())

    def test_value_kinds_have_their_shapes(self) -> None:
        """A plan holds only a valid plan, a marker its three fields, a rename its two paths; digests are checked."""
        plan = parse_record("plan", {"plan": self.plan(self.key)})
        self.assertEqual(parse_record("plan", plan), plan)
        bad = [("plan", {"plan": self.plan(self.key), "x": 1}), ("plan", {"plan": {"format": "x"}}),
               ("plan", dict(plan, manifest_sha256="0" * 64)), ("retired", {"operation_id": OPERATION}),
               ("retired", {"operation_id": OPERATION, "retired_at": "today", "new_mission_key": self.key}),
               ("retired", {"operation_id": "bad id", "retired_at": "2026-10-16T12:00:00Z", "new_mission_key": OLD}),
               ("rename", {"source": "a"}), ("rename", {"source": "a", "target": 1})]
        for kind, raw in bad:
            with self.subTest(kind=kind, raw=raw), self.assertRaises(ValueError):
                parse_record(kind, raw)


class RefusalTests(AdoptionFixture):
    """Every refusal comes before any write: no journal, no staging file and no moved folder."""

    def refused(self, error: type[Exception], updates: Any) -> None:
        """Assert that staging refuses before any write."""
        before = self.tree()
        journal = self.new_journal()
        with self.assertRaises(error):
            CommitRecords(self.paths, self.repository, lambda _phase, _index: None).stage(journal, updates(journal))
        self.assertEqual(self.tree(), before)
        self.assertFalse(self.repository.path_for(OPERATION).exists())

    def test_refusals(self) -> None:
        """Paths outside the derivation, other operations or keys, missing folders, a lower revision, two renames."""
        def marker(journal: MapJournal, **changes: str) -> RecordUpdate:
            update = retired_update(self.paths, journal, OLD, MOMENT)
            return RecordUpdate("retired", update.target_path, dict(update.record, **changes))
        other = association_folder(self.area, "second", OLD)
        other.mkdir(parents=True)
        cases = [
            lambda journal: [retired_update(self.paths, journal, self.key, MOMENT)],
            lambda journal: [RecordUpdate("retired", self.paths.relative(other / "retired.json"),
                                          retired_update(self.paths, journal, OLD, MOMENT).record)],
            lambda journal: [retired_update(self.paths, journal, END, MOMENT)],
            lambda journal: [marker(journal, operation_id="op-other")],
            lambda journal: [marker(journal, new_mission_key=END)],
            lambda journal: [history_update(self.paths, journal, MOMENT)] * 2,
            lambda journal: [history_update(self.paths, journal, MOMENT),
                             history_update(self.paths, journal, datetime(2026, 10, 16, 13, tzinfo=timezone.utc))],
            lambda journal: [RecordUpdate("plan", self.plan_update().target_path, {"plan": self.plan(OLD)})],
            lambda journal: [RecordUpdate("plan", self.plan_update().target_path, {"plan": self.plan(self.key)}, 0,
                                          revision=0)],
            lambda journal: [RecordUpdate("retired", marker(journal).target_path, marker(journal).record, revision=1)],
        ]
        for index, updates in enumerate(cases):
            with self.subTest(case=index):
                self.refused(ValueError, updates)
        # Without the rename, the stale active plan is found, so the moved plan names a revision it never read
        self.refused(RevisionConflict, lambda journal: [self.plan_update()])
        (profile_folder(self.area, "main") / history_name(self.key, MOMENT)).mkdir()
        self.refused(ValueError, lambda journal: [history_update(self.paths, journal, MOMENT)])

    def test_fresh_folder_records_stage_in_the_sibling_folder(self) -> None:
        """An update that names the sibling folder refuses; a restored copy of the active folder stages there (10.35)."""
        OriginalStore(self.area, self.key).create(dict(original(self.key), mission_root=ROOT), {sha(VANILLA): VANILLA})
        incoming = sealed(original(self.key, OUTPUT))
        sibling = f"{self.paths.relative(self.active)}.staged-{OPERATION}"
        self.refused(ValueError, lambda journal: [history_update(self.paths, journal, MOMENT), RecordUpdate(
            "plan", f"{sibling}/plan.json", {"plan": self.plan(self.key)})])
        journal = self.new_journal()
        CommitRecords(self.paths, self.repository, lambda _phase, _index: None).stage(journal, [
            history_update(self.paths, journal, MOMENT), restored_copy_update(self.paths, journal, incoming,
                                                                              {sha(OUTPUT): OUTPUT})])
        self.assertEqual(journal.records[0].target_path, f"{sibling}/restored-mission-{OPERATION}.json")

    def test_apply_targets_never_hold_the_new_kinds(self) -> None:
        """The Apply preflight uses record_targets only, so it refuses a plan, a marker and a rename."""
        allowed = record_targets(self.new_journal(), self.paths)
        self.assertNotIn(self.plan_update().target_path, allowed)
        self.assertNotIn("rename", allowed.values())
        self.assertNotIn("retired", allowed.values())

    def test_retired_marker_is_created_once(self) -> None:
        """An identical marker stages nothing; a different one is refused."""
        first = self.new_journal()
        self.commit(first, [retired_update(self.paths, first, OLD, MOMENT)])
        again = self.new_journal()
        CommitRecords(self.paths, self.repository, lambda _phase, _index: None).stage(
            again, [retired_update(self.paths, again, OLD, MOMENT)])
        self.assertEqual(again.records, [])
        later = self.new_journal()
        with self.assertRaises(RecordConflict):
            CommitRecords(self.paths, self.repository, lambda _phase, _index: None).stage(
                later, [retired_update(self.paths, later, OLD, datetime(2026, 10, 17, tzinfo=timezone.utc))])


if __name__ == "__main__":
    unittest.main()
