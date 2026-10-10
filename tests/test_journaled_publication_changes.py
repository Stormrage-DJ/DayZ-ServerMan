"""Cover the Architect's engine changes 3 to 5 (evidence 8.1, 16:18:24) and their backup-restore defaults."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import unittest
from pathlib import Path

from dayz_serverman.domain.restores import RestoreJournal
from dayz_serverman.repositories import journaled_publication, publication_contracts, restore_storage
from dayz_serverman.repositories.mission_map_journal import MapJournal
from dayz_serverman.repositories.mission_map_publication import EDITOR_POLICY, MissionMapPublicationError
from dayz_serverman.repositories.restore_storage import RestoreStorage
from tests.test_mission_map_journal import NEW, OLD
from tests.test_mission_map_publication import PublicationFixture


class OldStateRecheckTests(PublicationFixture):
    """Change 3: with the editor policy, each group's old state is checked again just before its replace."""

    def test_a_changed_target_is_never_replaced(self) -> None:
        """A target that changed after staging raises TARGET_CHANGED; its bytes stay and its staged file stays."""
        journal = self.prepared()

        def edit(phase: str, index: int) -> None:
            """Let another writer change the first target after the journal was written."""
            if (phase, index) == ("BEFORE_PUBLISH", 0):
                (self.mission / "areaflags.map").write_bytes(b"external edit")
        with self.assertRaises(MissionMapPublicationError) as caught:
            self.publication(edit).publish_groups(journal)
        self.assertEqual(caught.exception.code, "TARGET_CHANGED")
        self.assertEqual(str(caught.exception), EDITOR_POLICY.changed_message)
        self.assertEqual((self.mission / "areaflags.map").read_bytes(), b"external edit")
        self.assertEqual(Path(journal.groups[0].staging_path).read_bytes(), NEW["areaflags.map"])
        # The target now matches neither state, so compensation refuses and recovery must block
        self.assertFalse(self.publication().compensate(journal))

    def test_backup_restore_keeps_the_recheck_off(self) -> None:
        """The restore policy has no recheck and keeps its verification text byte for byte."""
        policy = restore_storage._RESTORE_POLICY
        self.assertFalse(policy.recheck_old_state)
        self.assertEqual(policy.verify_message, "published restore target failed verification")
        self.assertTrue(EDITOR_POLICY.recheck_old_state)


class AbsentFileTests(PublicationFixture):
    """Change 4: a group can require its file to end absent, and compensation restores it."""

    def test_removal_needs_a_verified_recovery_copy(self) -> None:
        """A missing or wrong recovery copy stops the removal before the target moves."""
        journal = self.prepared()
        removed = journal.groups[1]
        Path(removed.recovery_path or "").write_bytes(b"corrupt copy")
        with self.assertRaises(OSError) as caught:
            self.publication().publish_groups(journal)
        self.assertEqual(str(caught.exception), EDITOR_POLICY.verify_message)
        self.assertEqual(Path(removed.target_path).read_bytes(), OLD["mapgrouppos.xml"])
        self.assertFalse(Path(removed.staging_path).exists())

    def test_removal_moves_the_target_aside_and_compensation_restores_it(self) -> None:
        """The removed target sits at its staging path, counts as new while absent, and comes back on rollback."""
        journal = self.prepared()
        publication = self.publication()
        publication.publish_groups(journal)
        removed = journal.groups[1]
        self.assertFalse(Path(removed.target_path).exists())
        self.assertEqual(Path(removed.staging_path).read_bytes(), OLD["mapgrouppos.xml"])
        self.assertTrue(publication_contracts.in_new_state(removed))
        # The same absent target is not the new state of a group that publishes a file
        self.assertFalse(publication_contracts.in_new_state(
            dataclasses.replace(removed, new_exists=True, new_digest=removed.old_digest)))
        self.assertTrue(publication.compensate(journal))
        self.assertEqual(journal.phase, "ROLLED_BACK")
        self.assert_old_state()


class TypingAndWordingTests(PublicationFixture):
    """Change 5 and change 1: Protocol typing, policy wording and the restore-owned commit wrapper."""

    def test_engine_names_no_restore_journal_type(self) -> None:
        """The engine modules type against Protocols and never name RestoreJournal or RestoreGroup."""
        for module in (journaled_publication, publication_contracts):
            names = {node.id for node in ast.walk(ast.parse(inspect.getsource(module))) if isinstance(node, ast.Name)}
            self.assertFalse({"RestoreJournal", "RestoreGroup"} & names, module.__name__)
        self.assertFalse(issubclass(MapJournal, RestoreJournal))

    def test_only_backup_restore_sets_committed_after_publication(self) -> None:
        """The engine has no committing publish; the restore wrapper ends the phases as before."""
        self.assertFalse(hasattr(journaled_publication.JournaledPublication, "publish"))
        source = inspect.getsource(RestoreStorage._publish)
        self.assertIn("publish_groups", source)
        self.assertIn('"COMMITTED", True, True, "COMMITTED"', source)


if __name__ == "__main__":
    unittest.main()
