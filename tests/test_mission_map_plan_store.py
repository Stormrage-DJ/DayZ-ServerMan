"""Cover the editor area layout and the plan record store: states, revisions, interrupted saves and set aside."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from dayz_serverman.domain.mission_map_plan import PlanForm, parse_plan
from dayz_serverman.domain.mission_map_values import PlanValidationError
from dayz_serverman.domain.models import RepositoryError, RevisionConflict
from dayz_serverman.repositories import json_store
from dayz_serverman.repositories.json_store import StagingPolicy, VersionedJsonRepository
from dayz_serverman.repositories.mission_map_layout import (
    TargetClass, association_folder, interrupted_name, ledger_folder, ordinal_spelling, original_folder, set_aside_name,
    target_key,
)
from dayz_serverman.repositories.mission_map_plans import (
    MissionMapPlanStore, PlanRecordInvalid, PlanState, retired_marker,
)
from dayz_serverman.repositories.paths import PortablePaths
from tests.test_mission_map_plan import MISSION_KEY, circle, stored_plan


# Fixed UTC time of the store operations under test
MOMENT = datetime(2026, 10, 10, 12, 30, 5, tzinfo=timezone.utc)


class LayoutTests(unittest.TestCase):
    """Verify target keys, folders and dated names of the editor area."""

    def test_editor_area_is_below_the_manager_data_folder(self) -> None:
        """PortablePaths names data/mission-map below the manager root."""
        paths = PortablePaths.from_root(Path(tempfile.gettempdir()) / "manager")
        self.assertEqual(paths.mission_map, paths.data / "mission-map")

    def test_target_key_ignores_ordinal_case_and_is_class_bound(self) -> None:
        """One physical folder has one key whatever its spelling; the two classes never share a key."""
        key = target_key(TargetClass.MISSION, Path("C:/DayZ/MPMissions/dayzOffline.Chernarusplus"))
        self.assertRegex(key, "^[0-9a-f]{32}$")
        other_spelling = Path("c:\\dayz\\mpmissions\\.\\DAYZOFFLINE.chernarusplus\\")
        self.assertEqual(key, target_key(TargetClass.MISSION, other_spelling))
        self.assertNotEqual(key, target_key(TargetClass.RUNTIME, Path("C:/DayZ/MPMissions/dayzOffline.Chernarusplus")))
        self.assertNotEqual(key, target_key(TargetClass.MISSION, Path("C:/DayZ/MPMissions/dayzOffline.enoch")))
        self.assertRaises(ValueError, target_key, TargetClass.MISSION, Path("mpmissions/x"))

    def test_keys_follow_the_windows_ordinal_rule(self) -> None:
        """The committed Windows ordinal table (windows-ordinal-1): no full case folding, no Unicode normalization."""
        self.assertNotEqual(ordinal_spelling("Straße"), ordinal_spelling("strasse"))
        self.assertEqual(ordinal_spelling("STRASSE"), ordinal_spelling("strasse"))
        self.assertEqual(ordinal_spelling("Ä"), ordinal_spelling("ä"))
        self.assertNotEqual(ordinal_spelling("\u00e9"), ordinal_spelling("e\u0301"))
        self.assertEqual(ordinal_spelling("mpmissions\\dayzOffline.test"), "MPMISSIONS/DAYZOFFLINE.TEST")
        # The same rule decides the target keys of two NTFS folders
        street = target_key(TargetClass.MISSION, Path("C:/DayZ/mpmissions/Straße"))
        self.assertNotEqual(street, target_key(TargetClass.MISSION, Path("C:/DayZ/mpmissions/strasse")))
        self.assertEqual(street, target_key(TargetClass.MISSION, Path("C:/DAYZ/MPMISSIONS/STRAßE")))
        self.assertNotEqual(target_key(TargetClass.MISSION, Path("C:/DayZ/mpmissions/caf\u00e9")),
                            target_key(TargetClass.MISSION, Path("C:/DayZ/mpmissions/cafe\u0301")))

    def test_folders_refuse_unsafe_names(self) -> None:
        """Profile IDs and keys must be safe single folder names."""
        area = Path(tempfile.gettempdir()) / "area"
        self.assertEqual(association_folder(area, "main", MISSION_KEY), area / "profiles" / "main" / MISSION_KEY)
        self.assertEqual(original_folder(area, MISSION_KEY), area / "originals" / MISSION_KEY)
        self.assertEqual(ledger_folder(area, MISSION_KEY), area / "ledger" / MISSION_KEY)
        for profile_id, key in (("..", MISSION_KEY), ("Main", MISSION_KEY), ("main", "../x"), ("main", "A" * 32)):
            with self.subTest(profile_id=profile_id, key=key):
                self.assertRaises(ValueError, association_folder, area, profile_id, key)

    def test_dated_names_use_utc(self) -> None:
        """Set-aside and interrupted-save names carry the UTC stamp."""
        local = MOMENT.astimezone(timezone(timedelta(hours=2)))
        self.assertEqual(set_aside_name(local), "plan.invalid-20261010T123005Z.json")
        self.assertEqual(interrupted_name(MOMENT, 2), "plan.interrupted-20261010T123005Z-2.json")
        self.assertRaises(ValueError, set_aside_name, datetime(2026, 1, 1))


class PlanStoreTests(unittest.TestCase):
    """Verify record states, the revision guard and the never-overwrite rule of invalid saved data."""

    def setUp(self) -> None:
        """Create a temporary association folder."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_plan_store_")
        self.folder = Path(self.temp.name) / "profiles" / "main" / MISSION_KEY
        self.store = MissionMapPlanStore(self.folder)
        self.plan = parse_plan(stored_plan(), PlanForm.STORED)

    def tearDown(self) -> None:
        """Remove the temporary folder."""
        self.temp.cleanup()

    def write_record(self, fields: dict, revision: int = 0, schema_version: int = 1) -> None:
        """Write a raw plan record envelope, as an older or broken build might have left it."""
        self.folder.mkdir(parents=True, exist_ok=True)
        document = {"schema_version": schema_version, "revision": revision, **fields}
        (self.folder / "plan.json").write_text(json.dumps(document), encoding="utf-8")

    def test_missing_record_saves_at_revision_zero_and_then_guards_revisions(self) -> None:
        """A first save needs no revision; later saves need the current one; stale proofs are refused."""
        self.assertEqual(self.store.inspect().state, PlanState.MISSING)
        self.assertFalse(self.folder.exists())
        self.assertRaises(RevisionConflict, self.store.save, self.plan, 0, MOMENT)
        self.assertEqual(self.store.save(self.plan, None, MOMENT), (0, ()))
        changed = dict(self.plan, global_abundance=50)
        self.assertEqual(self.store.save(changed, 0, MOMENT)[0], 1)
        # A stale or missing proof writes nothing
        for stale in (0, None, 5, True):
            with self.subTest(stale=stale):
                self.assertRaises(RevisionConflict, self.store.save, self.plan, stale, MOMENT)
        inspection = self.store.inspect()
        self.assertEqual((inspection.state, inspection.revision, inspection.plan), (PlanState.VALID, 1, changed))

    def test_invalid_saved_data_is_never_overwritten(self) -> None:
        """Corrupt, future, newer and D1-invalid records are reported and kept byte for byte."""
        newer = dict(stored_plan(), version=2)
        invalid = stored_plan([circle(x=5000.0)])
        cases = [
            (PlanState.CORRUPT, lambda: (self.folder / "plan.json").write_text("{not json", encoding="utf-8")),
            (PlanState.CORRUPT, lambda: self.write_record({"plan": stored_plan(), "notes": 1})),
            (PlanState.FUTURE_SCHEMA, lambda: self.write_record({"plan": stored_plan()}, schema_version=2)),
            (PlanState.NEWER_PLAN, lambda: self.write_record({"plan": newer}, revision=4)),
            (PlanState.INVALID_PLAN, lambda: self.write_record({"plan": invalid}, revision=4)),
        ]
        for state, write in cases:
            with self.subTest(state=state):
                self.folder.mkdir(parents=True, exist_ok=True)
                write()
                before = (self.folder / "plan.json").read_bytes()
                self.assertEqual(self.store.inspect().state, state)
                for expected in (None, 0, 4):
                    with self.assertRaises(PlanRecordInvalid) as caught:
                        self.store.save(self.plan, expected, MOMENT)
                    self.assertEqual(caught.exception.inspection.state, state)
                self.assertEqual((self.folder / "plan.json").read_bytes(), before)
        # An invalid plan still names its mission root for the path-change check
        self.assertEqual(self.store.inspect().mission_root, "mpmissions\\dayzOffline.test")

    def test_set_aside_renames_invalid_data_and_a_new_plan_starts_at_revision_zero(self) -> None:
        """Only invalid data can be set aside; the dated copy keeps the bytes; the next save is revision 0."""
        self.assertRaises(RepositoryError, self.store.set_aside, MOMENT)
        self.write_record({"plan": dict(stored_plan(), version=3)}, revision=7)
        before = (self.folder / "plan.json").read_bytes()
        target = self.store.set_aside(MOMENT)
        self.assertEqual(target.name, "plan.invalid-20261010T123005Z.json")
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(self.store.inspect().state, PlanState.MISSING)
        self.assertEqual(self.store.save(self.plan, None, MOMENT)[0], 0)
        # A valid record cannot be set aside, and earlier evidence is never replaced
        self.assertRaises(RepositoryError, self.store.set_aside, MOMENT)
        self.write_record({"plan": "broken"})
        self.assertRaises(RepositoryError, self.store.set_aside, MOMENT)
        self.assertEqual(target.read_bytes(), before)

    def test_interrupted_save_keeps_the_record_and_its_evidence(self) -> None:
        """A failed atomic replace leaves the old record; the next save renames the leftover, never deletes it."""
        self.store.save(self.plan, None, MOMENT)
        record = (self.folder / "plan.json").read_bytes()
        changed = dict(self.plan, global_abundance=100)
        # Fault injection: the replace of the staged file fails as if the process stopped there
        with mock.patch.object(json_store, "replace_file", side_effect=OSError("power loss")):
            self.assertRaises(OSError, self.store.save, changed, 0, MOMENT)
        self.assertEqual((self.folder / "plan.json").read_bytes(), record)
        leftovers = sorted(self.folder.glob(".plan.json.*.tmp"))
        self.assertEqual(len(leftovers), 1)
        staged = leftovers[0].read_bytes()
        # The record stays readable; owners see the evidence, observers do not
        inspection = self.store.inspect()
        self.assertEqual((inspection.state, inspection.revision, inspection.leftovers),
                         (PlanState.VALID, 0, (leftovers[0],)))
        self.assertEqual(MissionMapPlanStore(self.folder, staging=StagingPolicy.OBSERVER).inspect().leftovers, ())
        # The next save moves the leftover to a dated evidence name first
        revision, evidence = self.store.save(changed, 0, MOMENT)
        self.assertEqual((revision, [path.name for path in evidence]),
                         (1, ["plan.interrupted-20261010T123005Z-1.json"]))
        self.assertEqual(evidence[0].read_bytes(), staged)
        self.assertEqual(list(self.folder.glob(".plan.json.*.tmp")), [])
        self.assertEqual(self.store.inspect().plan, changed)

    def test_evidence_names_never_collide(self) -> None:
        """Two leftovers in the same second get the next free numbers, also past an existing evidence file."""
        self.store.save(self.plan, None, MOMENT)
        self.folder.joinpath(interrupted_name(MOMENT, 1)).write_text("older evidence", encoding="utf-8")
        for name in (".plan.json.aaaa.tmp", ".plan.json.bbbb.tmp"):
            self.folder.joinpath(name).write_text("partial", encoding="utf-8")
        _revision, evidence = self.store.save(self.plan, 0, MOMENT)
        self.assertEqual([path.name for path in evidence], [interrupted_name(MOMENT, 2), interrupted_name(MOMENT, 3)])
        older = self.folder.joinpath(interrupted_name(MOMENT, 1))
        self.assertEqual(older.read_text(encoding="utf-8"), "older evidence")

    def test_staging_without_a_record_is_a_missing_plan_with_evidence(self) -> None:
        """An interrupted first save leaves no record; the next first save keeps the evidence."""
        self.folder.mkdir(parents=True)
        self.folder.joinpath(".plan.json.cccc.tmp").write_text("{", encoding="utf-8")
        inspection = self.store.inspect()
        self.assertEqual((inspection.state, len(inspection.leftovers)), (PlanState.MISSING, 1))
        self.assertEqual(self.store.save(self.plan, None, MOMENT)[1][0].read_text(encoding="utf-8"), "{")

    def test_oversized_plan_is_refused(self) -> None:
        """A plan whose canonical JSON exceeds 8 MiB is not saved."""
        big = copy.deepcopy(self.plan)
        properties = {f"k{index}": "x" * 256 for index in range(64)}
        bandit = {"id": "", "name": "Encounter", "kind": "encounter", "visible": True, "locked": False,
                  "centre": {"x": 1.0, "z": 1.0}, "radius": None, "provider": "ai_bandits", "properties": properties,
                  "excluded": False,
                  "linked_loot": {"enabled": False, "tier": None, "abundance": "rich", "radius": None}}
        big["objects"] = [dict(bandit, id=f"b{index}") for index in range(600)]
        with self.assertRaises(PlanValidationError) as caught:
            self.store.save(big, None, MOMENT)
        self.assertIn("8 MiB", str(caught.exception))
        self.assertFalse(self.folder.exists())


class RetiredMarkerTests(unittest.TestCase):
    """Verify that only a complete retired marker retires an association folder."""

    def test_marker_shapes(self) -> None:
        """A valid marker is read; a missing, corrupt or incomplete one is not a retirement."""
        with tempfile.TemporaryDirectory(prefix="serverman_retired_") as name:
            folder = Path(name)
            self.assertIsNone(retired_marker(folder))
            marker = VersionedJsonRepository(folder / "retired.json", 1)
            fields = {"operation_id": "adopt-1", "retired_at": "2026-10-10T12:30:00Z", "new_mission_key": MISSION_KEY}
            marker.save({"operation_id": "adopt-1"}, None)
            self.assertIsNone(retired_marker(folder))
            marker.save(dict(fields, retired_at="yesterday"), 0)
            self.assertIsNone(retired_marker(folder))
            marker.save(fields, 1)
            self.assertEqual(retired_marker(folder), fields)


if __name__ == "__main__":
    unittest.main()
