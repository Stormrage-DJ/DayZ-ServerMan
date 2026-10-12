"""Cover untouched READY groups, commit-point safety and raising recovery hooks."""

from __future__ import annotations

from pathlib import Path

from dayz_serverman.repositories.journaled_publication import JournaledPublication
from dayz_serverman.repositories.mission_map_journal import MissionMapJournalError
from dayz_serverman.repositories.mission_map_publication import EDITOR_POLICY, MissionMapPublicationError
from dayz_serverman.repositories.publication_contracts import RecoveryHooks
from dayz_serverman.repositories.restore_storage import _RESTORE_POLICY
from tests.test_mission_map_journal import OLD
from tests.test_mission_map_journal_paths import snapshot
from tests.test_mission_map_publication import Hooks, PublicationFixture


class UntouchedGroupTests(PublicationFixture):
    """An edit at a later READY group preserves that edit and rolls back preceding groups."""

    def refusal(self):
        """Publish two groups and edit the third immediately before its READY recheck."""
        journal = self.prepared()
        target = Path(journal.groups[2].target_path)

        def edit(phase, index):
            """Create the third target as an external edit before its recheck."""
            if (phase, index) == ("BEFORE_PUBLISH", 2):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"admin edit")

        with self.assertRaises(MissionMapPublicationError) as caught:
            self.publication(edit).publish_groups(journal)
        self.assertEqual(caught.exception.code, "TARGET_CHANGED")
        stored = self.repository.load(self.repository.path_for(journal.operation_id))
        self.assertEqual([group.state for group in stored.groups], ["PUBLISHED", "PUBLISHED", "READY"])
        return journal, target

    def assert_preserved(self, target):
        """Prove earlier groups old and the untouched group's external content unchanged."""
        self.assertEqual((self.mission / "areaflags.map").read_bytes(), OLD["areaflags.map"])
        self.assertEqual((self.mission / "mapgrouppos.xml").read_bytes(), OLD["mapgrouppos.xml"])
        self.assertEqual(target.read_bytes(), b"admin edit")

    def test_later_refusal_compensates_and_retires_without_block(self):
        """Explicit compensation succeeds and inspect accepts the READY suffix of ROLLED_BACK."""
        journal, target = self.refusal()
        publication = self.publication()
        self.assertTrue(publication.compensate(journal))
        self.assertEqual(journal.phase, "ROLLED_BACK")
        self.assertEqual(journal.groups[2].state, "READY")
        self.assertFalse(publication.inspect(self.dayz, Hooks().bound())["blocked"])
        self.assert_preserved(target)
        self.assertFalse((publication.recovery_root / journal.operation_id).exists())
        self.assertTrue(all(not Path(group.staging_path).exists() for group in journal.groups))

    def test_crash_before_compensation_recovers_without_block(self):
        """Startup from the stored refusal compensates earlier groups while preserving the edited file."""
        journal, target = self.refusal()
        self.assertFalse(self.publication().inspect(self.dayz, Hooks().bound())["blocked"])
        self.assert_preserved(target)
        self.assertEqual(self.repository.load(self.repository.retired_path_for(journal.operation_id)).result, "ROLLED_BACK")

    def test_publishing_neither_digest_still_blocks(self):
        """A target edited after the durable PUBLISHING save cannot be proven untouched."""
        journal = self.prepared()
        journal.phase, journal.publication_started = "PUBLISHING", True
        journal.groups[0].state = "PUBLISHING"
        self.repository.save(journal)
        Path(journal.groups[0].target_path).write_bytes(b"neither digest")
        self.assertTrue(self.publication().inspect(self.dayz, Hooks().bound())["blocked"])
        self.assertEqual(Path(journal.groups[0].target_path).read_bytes(), b"neither digest")

    def test_restore_ready_group_retains_old_block_behavior(self):
        """Restore's default policy never treats a READY group as proven untouched."""
        journal = self.prepared()
        Path(journal.groups[0].target_path).write_bytes(b"neither digest")
        journal.publication_started = True
        self.assertFalse(_RESTORE_POLICY.ready_groups_untouched)
        engine = JournaledPublication(lambda _phase, _index: None, _RESTORE_POLICY)
        self.assertFalse(engine.compensate(journal, self.repository))


class CommitRecoverySafetyTests(PublicationFixture):
    """COMMITTING never compensates, and declared hook failures become a recovery block."""

    def test_bare_committing_engine_blocks_without_a_write(self):
        """A missing record hook leaves the whole tree unchanged at the commit point."""
        journal = self.committing()
        before = snapshot(Path(self.temp.name))
        engine = JournaledPublication(lambda _phase, _index: None, EDITOR_POLICY)
        report = engine.inspect(self.repository, self.publication().recovery_root, lambda _journal: True)
        self.assertTrue(report["blocked"])
        self.assertEqual(snapshot(Path(self.temp.name)), before)
        self.assertEqual(self.repository.load(self.repository.path_for(journal.operation_id)).phase, "COMMITTING")

    def test_each_declared_forward_hook_error_blocks_without_a_write(self):
        """Storage, publication and journal errors keep COMMITTING and its files unchanged."""
        journal = self.committing()
        before = snapshot(Path(self.temp.name))
        for error in (OSError("hook failed"), MissionMapPublicationError("FAIL", "hook failed"), MissionMapJournalError("hook failed")):
            def raised(_journal):
                """Raise the declared failure from the caller's roll-forward hook."""
                raise error
            with self.subTest(error=type(error).__name__):
                self.assertTrue(self.publication().inspect(self.dayz, RecoveryHooks(raised, lambda _journal: True))["blocked"])
                self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_raising_rollback_hook_blocks_prepared_and_compensation_branches(self):
        """The rollback-hook branches catch their declared storage failure and keep the journal."""
        journal = self.prepared()
        def raised(_journal):
            """Fail the caller's record rollback."""
            raise OSError("rollback failed")
        hooks = RecoveryHooks(lambda _journal: True, raised)
        self.assertTrue(self.publication().inspect(self.dayz, hooks)["blocked"])
        self.assertTrue(self.repository.path_for(journal.operation_id).exists())
        # Resume from an interrupted publication using the same persisted journal
        journal.phase, journal.resolved, journal.result = "PUBLISHING", False, None
        journal.publication_started = True
        self.repository.save(journal)
        self.assertTrue(self.publication().inspect(self.dayz, hooks)["blocked"])
