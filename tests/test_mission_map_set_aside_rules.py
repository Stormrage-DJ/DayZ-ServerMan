"""Set-aside rework rules (5.3): the lock, storage refusals and a saved plan of another association (D2)."""

from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from dayz_serverman.application.mission_map_coordinator import READ_ONLY_REASONS
from dayz_serverman.application.mission_map_plans import _runtime_key
from dayz_serverman.bridge.contracts import ErrorCode
from dayz_serverman.domain.mission_map_plan import canonical_json
from dayz_serverman.repositories import mission_map_plans as store_module
from dayz_serverman.repositories.mission_map_layout import association_folder
from tests.test_mission_map_editor_load_save import CoordinatorFixture
from tests.ui_harness_support import FRONTEND


# The fixed clock of the service fixture gives this dated name
DATED_NAME = "plan.invalid-20261010T130000Z.json"


class SetAsideRuleTests(CoordinatorFixture):
    """Call the real coordinator over the real plan service and store."""

    def setUp(self) -> None:
        """Keep the active association folder of profile main."""
        super().setUp()
        self.key = self.service.active_association("main")["mission_key"]
        self.folder = association_folder(self.area, "main", self.key)

    def set_aside(self) -> dict:
        """Call the complete set-aside request of profile main."""
        return self.coordinator.set_aside_mission_map_plan({"profile_id": "main", "mission_key": self.key})

    def store(self, data: bytes) -> None:
        """Write the plan record of the active association folder."""
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "plan.json").write_bytes(data)

    def names(self) -> list[str]:
        """Return the file names of the association folder."""
        return sorted(path.name for path in self.folder.iterdir())

    def test_the_rename_runs_under_the_association_lock(self) -> None:
        """The lock of the active association is held at the rename; a held lock delays it (QA-5.3-F4)."""
        self.store(b"{bad JSON")
        lock = self.service._locks.lock_for("main", self.key)
        seen: list[bool] = []
        real = store_module.replace_file
        spy = lambda *paths: (seen.append(lock.locked()), real(*paths))  # noqa: E731
        with mock.patch.object(store_module, "replace_file", side_effect=spy):
            self.set_aside()
        self.assertEqual((seen, lock.locked(), self.names()), ([True], False, [DATED_NAME]))
        # A save or a commit that holds the lock keeps the record in place until it ends
        self.store(b"{bad JSON")
        self.service._clock = lambda: datetime(2026, 10, 10, 13, 0, 1, tzinfo=timezone.utc)
        done: list[dict] = []
        with self.service._locks.hold("main", self.key):
            worker = threading.Thread(target=lambda: done.append(self.set_aside()))
            worker.start()
            time.sleep(0.3)
            self.assertEqual((done, (self.folder / "plan.json").exists()), ([], True))
        worker.join(5)
        self.assertEqual(done[0]["set_aside"], "plan.invalid-20261010T130001Z.json")

    def test_a_denied_or_shared_file_is_a_retryable_storage_failure(self) -> None:
        """Set aside and save answer STORAGE_FAILURE for an owner; only an observer gets CONTROL_CONFLICT (QA-5.3-F3)."""
        self.store(b"{bad JSON")
        for number in (5, 32):
            denied = PermissionError(13, "Access is denied")
            denied.winerror = number
            with self.subTest(winerror=number), mock.patch.object(store_module, "replace_file", side_effect=denied):
                error = self.refused(self.set_aside, ErrorCode.STORAGE_FAILURE, "storage_failure")
                self.assertTrue(error.retryable)
                self.assertNotIn("observer", str(error))
                self.assertEqual(self.names(), ["plan.json"])
        (self.folder / "plan.json").unlink()
        with mock.patch.object(store_module.MissionMapPlanStore, "save", side_effect=PermissionError(13, "denied")):
            error = self.refused(lambda: self.save(self.draft(), None), ErrorCode.STORAGE_FAILURE, "storage_failure")
        self.assertTrue(error.retryable)
        self.assertNotIn("observer", str(error))

    def test_a_valid_plan_of_another_association_is_invalid_saved_data(self) -> None:
        """P12: load is read only, save refuses, set aside renames it and the next plan starts at revision 0."""
        other_key = dict(self.draft(), association={**self.draft()["association"], "mission_key": "0" * 32})
        cases = (("other profile", self.draft("second")), ("other mission", other_key),
                 ("other profile and mission", self.draft("other")))
        for second, (label, plan) in enumerate(cases):
            with self.subTest(label):
                moment = datetime(2026, 10, 10, 13, 0, second, tzinfo=timezone.utc)
                self.service._clock = lambda moment=moment: moment
                data = canonical_json({"schema_version": 1, "revision": 3, "plan": plan})
                self.store(data)
                loaded = self.load()
                self.assertEqual((loaded["state"], loaded["read_only"], loaded["read_only_reason"], loaded["plan"]),
                                 ("OTHER_ASSOCIATION", True, "invalid_saved_data", None))
                self.assertEqual(loaded["detail"], store_module.OTHER_ASSOCIATION_DETAIL)
                self.refused(lambda: self.save(self.draft(), 3), ErrorCode.REVISION_CONFLICT, "invalid_saved_data")
                self.assertEqual((self.folder / "plan.json").read_bytes(), data)
                name = self.set_aside()["set_aside"]
                self.assertEqual((self.folder / name).read_bytes(), data)
                self.assertEqual(self.load()["state"], "MISSING")
                self.assertEqual(self.save(self.draft(), None)["revision"], 0)
                (self.folder / "plan.json").unlink()

    def test_inspect_of_a_plan_of_another_association_is_invalid_saved_data(self) -> None:
        """QA-5.3-F7 (R07): Inspect of the saved plan uses the bound store, as load and save do."""
        self.add_header()
        data = canonical_json({"schema_version": 1, "revision": 3, "plan": self.draft("other")})
        self.store(data)
        start = self.coordinator.inspection.handlers()["inspect_mission_map"]
        error = self.refused(lambda: start({"profile_id": "main", "plan": None, "target": {"point": {"x": 1, "z": 1}}}),
                             ErrorCode.REVISION_CONFLICT, "invalid_saved_data")
        self.assertFalse(error.retryable)
        self.assertEqual((self.folder / "plan.json").read_bytes(), data)

    def test_the_path_change_load_compares_the_old_plan_with_its_own_folder(self) -> None:
        """A valid plan of an earlier mission root stays shown read only with the path-change reason."""
        self.old_folder("old-root")
        loaded = self.load()
        self.assertEqual((loaded["state"], loaded["read_only_reason"], loaded["plan"]["plan_id"]),
                         ("VALID", "mission_path_changed", "old-root"))

    def test_a_plan_that_the_profile_settings_do_not_match_loads_read_only(self) -> None:
        """QA-5.3-F6 (P16): another mission root spelling or runtime folder is read only, never a failing save."""
        runtime = _runtime_key(self.root.resolve(), self.profiles.read("main"))
        cases = (("mission root", {"mission_root": "mpmissions/elsewhere"}, "the plan names another mission root"),
                 ("runtime folder", {"runtime_profile_key": "f" * 32}, "the plan names another runtime profile folder"))
        for label, change, detail in cases:
            with self.subTest(label):
                plan = self.draft()
                plan["association"] = {**plan["association"], **change}
                data = canonical_json({"schema_version": 1, "revision": 0, "plan": plan})
                self.store(data)
                loaded = self.load()
                self.assertEqual((loaded["state"], loaded["read_only"], loaded["read_only_reason"], loaded["detail"]),
                                 ("VALID", True, "association_mismatch", detail))
                self.assertEqual(loaded["plan"]["plan_id"], plan["plan_id"])
                # The shown plan cannot be saved as it is; the window offers no save, because the load is read only
                self.refused(lambda: self.save(loaded["plan"], 0), ErrorCode.PROFILE_CONTEXT_MISMATCH, "association_mismatch")
                self.refused(self.set_aside, ErrorCode.REVISION_CONFLICT, "plan_not_invalid")
                self.assertEqual((self.folder / "plan.json").read_bytes(), data)
        # Matching settings, also with the profile's own runtime folder, still load writable
        for runtime_key in (None, runtime):
            with self.subTest(runtime_key=runtime_key):
                plan = self.draft()
                plan["association"] = {**plan["association"], "runtime_profile_key": runtime_key}
                self.store(canonical_json({"schema_version": 1, "revision": 0, "plan": plan}))
                loaded = self.load()
                self.assertEqual((loaded["state"], loaded["read_only"], loaded["read_only_reason"]), ("VALID", False, None))
                self.assertEqual(self.save(loaded["plan"], 0)["revision"], 1)

    def test_a_missing_runtime_folder_loads_read_only(self) -> None:
        """QA-5.3-F8 (P18): the plan names the profile's own runtime folder, which is gone; load is read only."""
        plan = self.draft()
        own = _runtime_key(self.root.resolve(), self.profiles.read("main"))
        plan["association"] = {**plan["association"], "runtime_profile_key": own}
        data = canonical_json({"schema_version": 1, "revision": 0, "plan": plan})
        self.store(data)
        (self.root / "profile_main").rmdir()
        loaded = self.load()
        self.assertEqual((loaded["state"], loaded["read_only"], loaded["read_only_reason"], loaded["detail"]),
                         ("VALID", True, "association_mismatch",
                          "the runtime profile folder of this profile is missing or outside the DayZ root"))
        # Save stays refused, and set aside does not apply to a valid record
        self.refused(lambda: self.save(loaded["plan"], 0), ErrorCode.NOT_FOUND, "mission_unresolved")
        self.refused(self.set_aside, ErrorCode.REVISION_CONFLICT, "plan_not_invalid")
        self.assertEqual((self.folder / "plan.json").read_bytes(), data)

    def test_every_read_only_reason_has_window_wording(self) -> None:
        """Wording parity: each read-only reason of a load has its own window text."""
        script = (FRONTEND / "map_editor_wording.js").read_text(encoding="utf-8")
        for reason in READ_ONLY_REASONS:
            with self.subTest(reason=reason):
                self.assertTrue(f'  {reason}: "Read only: ' in script, reason)

    def test_the_detail_has_the_same_wording_in_the_window(self) -> None:
        """Wording parity: the window names the state with the backend detail text."""
        script = (FRONTEND / "map_editor_wording.js").read_text(encoding="utf-8")
        entry = f'OTHER_ASSOCIATION: "{store_module.OTHER_ASSOCIATION_DETAIL}"'
        self.assertTrue(entry in script, entry)
        self.assertEqual(store_module.PlanState.OTHER_ASSOCIATION.value, "OTHER_ASSOCIATION")


if __name__ == "__main__":
    unittest.main()
