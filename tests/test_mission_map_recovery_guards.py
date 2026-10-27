"""Recovery refusals, shared lock order, active-lane deferral and observer read-only reporting."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dayz_serverman.adapters.windows.server_folder_lock import FolderLockFile, FolderWriter, ServerFolderBusy
from dayz_serverman.application.folder_writer_scope import STARTUP_RECOVERY_WAIT_SECONDS
from dayz_serverman.application.mission_map_recovery import EDITOR_KIND, RECOVERY_NO_ROOT, RECOVERY_NOT_STOPPED, RECOVERY_UNREADABLE
from dayz_serverman.application.mission_map_recovery_coordinator import MissionMapRecoveryCoordinator
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.domain.lifecycle import ServerState
from dayz_serverman.repositories.mission_map_commit_records import CommitRecords
from dayz_serverman.repositories.mission_map_recovery_cleanup import cleanup_orphans
from dayz_serverman.session_observer import pending_recoveries
from tests.test_mission_map_journal_paths import snapshot
from tests.test_mission_map_recovery_phases import RecoveryFixture


class RecoveryGuardTests(RecoveryFixture):
    """Every refused guard leaves the durable tree unchanged and never starts a process."""

    def test_journal_progress_during_guard_wait_blocks_without_phase_write(self):
        """Refresh a READY snapshot that advanced to a touched third state before the guard was acquired."""
        journal = self.crash_at("AFTER_PREPARED")
        recovery = self.recovery()
        original_guard = recovery.guard.stopped
        durable_tree = {}

        @contextmanager
        def stopped(root):
            """Make the current publication progress visible while recovery waits for its guard."""
            journal.phase, journal.publication_started = "PUBLISHING", True
            journal.groups[0].state = "PUBLISHING"
            self.repository.save(journal)
            Path(journal.groups[0].target_path).write_bytes(b"admin third state")
            durable_tree.update(snapshot(Path(self.temp.name)))
            with original_guard(root):
                yield

        recovery.guard.stopped = stopped
        self.events.clear()
        self.assertTrue(recovery.inspect()["blocked"])
        self.assertEqual(snapshot(Path(self.temp.name)), durable_tree)
        self.assertEqual(self.repository.load(self.repository.path_for(journal.operation_id)).phase, "PUBLISHING")
        self.assertEqual(self.events, ["mutex", "stopped", "writer", "association", "release association",
                                       "release writer", "release mutex"])

    def test_guarded_writer_refresh_rejects_unreadable_current_journals(self):
        """A current read failure or unreadable record blocks before association selection and any write."""
        self.crash_at("AFTER_PREPARED")
        recovery = self.recovery()
        initial = recovery.journals.records()
        before = snapshot(Path(self.temp.name))
        for current in (OSError("unavailable"), ((initial[0][0], None),)):
            with self.subTest(current=type(current).__name__), patch.object(recovery.journals, "records", side_effect=[initial, current]) as reads:
                self.events.clear()
                self.assertEqual(recovery.inspect()["reason"], RECOVERY_UNREADABLE)
                self.assertEqual(reads.call_count, 2)
                self.assertEqual(snapshot(Path(self.temp.name)), before)
                self.assertEqual(self.events, ["mutex", "stopped", "writer", "release writer", "release mutex"])

    def test_missing_root_has_the_repairable_editor_block(self):
        """The owner block carries the existing QF-069 missing-root cause."""
        self.crash_at("AFTER_PREPARED")
        recovery = self.recovery()
        recovery.settings = SimpleNamespace(load=lambda: SimpleNamespace(dayz_root=None))
        operations = OperationManager(OperationStore(self.paths.operations))
        self.addCleanup(operations.shutdown, 2)
        before = snapshot(Path(self.temp.name))
        MissionMapRecoveryCoordinator(recovery, operations).startup()
        self.assertEqual(operations.recovery_block_pair(), (RECOVERY_NO_ROOT, EDITOR_KIND))
        self.assertTrue(operations._recovery_blocks.only_cause("MISSING_DAYZ_ROOT"))
        self.assertEqual(snapshot(Path(self.temp.name)), before)
        self.assertFalse(any(self.paths.mission_map.rglob("original.json")))

    def test_running_server_refuses_before_writer_and_any_write(self):
        """STOPPED proof precedes writer acquisition and association locks."""
        self.crash_at("AFTER_PREPARED")
        self.events.clear()
        self.ports.state = ServerState.RUNNING_MANAGED
        before = snapshot(Path(self.temp.name))
        self.assertEqual(self.recovery().inspect()["reason"], RECOVERY_NOT_STOPPED)
        self.assertEqual(self.events, ["mutex", "stopped", "release mutex"])
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_busy_writer_refuses_without_any_write(self):
        """A busy writer takes the ordinary recovery block path."""
        self.crash_at("AFTER_PREPARED")
        recovery = self.recovery()

        @contextmanager
        def busy(_wait):
            """Refuse instead of entering the writer body."""
            raise ServerFolderBusy()
            yield

        recovery.folder_writer = SimpleNamespace(exclusive=busy)
        before = snapshot(Path(self.temp.name))
        self.assertEqual(recovery.inspect()["reason"], RECOVERY_NOT_STOPPED)
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_real_nonreentrant_writer_is_acquired_once_before_shared_locks(self):
        """A real Windows writer detects accidental nesting; recovery uses one startup hold."""
        self.crash_at("AFTER_PREPARED")
        recovery = self.recovery()
        handle = FolderLockFile.create(self.paths.root / "writer-test.lock")
        self.addCleanup(handle.close)
        writer = FolderWriter(handle)
        recovery.folder_writer = writer
        self.events.clear()
        rollback = CommitRecords.rollback

        def checked(records, journal):
            """Prove that record cleanup holds the real writer and the shared association lock."""
            self.assertIsNotNone(writer._owner)
            self.assertTrue(self.locks.lock_for(journal.profile_id, journal.mission_key).locked())
            return rollback(records, journal)

        def checked_sweep(paths, locks):
            """Prove broad cleanup retains the writer after releasing the journal association hold."""
            self.assertIsNotNone(writer._owner)
            self.assertFalse(locks.lock_for("main", self.mission_key()).locked())
            return cleanup_orphans(paths, locks)

        with patch.object(writer, "exclusive", wraps=writer.exclusive) as exclusive, patch.object(CommitRecords, "rollback", checked), \
                patch("dayz_serverman.application.mission_map_recovery.cleanup_orphans", checked_sweep):
            self.assertFalse(recovery.inspect(wait_seconds=STARTUP_RECOVERY_WAIT_SECONDS)["blocked"])
        exclusive.assert_called_once_with(STARTUP_RECOVERY_WAIT_SECONDS)
        self.assertEqual(self.events[:3], ["mutex", "stopped", "association"])
        self.assertIs(recovery.locks, self.locks)
        self.assertLess(self.events.index("release association"), self.events.index("release mutex"))

    def test_no_journal_cleanup_busy_is_diagnostic_and_preserves_known_block(self):
        """A busy manager-only sweep changes no file and clears no known editor recovery block."""
        operations = OperationManager(OperationStore(self.paths.operations))
        self.addCleanup(operations.shutdown, 2)
        operations.block_for_recovery("known pending editor recovery", owner=EDITOR_KIND)
        before = snapshot(Path(self.temp.name))
        recovery = self.recovery()
        with patch.object(self.ports, "exclusive", side_effect=ServerFolderBusy()):
            result = MissionMapRecoveryCoordinator(recovery, operations).inspect_mission_map_recovery({})
        self.assertFalse(result["blocked"])
        self.assertTrue(result["cleanup_deferred"])
        self.assertEqual(operations.recovery_block_pair(), ("known pending editor recovery", EDITOR_KIND))
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_no_journal_rereads_after_writer_and_keeps_a_new_journal(self):
        """A commit that became visible while cleanup waited requires later guarded recovery."""
        journal, updates, _candidates = self.candidates()
        recovery = self.recovery()
        after_commit_stage = {}

        @contextmanager
        def racing_writer(_wait):
            """Make the commit's durable manager state visible at writer acquisition."""
            records = CommitRecords(self.paths, self.repository, lambda _phase, _index: None)
            records.stage(journal, updates)
            self.repository.save(journal)
            after_commit_stage.update(snapshot(Path(self.temp.name)))
            yield

        recovery.folder_writer = SimpleNamespace(exclusive=racing_writer)
        self.events.clear()
        self.assertTrue(recovery.inspect()["blocked"])
        self.assertEqual(snapshot(Path(self.temp.name)), after_commit_stage)
        self.assertEqual(self.events, [])

    def test_no_journal_reread_failure_blocks_without_cleanup(self):
        """An unreadable journal set after writer acquisition cannot be swept as orphan storage."""
        recovery = self.recovery()
        before = snapshot(Path(self.temp.name))
        with patch.object(recovery.journals, "records", side_effect=[(), OSError("unavailable")]):
            self.assertTrue(recovery.inspect()["blocked"])
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_unreadable_journal_blocks_before_settings_guard_or_writes(self):
        """Unreadable active journals are retained and prevent even orphan cleanup."""
        path = self.repository.root / "broken.json"
        path.write_bytes(b"not JSON")
        self.events.clear()
        recovery = self.recovery()
        recovery.settings = SimpleNamespace(load=lambda: self.fail("settings read"))
        before = snapshot(Path(self.temp.name))
        self.assertTrue(recovery.inspect()["blocked"])
        self.assertEqual(self.events, [])
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_journal_set_read_failure_blocks_without_cleanup(self):
        """A failed directory read cannot turn pending recovery into orphan cleanup."""
        recovery = self.recovery()
        before = snapshot(Path(self.temp.name))
        with patch.object(recovery.journals, "records", side_effect=OSError("unavailable")):
            self.assertTrue(recovery.recovery_pending())
            self.assertTrue(recovery.inspect()["blocked"])
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_unsafe_journal_paths_block_before_target_reads_or_writes(self):
        """The caller validates paths before the touched-target proof check."""
        journal = self.crash_at("AFTER_PUBLISHED")
        journal.groups[0].target_path = str(self.paths.root / "unrelated.xml")
        self.repository.save(journal)
        before = snapshot(Path(self.temp.name))
        self.assertTrue(self.recovery().inspect()["blocked"])
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_observer_reports_editor_kind_without_changing_the_tree(self):
        """Observers neither clean invalid journals nor run editor recovery."""
        (self.repository.root / "broken.json").write_bytes(b"not JSON")
        before = snapshot(Path(self.temp.name))
        self.assertIn(EDITOR_KIND, pending_recoveries(self.paths))
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_live_lane_defers_even_when_no_journal_is_visible(self):
        """A live commit may have staged manager records before writing its journal."""
        operations = SimpleNamespace(is_drained=lambda: False)
        recovery = self.recovery()
        with patch.object(recovery, "inspect", side_effect=AssertionError("must defer")):
            result = MissionMapRecoveryCoordinator(recovery, operations).inspect_mission_map_recovery({})
        self.assertEqual(result, {"blocked": True, "deferred": True, "diagnostics": []})

    def test_on_demand_clears_only_editor_owned_blocks(self):
        """A successful recovery retains an unrelated restore block."""
        operations = OperationManager(OperationStore(self.paths.operations))
        self.addCleanup(operations.shutdown, 2)
        operations.block_for_recovery("restore still pending", owner="RESTORE_BACKUP")
        operations.block_for_recovery("editor pending", owner=EDITOR_KIND)
        result = MissionMapRecoveryCoordinator(self.recovery(), operations).inspect_mission_map_recovery({})
        self.assertFalse(result["blocked"])
        self.assertEqual(operations.recovery_block_pair(), ("restore still pending", "RESTORE_BACKUP"))
