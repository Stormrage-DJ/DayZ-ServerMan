"""Cover editor publication through the shared engine: the split commit point, COMMITTING recovery and hooks."""

from __future__ import annotations

import unittest
from collections.abc import Callable
from pathlib import Path

from dayz_serverman.application.folder_writer_scope import WriterScope
from dayz_serverman.repositories.mission_map_journal import MapJournal, MissionMapJournalError
from dayz_serverman.repositories.mission_map_publication import (
    EDITOR_POLICY, MissionMapPublication, MissionMapPublicationError,
)
from dayz_serverman.repositories.publication_contracts import RecoveryHooks
from tests.test_mission_map_journal import NEW, OLD, JournalFixture
from tests.test_mission_map_journal_paths import snapshot


class Hooks:
    """Recording roll-forward and rollback hooks; the roll-forward marks every record published, as 8.5 will."""

    def __init__(self, forward: bool = True, back: bool = True) -> None:
        """Store the results that the hooks return."""
        self.forward, self.back = forward, back
        self.calls: list[tuple[str, str]] = []

    def roll_forward(self, journal: MapJournal) -> bool:
        """Record the call and mark the records published when the hook succeeds."""
        self.calls.append(("forward", journal.phase))
        if self.forward:
            for record in journal.records:
                record.state = "PUBLISHED"
        return self.forward

    def rollback(self, journal: MapJournal) -> bool:
        """Record the call."""
        self.calls.append(("back", journal.phase))
        return self.back

    def bound(self) -> RecoveryHooks[MapJournal]:
        """Return the hooks in the engine form."""
        return RecoveryHooks(self.roll_forward, self.rollback)


class PublicationFixture(JournalFixture):
    """The fixture journal with three groups: a replaced file, a removed file and a new file in a new folder."""

    def setUp(self) -> None:
        """Create the fixture and hold the A13 writer side, as every engine caller must."""
        super().setUp()
        self.scope = WriterScope(None)
        self.scope.acquire()

    def tearDown(self) -> None:
        """Release the writer side and remove the folders."""
        self.scope.release()
        super().tearDown()

    def publication(self, fault: Callable[[str, int], None] | None = None) -> MissionMapPublication:
        """Return the editor publication over the fixture store."""
        return MissionMapPublication(self.paths, self.repository, self.scope, fault_hook=fault)

    def prepared(self) -> MapJournal:
        """Stage the three groups and save the journal in phase PREPARED (D4 steps 3 and 4)."""
        journal = self.journal([
            self.group("mission", "areaflags.map", NEW["areaflags.map"]),
            self.group("mission", "mapgrouppos.xml", None),
            self.group("mission", "env/zombie_territories.xml", NEW["env/zombie_territories.xml"]),
        ])
        self.stage(journal)
        self.repository.save(journal)
        return journal

    def committing(self) -> MapJournal:
        """Publish every group and advance to the durable commit point."""
        journal = self.prepared()
        publication = self.publication()
        publication.publish_groups(journal)
        self.assertEqual(publication.advance(journal), "PUBLISHED")
        self.assertEqual(publication.advance(journal), "COMMITTING")
        return journal

    def assert_old_state(self) -> None:
        """Assert that every mission file holds its old state and no created folder remains."""
        for relative, data in OLD.items():
            self.assertEqual((self.mission / relative).read_bytes(), data)
        self.assertFalse((self.mission / "env").exists())

    def assert_new_state(self) -> None:
        """Assert that every group target holds its new state."""
        self.assertEqual((self.mission / "areaflags.map").read_bytes(), NEW["areaflags.map"])
        self.assertFalse((self.mission / "mapgrouppos.xml").exists())
        self.assertEqual((self.mission / "env/zombie_territories.xml").read_bytes(), NEW["env/zombie_territories.xml"])

    def assert_retired(self, journal: MapJournal, result: str) -> None:
        """Assert that the journal is retired with the result and that no staging or recovery file remains."""
        self.assertEqual(self.repository.records(), ())
        retired = self.repository.load(self.repository.retired_path_for(journal.operation_id))
        self.assertEqual(retired.result, result)
        self.assertFalse((self.publication().recovery_root / journal.operation_id).exists())
        self.assertEqual([path.name for path in self.mission.rglob(".*")], [])


class CommitPointTests(PublicationFixture):
    """Change 1: publish_groups leaves the phase sequence to the caller."""

    def test_groups_publish_and_the_caller_ends_the_phases(self) -> None:
        """Groups end PUBLISHED in phase PUBLISHING; COMMITTED needs every record published."""
        journal = self.prepared()
        publication = self.publication()
        publication.publish_groups(journal)
        stored = self.repository.load(self.repository.path_for(journal.operation_id))
        self.assertEqual((stored.phase, [group.state for group in stored.groups]), ("PUBLISHING", ["PUBLISHED"] * 3))
        self.assert_new_state()
        publication.advance(journal)
        publication.advance(journal)
        # The store refuses COMMITTED while a record is unpublished, and the journal keeps its phase
        with self.assertRaises(MissionMapJournalError):
            publication.advance(journal)
        self.assertEqual((journal.phase, journal.resolved, journal.result), ("COMMITTING", False, None))
        for record in journal.records:
            record.state = "PUBLISHED"
        self.repository.save(journal)
        self.assertEqual(publication.advance(journal), "COMMITTED")
        with self.assertRaises(MissionMapPublicationError):
            publication.advance(journal)
        publication.cleanup(journal)
        publication.retire(journal)
        self.assert_retired(journal, "COMMITTED")
        self.assert_new_state()


class RecoveryHookTests(PublicationFixture):
    """Change 2: COMMITTING rolls forward only from a proven new state; rollbacks discard staged records."""

    def test_committing_rolls_forward_from_the_new_state(self) -> None:
        """Every target is new, so the roll-forward hook runs once and the journal retires as COMMITTED."""
        journal = self.committing()
        hooks = Hooks()
        report = self.publication().inspect(self.dayz, hooks.bound())
        self.assertEqual(report, {"blocked": False, "diagnostics": []})
        self.assertEqual(hooks.calls, [("forward", "COMMITTING")])
        self.assert_retired(journal, "COMMITTED")
        self.assert_new_state()

    def test_committing_blocks_without_a_write_when_a_target_is_not_new(self) -> None:
        """A target in its old state, or in neither state, blocks; the hook never runs and nothing changes."""
        journal = self.committing()
        for data in (OLD["areaflags.map"], b"third-party"):
            with self.subTest(data=data):
                (self.mission / "areaflags.map").write_bytes(data)
                before = snapshot(Path(self.temp.name))
                hooks = Hooks()
                report = self.publication().inspect(self.dayz, hooks.bound())
                self.assertTrue(report["blocked"])
                self.assertEqual(report["diagnostics"][0]["message"], EDITOR_POLICY.unresolved_message)
                self.assertEqual(hooks.calls, [])
                self.assertEqual(snapshot(Path(self.temp.name)), before)
        self.assertEqual(self.repository.load(self.repository.path_for(journal.operation_id)).phase, "COMMITTING")

    def test_a_refused_roll_forward_blocks(self) -> None:
        """A roll-forward hook that cannot publish a record leaves the journal at COMMITTING."""
        journal = self.committing()
        report = self.publication().inspect(self.dayz, Hooks(forward=False).bound())
        self.assertTrue(report["blocked"])
        self.assertEqual(self.repository.load(self.repository.path_for(journal.operation_id)).phase, "COMMITTING")

    def test_prepared_journal_rolls_back_and_discards_records(self) -> None:
        """PREPARED: staging files go, the rollback hook runs, and the journal retires as ROLLED_BACK."""
        journal = self.prepared()
        hooks = Hooks()
        self.assertFalse(self.publication().inspect(self.dayz, hooks.bound())["blocked"])
        self.assertEqual(hooks.calls, [("back", "ROLLED_BACK")])
        self.assert_retired(journal, "ROLLED_BACK")
        self.assert_old_state()

    def test_interrupted_publication_compensates_then_discards_records(self) -> None:
        """A crash after the last move compensates in reverse order, also the removed file, then rolls back."""
        journal = self.prepared()

        def crash(phase: str, index: int) -> None:
            """Stop the process after the third group moved in."""
            if (phase, index) == ("AFTER_PUBLISH", 2):
                raise KeyboardInterrupt("simulated crash")
        with self.assertRaises(KeyboardInterrupt):
            self.publication(crash).publish_groups(journal)
        hooks = Hooks()
        self.assertFalse(self.publication().inspect(self.dayz, hooks.bound())["blocked"])
        self.assertEqual(hooks.calls, [("back", "ROLLED_BACK")])
        self.assert_retired(journal, "ROLLED_BACK")
        self.assert_old_state()

    def test_committed_journal_finishes_cleanup_and_retires(self) -> None:
        """A COMMITTED journal that was not retired proves the new state, also the absence, then retires."""
        journal = self.committing()
        for record in journal.records:
            record.state = "PUBLISHED"
        self.repository.save(journal)
        self.publication().advance(journal)
        hooks = Hooks()
        self.assertFalse(self.publication().inspect(self.dayz, hooks.bound())["blocked"])
        self.assertEqual(hooks.calls, [("forward", "COMMITTED")])
        self.assert_retired(journal, "COMMITTED")
        self.assert_new_state()

    def test_rolled_back_journal_finishes_cleanup_and_retires(self) -> None:
        """A compensated journal that was not retired discards its records through the rollback hook."""
        journal = self.prepared()
        publication = self.publication()
        publication.publish_groups(journal)
        self.assertTrue(publication.compensate(journal))
        hooks = Hooks()
        self.assertFalse(publication.inspect(self.dayz, hooks.bound())["blocked"])
        self.assertEqual(hooks.calls, [("back", "ROLLED_BACK")])
        self.assert_retired(journal, "ROLLED_BACK")


class WriterSideTests(PublicationFixture):
    """The Architect condition: every editor engine step runs inside the A13 writer side."""

    def test_every_step_refuses_without_the_writer_side(self) -> None:
        """Without the held writer side each step raises before any write; with it the steps run."""
        journal = self.prepared()
        self.scope.release()
        publication = self.publication()
        before = snapshot(Path(self.temp.name))
        steps = {
            "publish_groups": lambda: publication.publish_groups(journal),
            "advance": lambda: publication.advance(journal),
            "compensate": lambda: publication.compensate(journal),
            "cleanup": lambda: publication.cleanup(journal),
            "retire": lambda: publication.retire(journal),
            "inspect": lambda: publication.inspect(self.dayz, Hooks().bound()),
        }
        for name, step in steps.items():
            with self.subTest(step=name), self.assertRaises(MissionMapPublicationError) as caught:
                step()
            self.assertEqual(caught.exception.code, "WRITER_SIDE_REQUIRED")
        self.assertEqual(snapshot(Path(self.temp.name)), before)
        self.assertEqual(journal.phase, "PREPARED")
        # The same publication runs once the caller holds the writer side
        with self.scope.held_for():
            publication.publish_groups(journal)
        self.assert_new_state()


if __name__ == "__main__":
    unittest.main()
