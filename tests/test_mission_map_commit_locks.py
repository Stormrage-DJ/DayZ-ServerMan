"""Verify real plan saves wait for commits and staging namespaces remain disjoint."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from dayz_serverman.application.folder_writer_scope import WriterScope
from dayz_serverman.application.mission_map_plans import MissionMapPlanService
from dayz_serverman.repositories.mission_map_layout import association_folder, original_folder
from dayz_serverman.repositories.mission_map_plans import MissionMapPlanStore
from dayz_serverman.repositories.mission_map_publication import MissionMapPublication
from tests.test_mission_map_commit_fixture import CommitFixture
from tests.test_mission_map_plan import stored_plan
from tests.test_mission_map_plan_service import Profiles, profile


class CommitLockTests(CommitFixture):
    """The same AssociationLocks instance serializes the actual plan service and the commit step."""

    def plan_service(self, journal):
        """Build a real plan service whose records resolve to the fixture mission and shared locks."""
        settings = SimpleNamespace(load=lambda: SimpleNamespace(dayz_root=str(self.dayz), revision=7))
        return MissionMapPlanService(Profiles(profile("main", journal.mission_root)), settings,
                                     self.paths.mission_map, self.locks)

    def test_save_waits_until_commit_releases_the_shared_association_lock(self):
        """A waiting save reaches its locked validator only after all commit records become published."""
        journal, updates, candidates = self.candidates()
        staged, release, attempted, saved = (threading.Event() for _ in range(4))
        failures = []
        original_lock_for = self.locks.lock_for
        def lock_for(profile_id, key):
            """Prove the save reached the same lock while the commit still holds it."""
            if threading.current_thread().name == "plan-save":
                attempted.set()
            return original_lock_for(profile_id, key)
        self.locks.lock_for = lock_for
        def pause(phase, _index):
            """Pause while all record stages exist, before the durable journal and target write."""
            if phase == "AFTER_STAGE_RECORDS":
                staged.set()
                if not release.wait(5):
                    raise OSError("test release timed out")
        def commit():
            """Publish from the operation-lane worker and expose failures to the test."""
            try:
                self.run_commit(journal, updates, candidates, self.service(pause))
            except BaseException as error:
                failures.append(error)
        plan_service = self.plan_service(journal)
        plan = dict(stored_plan([]), association=plan_service.active_association("main"))
        def save():
            """Use the actual save path and inspect the committed records after its lock acquisition."""
            try:
                def validate(_plan):
                    """Confirm all commit targets are published when the pending save proceeds."""
                    self.assertTrue(all((self.paths.root / update.target_path).is_file() for update in updates))
                plan_service.save("main", plan, None, before_save=validate)
                saved.set()
            except BaseException as error:
                failures.append(error)
        worker = threading.Thread(target=commit, name="commit")
        saver = threading.Thread(target=save, name="plan-save")
        worker.start()
        try:
            self.assertTrue(staged.wait(3))
            saver.start()
            self.assertTrue(attempted.wait(3))
            self.assertFalse(saved.is_set())
            self.assertFalse((original_folder(self.paths.mission_map, journal.mission_key) / "original.json").exists())
        finally:
            release.set()
            worker.join(5)
            if saver.ident is not None:
                saver.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(saver.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(saved.is_set())

    def test_rollback_never_touches_plan_save_staging(self):
        """Commit recovery removes only commit staging; a later plan save retains its own dated evidence."""
        journal, updates, candidates = self.candidates()
        folder = association_folder(self.paths.mission_map, "main", journal.mission_key)
        folder.mkdir(parents=True, exist_ok=True)
        plan_stage = folder / ".plan.json.aabbcc.tmp"
        plan_stage.write_bytes(b"interrupted plan save")
        def crash(phase, _index):
            """Stop after PREPARED with manager commit stages and the distinct plan-save stage."""
            if phase == "AFTER_PREPARED":
                raise KeyboardInterrupt("crash")
        with self.assertRaises(KeyboardInterrupt):
            self.run_commit(journal, updates, candidates, self.service(crash))
        store = MissionMapPlanStore(folder)
        self.assertEqual(store.leftovers(), (plan_stage,))
        writer = WriterScope(None)
        with writer.held_for():
            publication = MissionMapPublication(self.paths, self.repository, writer)
            self.assertFalse(publication.inspect(self.dayz, self.record_hooks())["blocked"])
        self.assertEqual(plan_stage.read_bytes(), b"interrupted plan save")
        self.assertFalse(list(folder.glob("*.staged-*")))
        plan_service = self.plan_service(journal)
        plan = dict(stored_plan([]), association=plan_service.active_association("main"))
        result = plan_service.save("main", plan, None)
        self.assertEqual(len(result["interrupted_evidence"]), 1)
        self.assertEqual((folder / result["interrupted_evidence"][0]).read_bytes(), b"interrupted plan save")
