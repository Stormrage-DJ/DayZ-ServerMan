"""Mission change and profile rename: a new mission starts its own association, a rename keeps the records (R20).

The plan service runs over a DayZ root and an editor area in temporary folders, as in the plan service tests.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from dayz_serverman.domain.profiles import ProfileRecord
from dayz_serverman.repositories.mission_map_layout import (
    DELETED_FOLDER, TargetClass, association_folder, association_profiles, target_key,
)
from dayz_serverman.repositories.mission_map_plans import MissionMapPlanStore
from tests.test_mission_map_plan_service import MISSION, MOMENT, ServiceFixture, profile
from tests.test_mission_map_plan import circle

# The second mission of the fixture root, relative to the DayZ root
OTHER_MISSION = "mpmissions\\dayzOffline.other"


class MissionChangeTests(ServiceFixture):
    """A changed mission never inherits the plan, baseline or last-applied record of another mission."""

    def saved_main(self) -> tuple[dict, bytes]:
        """Save a plan of profile main on its first mission and give the folder applied records."""
        association = self.service.active_association("main")
        self.service.save("main", self.plan_for(association, [circle("old-zone")]), None)
        folder = association_folder(self.area, "main", association["mission_key"])
        (folder / "baseline.json").write_text('{"baseline": 1}', encoding="utf-8")
        (folder / "applied.json").write_text('{"applied": 1}', encoding="utf-8")
        return association, b"".join(path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file())

    def test_new_mission_starts_a_separate_association_without_a_baseline(self) -> None:
        """The new mission loads empty and saves into its own folder; the old association stays unchanged."""
        old, old_bytes = self.saved_main()
        self.profiles.records["main"] = profile("main", OTHER_MISSION, runtime_profile="profile_main")
        loaded = self.service.load("main")
        self.assertEqual((loaded["state"], loaded["revision"], loaded["plan"], loaded["path_change"],
                          loaded["read_only"]), ("MISSING", None, None, None, False))
        new_key = loaded["association"]["mission_key"]
        self.assertEqual(new_key, target_key(TargetClass.MISSION, (self.root / OTHER_MISSION).resolve()))
        self.assertNotEqual(new_key, old["mission_key"])
        # The first save of the new mission starts at revision 0 and writes only the plan
        saved = self.service.save("main", self.plan_for(loaded["association"], [circle("new-zone")]), None)
        self.assertEqual(saved["revision"], 0)
        new_folder = association_folder(self.area, "main", new_key)
        self.assertEqual(sorted(path.name for path in new_folder.iterdir()), ["plan.json"])
        old_folder = association_folder(self.area, "main", old["mission_key"])
        self.assertEqual(b"".join(path.read_bytes() for path in sorted(old_folder.rglob("*")) if path.is_file()),
                         old_bytes)
        # Back on the first mission, its own plan and revision return
        self.profiles.records["main"] = profile("main", MISSION, runtime_profile="profile_main")
        loaded = self.service.load("main")
        self.assertEqual((loaded["revision"], loaded["plan"]["objects"][0]["id"]), (0, "old-zone"))

    def test_rename_keeps_the_records_of_the_profile(self) -> None:
        """A new display name keeps the association, the plan and its revision; saves continue from it."""
        association, _bytes = self.saved_main()
        renamed = replace(self.profiles.records["main"].values, display_name="Renamed Main")
        self.profiles.records["main"] = ProfileRecord(2, renamed)
        loaded = self.service.load("main")
        self.assertEqual((loaded["association"], loaded["revision"], loaded["read_only"]), (association, 0, False))
        self.assertEqual(loaded["plan"]["objects"][0]["id"], "old-zone")
        self.assertEqual(self.service.save("main", loaded["plan"], 0)["revision"], 1)

    def test_remnant_in_the_deleted_folder_changes_nothing(self) -> None:
        """A deleted profile's leftover with folders of main's mission root is no path change and no sharer."""
        association = self.service.active_association("main")
        self.service.save("main", self.plan_for(association), None)
        # The leftover holds main's active key and another key of the same mission root
        remnant = self.area / DELETED_FOLDER / "profile-ghost-20261016T120000Z"
        for key in (association["mission_key"], "e" * 32):
            ghost = {"profile_id": "ghost", "mission_root": MISSION, "mission_key": key, "runtime_profile_key": None}
            MissionMapPlanStore(remnant / key).save(self.plan_for(ghost), None, MOMENT)
        loaded = self.service.load("main")
        self.assertEqual((loaded["path_change"], loaded["read_only"], loaded["revision"]), (None, False, 0))
        self.assertEqual(association_profiles(self.area, association["mission_key"]), ("main",))
        self.assertEqual(self.service.save("main", loaded["plan"], 0)["revision"], 1)


if __name__ == "__main__":
    unittest.main()
