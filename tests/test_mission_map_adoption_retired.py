"""Leaf 10.13: the rows of the retired case for each original state, and no restore offer beside a mismatch.

The rows are the Product Owner texts of 2026-10-17 09:29:14 in evidence 10.13. The restore rule is point 9 of
the Architect entry 2026-10-17 09:30:36 in evidence 10.13.
"""

from __future__ import annotations

import unittest

from dayz_serverman.application.mission_map_adoption import MissionMapAdoption
from dayz_serverman.application.mission_map_adoption_coordinator import MissionMapAdoptionCoordinator
from dayz_serverman.bridge.facade import ApplicationCallError
from dayz_serverman.cli.exit_codes import dispatch_exit
from dayz_serverman.cli.map_wording import READ_ONLY_TEXTS
from dayz_serverman.domain import mission_map_adoption_texts as texts
from dayz_serverman.domain.mission_map_records import copied_original
from dayz_serverman.repositories.mission_map_layout import MISSION_FILE_SET, TargetClass, history_name, target_key
from dayz_serverman.repositories.mission_map_records import OriginalStore
from tests.mission_map_adoption_support import MOMENT, AdoptionFixture
from tests.mission_map_mapgroup_support import zone
from tests.test_mission_map_plan import stored_plan

# The relative mission root of profile main, and the display path that the chain-end original names
MAIN_ROOT = "mpmissions\\dayzOffline.chernarusplus"
LATER_PATH = "D:\\DayZMoved\\mpmissions\\dayzOffline.chernarusplus"


class RetiredOriginalTests(AdoptionFixture):
    """Step 2 always has its row; step 4 has one row for each D5 outcome, and none for the same original."""

    def setUp(self) -> None:
        """Build the return to an earlier path: the active key is retired toward the chain end."""
        super().setUp()
        self.save([zone("rich")])
        self.earlier = self.key()
        self.move_root()
        self.later = self.key()
        self.retire("main", self.earlier, self.later)
        self.save([zone("rich")])
        self.move_root(name="DayZServer")

    def original(self, key: str, display: str, captured_at: str = "2026-10-14T19:00:00Z") -> dict:
        """Write an original of a key whose managed files were all absent."""
        files = [self.entry(path, None, None) for path in MISSION_FILE_SET]
        return OriginalStore(self.area, key).create(
            {"target_class": "mission", "target_key": key, "display_path": display, "mission_root": MAIN_ROOT,
             "terrain_id": "chernarusplus", "captured_at": captured_at, "profile_id": "main",
             "operation_id": "op0001", "files": files}, {})

    def rows(self) -> list[str]:
        """Return the record rows of the review."""
        answer = self.answer()
        self.assertEqual(answer["case"], "retired")
        return answer["records"]

    def expected(self, step: str | None) -> list[str]:
        """Return the rows for one outcome of step 4."""
        values = {"new_path": str(self.mission.resolve()), "other_path": LATER_PATH,
                  "history_name": history_name(self.earlier, MOMENT)}
        rows = [texts.ROW_HISTORY.format(**values), texts.ROW_RETIRED.format(old_path=LATER_PATH),
                texts.ROW_FRESH.format(**values)]
        return rows + ([texts.ROW_RETIRED_ORIGINAL[step].format(**values)] if step else [])

    def test_installed_when_the_active_key_has_no_original(self) -> None:
        self.original(self.later, LATER_PATH)
        self.assertEqual(self.rows(), self.expected("installed"))
        self.assertTrue(self.rows()[-1].endswith(f"installed from the records of {LATER_PATH}."))

    def test_no_row_for_the_same_original(self) -> None:
        source = self.original(self.earlier, str(self.mission))
        record = copied_original(source, target_key=self.later, display_path=LATER_PATH, mission_root=MAIN_ROOT,
                                 case="move", operation_id="adopt-1")
        OriginalStore(self.area, self.later).create(record, {})
        self.assertEqual(self.rows(), self.expected(None))

    def test_a_different_original_becomes_active_on_an_unshared_key(self) -> None:
        self.original(self.earlier, str(self.mission))
        self.original(self.later, LATER_PATH, "2026-10-15T19:00:00Z")
        # Profile second resolves to the active key; without it the key is not shared
        del self.profiles.records["second"]
        self.assertEqual(self.rows(), self.expected("active"))

    def test_the_current_original_stays_on_a_shared_key(self) -> None:
        self.original(self.earlier, str(self.mission))
        self.original(self.later, LATER_PATH, "2026-10-15T19:00:00Z")
        self.assertEqual(self.rows(), self.expected("shared"))
        self.assertEqual(self.reader.review("main").context.original_step, "shared")


class RestoreMismatchTests(AdoptionFixture):
    """While `association_mismatch` holds, no `restore` offer and a refused review; both return after the fix."""

    def test_restore_waits_for_matching_settings(self) -> None:
        folder = self.root / "profile_main"
        plan = stored_plan([zone("rich")])
        runtime = target_key(TargetClass.RUNTIME, folder.resolve())
        plan.update(association=dict(self.plans.active_association("main"), runtime_profile_key=runtime),
                    background=None)
        self.plans.save("main", plan, None)
        self.invalidate()
        self.assertEqual(self.load()["adoption"], {"case": "restore"})
        moved = folder.rename(folder.with_name("profile_elsewhere"))
        loaded = self.load()
        self.assertEqual((loaded["read_only_reason"], loaded["adoption"]), ("association_mismatch", None))
        # A direct review refuses with the reason of the load (Architect 10:11:41): exit 3 and the read-only text
        handler = MissionMapAdoptionCoordinator(MissionMapAdoption(self.reader))
        with self.assertRaises(ApplicationCallError) as raised:
            handler.preview_mission_map_adoption({"profile_id": "main"})
        error = {"code": raised.exception.code.value, "details": raised.exception.details}
        self.assertEqual((error["code"], error["details"]["reason"]),
                         ("PROFILE_CONTEXT_MISMATCH", "association_mismatch"))
        self.assertEqual(dispatch_exit(error), 3)
        text = READ_ONLY_TEXTS[error["details"]["reason"]]
        self.assertTrue(text.startswith("Read only: the saved map plan does not match"))
        moved.rename(folder)
        loaded = self.load()
        self.assertEqual((loaded["read_only_reason"], loaded["adoption"]), ("adoption_required", {"case": "restore"}))
        self.assertEqual(handler.preview_mission_map_adoption({"profile_id": "main"})["case"], "restore")


if __name__ == "__main__":
    unittest.main()
