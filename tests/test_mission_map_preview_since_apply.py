"""End-to-end preview after a real Apply (QA-8.6-F2): objects and global value since the last Apply, current groups."""

from __future__ import annotations

import copy
import unittest

from tests.mission_map_apply_support import ApplyFixture
from tests.mission_map_mapgroup_support import zone
from tests.test_mission_map_infected import encounter


class SinceLastApplyTests(ApplyFixture):
    """The preview compares the saved plan with the plan objects of the version 2 applied record."""

    def test_added_changed_and_removed_objects_and_the_applied_global_value(self) -> None:
        """Change one zone, remove one, add one and rename an encounter; only the first three are listed."""
        first = dict(zone("rich"), id="a", name="Zone A")
        second = dict(zone("sparse", 100.0, 100.0, 30.0), id="b", name="Zone B")
        infected = encounter("e1", x=600.0, z=600.0, radius=40.0)
        self.save([first, second, infected], 25)
        self.run_apply()
        _original, _baseline, (applied, _revision) = self.records()
        changed = dict(copy.deepcopy(first), radius=60.0)
        added = dict(zone("very_rich", 300.0, 300.0, 20.0), id="c", name="Zone C")
        renamed = dict(infected, name="Renamed", visible=False, locked=True)
        self.save([changed, renamed, added], 50, expected=0)
        answer = self.review().answer
        objects = answer["objects"]
        self.assertTrue(objects["previous"])
        self.assertEqual([item["id"] for item in objects["added"]], ["c"])
        self.assertEqual([item["id"] for item in objects["changed"]], ["a"])
        self.assertEqual(objects["removed"], [{"id": "b", "name": "Zone B", "kind": "loot_circle", "provider": None}])
        self.assertEqual(objects["global"], {"current": 50, "applied": 25})
        # The loot report gets the generated groups of the applied realization as the current groups
        current = {item["name"] for item in applied["generated_groups"]}
        candidate = {clone.name for clone in self.review().candidates.mapgroups.clones}
        self.assertTrue(current)
        self.assertEqual(answer["loot"]["removed_groups"], sorted(current - candidate))

    def test_after_an_apply_of_the_same_plan_nothing_is_listed(self) -> None:
        """Directly after Apply the preview lists no object and the same global value."""
        self.save([zone("rich")], 25)
        self.run_apply()
        objects = self.review().answer["objects"]
        self.assertEqual((objects["previous"], objects["added"], objects["changed"], objects["removed"]),
                         (True, [], [], []))
        self.assertEqual(objects["global"], {"current": 25, "applied": 25})


if __name__ == "__main__":
    unittest.main()
