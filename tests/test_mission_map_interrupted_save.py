"""Reopen after an interrupted plan save shows the last saved plan; the save files stay as evidence (5.4, R15)."""

from __future__ import annotations

import copy
import unittest
from unittest import mock

from dayz_serverman.application.mission_map_coordinator import MissionMapCoordinator
from dayz_serverman.bridge.contracts import ErrorCode
from dayz_serverman.repositories import json_store
from dayz_serverman.repositories.mission_map_layout import association_folder, interrupted_name
from tests.test_mission_map_editor_load_save import CoordinatorFixture
from tests.test_mission_map_plan import circle
from tests.test_mission_map_plan_service import MOMENT


class InterruptedPlanSaveTests(CoordinatorFixture):
    """Fault injection at the atomic replace of the editor save, then a load as after a crash."""

    def test_reopen_after_an_interrupted_save_shows_the_last_saved_plan(self) -> None:
        """The failed save keeps the record; a new owner session loads it and the next save keeps the evidence."""
        plan = self.draft(objects=[circle("kept")])
        self.assertEqual(self.save(plan)["revision"], 0)
        folder = association_folder(self.area, "main", plan["association"]["mission_key"])
        record = (folder / "plan.json").read_bytes()
        changed = copy.deepcopy(plan)
        changed["objects"].append(circle("unsaved", x=300))
        # Fault injection: the replace of the staged file fails as if the process stopped there
        with mock.patch.object(json_store, "replace_file", side_effect=OSError("power loss")):
            self.refused(lambda: self.save(changed, 0), ErrorCode.STORAGE_FAILURE, "storage_failure")
        self.assertEqual((folder / "plan.json").read_bytes(), record)
        leftovers = sorted(folder.glob(".plan.json.*.tmp"))
        self.assertEqual(len(leftovers), 1)
        staged = leftovers[0].read_bytes()
        # Reopen: a new coordinator and service, as after a restart, loads the last saved plan
        reopened = MissionMapCoordinator(self.make_service())
        loaded = reopened.load_mission_map({"profile_id": "main"})
        self.assertEqual((loaded["state"], loaded["revision"], loaded["read_only"]), ("VALID", 0, False))
        self.assertEqual([item["id"] for item in loaded["plan"]["objects"]], ["kept"])
        self.assertEqual(loaded["interrupted_evidence"], [leftovers[0].name])
        # Nothing was discarded or deleted by the load; the next save renames the leftover to a dated evidence name
        saved = reopened.save_mission_map_plan({"profile_id": "main", "plan": loaded["plan"], "expected_revision": 0,
                                               "background_selection_id": None})
        evidence = interrupted_name(MOMENT, 1)
        self.assertEqual((saved["revision"], saved["interrupted_evidence"]), (1, [evidence]))
        self.assertEqual((folder / evidence).read_bytes(), staged)
        self.assertEqual(sorted(path.name for path in folder.iterdir() if path.name.startswith(("plan", ".plan"))),
                         [evidence, "plan.json"])

    def test_a_load_without_a_save_changes_no_file(self) -> None:
        """A discard sends no save (Edge: test_a_real_discard_keeps_the_committed_plan); the next load writes nothing."""
        plan = self.draft(objects=[circle("kept")])
        self.save(plan)
        folder = association_folder(self.area, "main", plan["association"]["mission_key"])
        before = {path.name: path.read_bytes() for path in folder.iterdir()}
        loaded = MissionMapCoordinator(self.make_service()).load_mission_map({"profile_id": "main"})
        self.assertEqual((loaded["revision"], loaded["plan"]["objects"]), (0, plan["objects"]))
        self.assertEqual({path.name: path.read_bytes() for path in folder.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()
