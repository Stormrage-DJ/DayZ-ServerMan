"""Verify coordinator association refusals, lock timing, actual applied state and write ownership."""

from __future__ import annotations

import copy
import threading
import unittest
from unittest import mock

from dayz_serverman.bridge.contracts import ErrorCode
from dayz_serverman.bridge.facade import ApplicationCallError
from dayz_serverman.domain.mission_map_plan import configuration_fingerprint
from dayz_serverman.mission_map_composition import build_mission_map
from dayz_serverman.repositories.json_store import StagingPolicy, VersionedJsonRepository
from dayz_serverman.repositories.mission_map_layout import association_folder
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore
from dayz_serverman.repositories.paths import PortablePaths
from tests.test_mission_map_editor_load_save import CoordinatorFixture
from tests.test_mission_map_plan import circle
from tests.test_mission_map_records import VANILLA, applied, baseline, sha


# Longest thread join; correct tests finish immediately after their lock is released
JOIN_SECONDS = 5.0
# An event that fires during this wait proves a save bypassed a held association lock
BLOCKED_SECONDS = 0.1


class CoordinatorAssociationTests(CoordinatorFixture):
    """Cover moved/retired associations and the manager-only persistence boundary."""

    def test_path_change_returns_actual_old_plan_read_only_and_refuses_repeated_saves(self) -> None:
        """An autosave cannot create the new association or end a mission-path-change state."""
        old = self.old_folder("earlier-root")
        plan = self.draft()
        loaded = self.load()
        self.assertEqual(loaded["read_only_reason"], "mission_path_changed")
        self.assertTrue(loaded["read_only"])
        self.assertEqual(loaded["plan"]["plan_id"], "earlier-root")
        self.assertEqual(loaded["path_change"]["shown_mission_key"], old.name)
        for _attempt in range(2):
            self.refused(lambda: self.save(plan), ErrorCode.PROFILE_CONTEXT_MISMATCH, "mission_path_changed")
        active = association_folder(self.area, "main", plan["association"]["mission_key"])
        self.assertFalse(active.exists())
        self.assertIsNone(self.load("second")["path_change"])

    def test_retired_active_folder_follows_chain_and_is_never_saved(self) -> None:
        """Returning to an earlier path shows the newest unretired plan, even across different relative roots."""
        plan = self.draft()
        self.save(plan)
        active = association_folder(self.area, "main", plan["association"]["mission_key"])
        before = (active / "plan.json").read_bytes()
        middle = self.old_folder("middle-root", "mpmissions\\different-a")
        newest = self.old_folder("newest-root", "mpmissions\\different-b")
        for folder, target in ((active, middle.name), (middle, newest.name)):
            VersionedJsonRepository(folder / "retired.json", 1).save(
                {"operation_id": "adopt-1", "retired_at": "2026-10-10T13:00:00Z", "new_mission_key": target}, None)
        loaded = self.load()
        self.assertEqual(loaded["read_only_reason"], "returned_to_earlier_path")
        self.assertTrue(loaded["read_only"])
        self.assertEqual(loaded["plan"]["plan_id"], "newest-root")
        self.assertEqual(loaded["path_change"]["shown_mission_key"], newest.name)
        self.refused(lambda: self.save(plan, 0), ErrorCode.PROFILE_CONTEXT_MISMATCH, "returned_to_earlier_path")
        self.assertEqual((active / "plan.json").read_bytes(), before)

    def test_broken_or_cyclic_retired_chain_refuses_without_reviving_active_plan(self) -> None:
        """Damaged adoption metadata never authorizes a write to a retired folder."""
        plan = self.draft()
        self.save(plan)
        active = association_folder(self.area, "main", plan["association"]["mission_key"])
        before = (active / "plan.json").read_bytes()
        marker = VersionedJsonRepository(active / "retired.json", 1)
        for revision, target in ((None, "f" * 32), (0, active.name)):
            with self.subTest(target=target):
                marker.save({"operation_id": "adopt-1", "retired_at": "2026-10-10T13:00:00Z",
                             "new_mission_key": target}, revision)
                self.refused(self.load, ErrorCode.PROFILE_CONTEXT_MISMATCH, "returned_to_earlier_path")
                self.refused(lambda: self.save(plan, 0), ErrorCode.PROFILE_CONTEXT_MISMATCH, "returned_to_earlier_path")
                self.assertEqual((active / "plan.json").read_bytes(), before)

    def test_save_changes_only_manager_records_and_never_calls_operation_lane(self) -> None:
        """Real saves leave every synthetic game file and directory unchanged, with no lane collaborator."""
        self.add_header()
        (self.mission / "custom.xml").write_bytes(b"<custom>kept</custom>")
        before = {str(path.relative_to(self.root)): path.read_bytes() if path.is_file() else None
                  for path in self.root.rglob("*")}
        self.save(self.draft(objects=[circle()]))
        self.save(self.draft(objects=[circle()]), 0)
        after = {str(path.relative_to(self.root)): path.read_bytes() if path.is_file() else None
                 for path in self.root.rglob("*")}
        self.assertEqual(after, before)
        self.assertTrue(self.area.is_dir())
        self.assertFalse(hasattr(self.coordinator, "_operations"))

    def test_storage_failure_is_retryable_and_does_not_report_success(self) -> None:
        """A failed atomic replace reports storage_failure and preserves the earlier plan."""
        plan = self.draft()
        self.save(plan)
        with mock.patch("dayz_serverman.repositories.json_store.replace_file", side_effect=OSError("disk refused")):
            error = self.refused(lambda: self.save(dict(plan, global_abundance=50), 0),
                                 ErrorCode.STORAGE_FAILURE, "storage_failure")
        self.assertTrue(error.retryable)
        self.assertEqual(self.load()["plan"]["global_abundance"], 0)

    def test_composition_exposes_coordinator_without_registering_handlers(self) -> None:
        """The builder owns one coordinator and preserves the same lock set for plans and commits."""
        parts = build_mission_map(PortablePaths.from_root(self.area.parent.parent), self.profiles,
                                 self.service._settings)
        self.assertIs(parts.coordinator._plans, parts.plans)
        self.assertIs(parts.plans._locks, parts.locks)
        self.assertEqual(set(parts.coordinator.handlers()), {"load_mission_map", "save_mission_map_plan"})
        self.assertFalse(self.area.exists())
        observer = build_mission_map(PortablePaths.from_root(self.area.parent.parent), self.profiles,
                                    self.service._settings, staging=StagingPolicy.OBSERVER)
        with self.assertRaises(ApplicationCallError) as caught:
            observer.coordinator.save_mission_map_plan({"profile_id": "main", "plan": self.draft(),
                                                       "expected_revision": None, "background_selection_id": None})
        self.assertEqual(caught.exception.code, ErrorCode.CONTROL_CONFLICT)
        self.assertFalse(self.area.exists())


class CoordinatorRecordTests(CoordinatorFixture):
    """Cover actual applied fingerprints, D3 reporting and the in-lock reference freshness check."""

    def record_store(self, plan: dict, kind: str) -> AssociationRecordStore:
        """Bind a record store to the same temporary association as the coordinator."""
        return AssociationRecordStore(self.area, "main", plan["association"]["mission_key"], kind)

    def test_draft_flag_uses_actual_applied_state_and_undo_returns_to_match(self) -> None:
        """A null reference is not rebound; actual applied state still governs Draft changes."""
        plan = self.draft(objects=[circle()])
        self.save(plan)
        store = self.record_store(plan, "applied")
        record = applied(key=plan["association"]["mission_key"])
        record["configuration_fingerprint"] = configuration_fingerprint(plan)
        store.save(record, {sha(VANILLA): VANILLA}, None)
        self.assertFalse(self.load()["draft_changes"])
        self.assertIsNone(self.load()["plan"]["records"]["applied"])
        changed = dict(plan, global_abundance=25)
        self.assertTrue(self.save(changed, 0)["draft_changes"])
        self.assertTrue(self.load()["draft_changes"])
        self.assertFalse(self.save(plan, 1)["draft_changes"])
        self.assertFalse(self.load()["draft_changes"])

    def test_stale_reference_is_reported_preserved_and_refuses_save(self) -> None:
        """A missing referenced record asks for a reload and never becomes a null reference silently."""
        plan = self.draft()
        plan["records"]["baseline"] = {"revision": 0, "manifest_sha256": "a" * 64}
        self.service.save("main", plan, None)
        loaded = self.load()
        self.assertEqual(loaded["plan"]["records"], plan["records"])
        self.assertIn("Reload", loaded["detail"])
        self.assertEqual(loaded["problems"][0]["rule"], "records_stale")
        self.assertIsNone(loaded["read_only_reason"])
        error = self.refused(lambda: self.save(plan, 0), ErrorCode.REVISION_CONFLICT, "stale_revision")
        self.assertTrue(error.retryable)
        self.assertEqual(self.load()["revision"], 0)

    def test_waiting_save_checks_record_freshness_after_commit_releases_shared_lock(self) -> None:
        """A commit changes a referenced record while save waits; the save then refuses without overwriting."""
        plan = self.draft()
        store = self.record_store(plan, "baseline")
        record = baseline(key=plan["association"]["mission_key"])
        store.save(record, {sha(VANILLA): VANILLA}, None)
        plan["records"]["baseline"] = store.identity()
        self.save(plan)
        started, finished = threading.Event(), threading.Event()
        failures: list[Exception] = []

        def save_waiting() -> None:
            """Try a stale document while the main thread represents a record commit."""
            started.set()
            try:
                self.save(copy.deepcopy(plan), 0)
            except Exception as error:
                failures.append(error)
            finally:
                finished.set()

        # A plan save must wait for this lock and see the committed baseline revision afterwards
        with self.service._locks.hold("main", plan["association"]["mission_key"]):
            thread = threading.Thread(target=save_waiting, daemon=True)
            thread.start()
            self.assertTrue(started.wait(JOIN_SECONDS))
            self.assertFalse(finished.wait(BLOCKED_SECONDS))
            store.save(record, {sha(VANILLA): VANILLA}, 0)
        thread.join(JOIN_SECONDS)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 1)
        error = failures[0]
        self.assertIsInstance(error, ApplicationCallError)
        self.assertEqual((error.code, error.details["reason"]), (ErrorCode.REVISION_CONFLICT, "stale_revision"))
        self.assertEqual(self.service.load("main")["revision"], 0)


if __name__ == "__main__":
    unittest.main()
