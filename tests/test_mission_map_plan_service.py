"""Cover the plan service: associations, profile isolation, invalid saved data and the mission-path-change state."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dayz_serverman.application.mission_map_plans import (
    AssociationMismatch, MissionMapPlanService, MissionPathChanged,
)
from dayz_serverman.domain.mission_map_values import PlanValidationError
from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord
from dayz_serverman.repositories.json_store import StagingPolicy, VersionedJsonRepository
from dayz_serverman.repositories.mission_map_layout import TargetClass, association_folder, target_key
from dayz_serverman.repositories.mission_map_plans import MissionMapPlanStore, PlanRecordInvalid
from tests.test_mission_map_plan import circle, stored_plan


# Fixed UTC time of plan saves under test
MOMENT = datetime(2026, 10, 10, 13, 0, 0, tzinfo=timezone.utc)
# The mission root of the test profiles, relative to the DayZ root
MISSION = "mpmissions\\dayzOffline.test"


class Profiles:
    """Return profile records by identifier, as ProfileService.read does."""

    def __init__(self, *records: ProfileRecord) -> None:
        """Index the records by profile ID."""
        self.records = {record.values.profile_id: record for record in records}

    def read(self, profile_id: object) -> ProfileRecord:
        """Return the record of one profile."""
        return self.records[profile_id]


def profile(profile_id: str, mission_root: str = MISSION, runtime_profile: str | None = None) -> ProfileRecord:
    """Build a profile record on the given mission."""
    values = ProfileInput(profile_id, profile_id.title(), "DayZServer_x64.exe", "serverDZ.cfg", mission_root, 2302,
                          (), (), runtime_profile)
    return ProfileRecord(1, values)


class ServiceFixture(unittest.TestCase):
    """Temporary DayZ root, manager area and plan service shared by the service tests."""

    def setUp(self) -> None:
        """Create a DayZ root with two missions and an empty manager area in temporary folders."""
        self.temp = tempfile.TemporaryDirectory(prefix="serverman_plan_service_")
        base = Path(self.temp.name)
        self.root = base / "DayZServer"
        for name in ("dayzOffline.test", "dayzOffline.other"):
            (self.root / "mpmissions" / name).mkdir(parents=True)
        (self.root / "profile_main").mkdir()
        self.area = base / "manager" / "data" / "mission-map"
        self.profiles = Profiles(profile("main", runtime_profile="profile_main"), profile("second"),
                                 profile("other", "mpmissions\\dayzOffline.other"))
        self.service = self.make_service()

    def tearDown(self) -> None:
        """Remove the temporary folders."""
        self.temp.cleanup()

    def make_service(self, staging: StagingPolicy = StagingPolicy.OWNER) -> MissionMapPlanService:
        """Build a service over the temporary DayZ root and manager area."""
        settings = SimpleNamespace(load=lambda: SimpleNamespace(dayz_root=str(self.root), revision=1))
        return MissionMapPlanService(self.profiles, settings, self.area, staging=staging,  # type: ignore[arg-type]
                                     clock=lambda: MOMENT)

    def plan_for(self, association: dict[str, Any], objects: list | None = None) -> dict[str, Any]:
        """Return a valid stored plan of the association."""
        return dict(stored_plan(objects), association=dict(association))

    def old_folder(self, label: str, mission_root: str = MISSION) -> Path:
        """Create an association folder of profile main from an earlier DayZ root, with a saved plan."""
        old_mission = Path(self.temp.name) / label / "mpmissions" / "dayzOffline.test"
        key = target_key(TargetClass.MISSION, old_mission)
        association = {"profile_id": "main", "mission_root": mission_root, "mission_key": key,
                       "runtime_profile_key": None}
        folder = association_folder(self.area, "main", key)
        MissionMapPlanStore(folder).save(dict(self.plan_for(association), plan_id=label), None, MOMENT)
        return folder


class PlanServiceTests(ServiceFixture):
    """Verify load and save of the active association and the never-overwrite rule."""

    def test_first_load_creates_nothing_and_save_starts_at_revision_zero(self) -> None:
        """A never-saved plan loads as missing without folders; saves then follow the revision guard."""
        loaded = self.service.load("main")
        self.assertEqual((loaded["state"], loaded["revision"], loaded["read_only"]), ("MISSING", None, False))
        self.assertEqual(loaded["association"]["mission_key"], target_key(
            TargetClass.MISSION, (self.root / "mpmissions" / "dayzOffline.test").resolve()))
        self.assertFalse(self.area.exists())
        plan = self.plan_for(loaded["association"])
        self.assertEqual(self.service.save("main", plan, None)["revision"], 0)
        self.assertEqual(self.service.save("main", dict(plan, global_abundance=75), 0)["revision"], 1)
        self.assertRaises(RevisionConflict, self.service.save, "main", plan, 0)
        self.assertRaises(ValueError, self.service.save, "main", plan, True)
        reloaded = self.service.load("main")
        self.assertEqual((reloaded["state"], reloaded["revision"], reloaded["plan"]["global_abundance"]),
                         ("VALID", 1, 75))

    def test_profiles_on_one_mission_are_isolated(self) -> None:
        """Two profiles of the same mission keep separate plans; one cannot save the other's plan."""
        main = self.service.active_association("main")
        second = self.service.active_association("second")
        self.assertEqual(main["mission_key"], second["mission_key"])
        self.service.save("main", self.plan_for(main, [circle("a")]), None)
        self.service.save("second", self.plan_for(second, [circle("b")]), None)
        self.assertEqual(self.service.load("main")["plan"]["objects"][0]["id"], "a")
        self.assertEqual(self.service.load("second")["plan"]["objects"][0]["id"], "b")
        self.assertRaises(AssociationMismatch, self.service.save, "second", self.plan_for(main), 0)
        # A profile on another mission has its own association
        self.assertEqual(self.service.load("other")["state"], "MISSING")

    def test_association_must_be_the_active_one(self) -> None:
        """Another mission key or root, or an unknown runtime key, is refused; the profile's runtime key is not."""
        active = self.service.active_association("main")
        for change in ({"mission_key": "0" * 32}, {"mission_root": "mpmissions\\dayzOffline.other"},
                       {"runtime_profile_key": "f" * 32}):
            with self.subTest(change=change):
                plan = self.plan_for({**active, **change})
                self.assertRaises(AssociationMismatch, self.service.save, "main", plan, None)
        runtime = target_key(TargetClass.RUNTIME, (self.root / "profile_main").resolve())
        saved = self.service.save("main", self.plan_for({**active, "runtime_profile_key": runtime}), None)
        self.assertEqual(saved["association"]["runtime_profile_key"], runtime)
        # Case and separators of the mission root do not matter; the document itself must be valid
        spelled = {**active, "mission_root": "MPMissions/DayZOffline.TEST"}
        self.assertEqual(self.service.save("main", self.plan_for(spelled), 0)["revision"], 1)
        self.assertRaises(PlanValidationError, self.service.save, "main", self.plan_for(active, [circle(x=-1.0)]), 1)
        second = self.service.active_association("second")
        self.assertRaises(AssociationMismatch, self.service.save, "second",
                          self.plan_for({**second, "runtime_profile_key": runtime}), None)

    def test_invalid_saved_data_is_reported_kept_and_set_aside_explicitly(self) -> None:
        """Load reports invalid data; saves are refused until set aside; then a new plan starts at revision 0."""
        active = self.service.active_association("main")
        folder = association_folder(self.area, "main", active["mission_key"])
        folder.mkdir(parents=True)
        (folder / "plan.json").write_text('{"schema_version": 1, "revision": 3, "plan": {"format": 1}}',
                                          encoding="utf-8")
        loaded = self.service.load("main")
        self.assertEqual((loaded["state"], loaded["plan"]), ("INVALID_PLAN", None))
        self.assertTrue(loaded["detail"])
        for expected in (None, 3):
            self.assertRaises(PlanRecordInvalid, self.service.save, "main", self.plan_for(active), expected)
        self.assertEqual(self.service.set_aside("main")["set_aside"], "plan.invalid-20261010T130000Z.json")
        self.assertEqual(self.service.save("main", self.plan_for(active), None)["revision"], 0)

    def test_observer_reads_only(self) -> None:
        """An observer service loads without creating folders and refuses every write."""
        observer = self.make_service(StagingPolicy.OBSERVER)
        self.assertEqual(observer.load("main")["state"], "MISSING")
        self.assertFalse(self.area.exists())
        plan = self.plan_for(observer.active_association("main"))
        self.assertRaises(PermissionError, observer.save, "main", plan, None)
        self.assertRaises(PermissionError, observer.set_aside, "main")


class MissionPathChangeTests(ServiceFixture):
    """Verify the mission-path-change state: read-only old plan, refused saves, retirement and ordering."""

    def test_path_change_shows_the_old_plan_read_only_and_refuses_saves(self) -> None:
        """No active folder is created, and saves stay refused, also after a repeated autosave attempt."""
        old = self.old_folder("old-root")
        active = self.service.active_association("main")
        loaded = self.service.load("main")
        self.assertTrue(loaded["read_only"])
        self.assertEqual((loaded["plan"]["plan_id"], loaded["path_change"]["shown_mission_key"]),
                         ("old-root", old.name))
        active_folder = association_folder(self.area, "main", active["mission_key"])
        for _attempt in range(2):
            with self.assertRaises(MissionPathChanged) as caught:
                self.service.save("main", self.plan_for(active), None)
            self.assertEqual([folder["mission_key"] for folder in caught.exception.folders], [old.name])
            self.assertFalse(active_folder.exists())
        self.assertRaises(MissionPathChanged, self.service.set_aside, "main")
        # Other profiles and other missions are not affected
        self.assertIsNone(self.service.load("second")["path_change"])
        self.assertIsNone(self.service.load("other")["path_change"])

    def test_existing_active_records_stay_unchanged(self) -> None:
        """When the active folder already exists, its plan is kept and saves are still refused."""
        active = self.service.active_association("main")
        self.service.save("main", self.plan_for(active), None)
        before = (association_folder(self.area, "main", active["mission_key"]) / "plan.json").read_bytes()
        self.old_folder("old-root")
        self.assertTrue(self.service.load("main")["read_only"])
        self.assertRaises(MissionPathChanged, self.service.save, "main", self.plan_for(active), 0)
        self.assertEqual((association_folder(self.area, "main", active["mission_key"]) / "plan.json").read_bytes(),
                         before)

    def test_retired_folder_ends_the_state(self) -> None:
        """A valid retired marker removes the folder from the check; an incomplete one does not."""
        old = self.old_folder("old-root")
        marker = VersionedJsonRepository(old / "retired.json", 1)
        marker.save({"operation_id": "adopt-1"}, None)
        self.assertTrue(self.service.load("main")["read_only"])
        active = self.service.active_association("main")
        marker.save({"operation_id": "adopt-1", "retired_at": "2026-10-10T13:00:00Z",
                     "new_mission_key": active["mission_key"]}, 0)
        loaded = self.service.load("main")
        self.assertEqual((loaded["read_only"], loaded["path_change"], loaded["state"]), (False, None, "MISSING"))
        self.assertEqual(self.service.save("main", self.plan_for(active), None)["revision"], 0)
        self.assertTrue((old / "plan.json").exists())

    def test_several_matches_show_the_newest_plan_then_the_lowest_key(self) -> None:
        """Load shows the folder whose plan changed last; equal times are ordered by mission key."""
        folders = [self.old_folder(label, root) for label, root in
                   (("first", MISSION), ("second", "MPMISSIONS/dayzoffline.TEST"), ("third", MISSION))]
        times = {folders[0]: 1_000, folders[1]: 3_000, folders[2]: 2_000}
        for folder, seconds in times.items():
            os.utime(folder / "plan.json", ns=(seconds * 10**9, seconds * 10**9))
        loaded = self.service.load("main")
        self.assertEqual(loaded["path_change"]["shown_mission_key"], folders[1].name)
        self.assertEqual(loaded["plan"]["plan_id"], "second")
        self.assertEqual([item["mission_key"] for item in loaded["path_change"]["folders"]],
                         [folders[1].name, folders[2].name, folders[0].name])
        # With equal times the lowest mission key is shown
        for folder in folders:
            os.utime(folder / "plan.json", ns=(5 * 10**9, 5 * 10**9))
        lowest = min(folder.name for folder in folders)
        self.assertEqual(self.service.load("main")["path_change"]["shown_mission_key"], lowest)

    def test_other_mission_root_is_a_separate_association(self) -> None:
        """An old folder of a different mission root is not a path change (R20 isolation)."""
        self.old_folder("old-root", "mpmissions\\dayzOffline.enoch")
        loaded = self.service.load("main")
        self.assertEqual((loaded["read_only"], loaded["path_change"], loaded["state"]), (False, None, "MISSING"))


if __name__ == "__main__":
    unittest.main()
