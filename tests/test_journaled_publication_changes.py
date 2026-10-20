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
        # READY proves the engine never touched this target; rollback preserves the edit
        self.assertEqual(journal.groups[0].state, "READY")
        self.assertTrue(self.publication().compensate(journal))

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


class ConfigurationAdmissionTests(unittest.TestCase):
    """Configuration recovery uses the same stopped and folder-writer admission as Apply."""

    def setUp(self):
        """Reuse the real isolated pair builder and existing lifecycle/mutex doubles."""
        from tests.test_journaled_publication_commit import ConfigurationPairTests
        from tests.test_configuration_workflow import FakeSettings
        from tests.applied_gate_fixtures import CountingLifecycle
        from tests.test_publication_start_guard import _Mutex
        from dayz_serverman.application.installation_guard import InstallationGuard
        from dayz_serverman.application.configuration_recovery import ConfigurationRecovery
        from dayz_serverman.application.operations.manager import OperationManager
        from dayz_serverman.application.operations.store import OperationStore
        ConfigurationPairTests.setUp(self)
        self.publication = ConfigurationPairTests.publisher(self)
        self.pair_publish = lambda: ConfigurationPairTests.publish(self, self.publication)
        self.settings = FakeSettings(self.dayz)
        self.lifecycle, self.mutex = CountingLifecycle(), _Mutex()
        self.operations = OperationManager(OperationStore(self.root / "lane"))
        self.addCleanup(self.operations.shutdown, 2)
        self.writer_events = []
        from contextlib import contextmanager
        @contextmanager
        def exclusive(_wait):
            self.assertTrue(self.mutex.held)
            self.writer_events.append("acquire")
            try:
                yield
            finally:
                self.writer_events.append("release")
        from types import SimpleNamespace
        writer = SimpleNamespace(exclusive=exclusive)
        self.recovery = ConfigurationRecovery(self.publication, self.settings, self.operations,
                                               InstallationGuard(self.lifecycle, self.mutex), writer)

    def interrupt(self):
        """Leave a durable committed-direction pair awaiting its exact successor."""
        from dayz_serverman.repositories.configuration_journal import ConfigurationPublicationError
        def fail(phase, _index):
            if phase == "COMMITTING":
                raise OSError("interrupted")
        self.publication.fault = fail
        with self.assertRaises(ConfigurationPublicationError):
            self.pair_publish()
        self.publication.fault = lambda *_args: None

    def test_startup_refusals_write_nothing_then_recovery_clears_only_own_block(self):
        """Unsafe lifecycle states block launch/mutation; safe recovery preserves other owners."""
        from dayz_serverman.domain.lifecycle import ServerState
        from dayz_serverman.application.configuration_recovery import OWNER
        self.interrupt()
        before = self.profile_path.read_bytes(), self.native.read_bytes()
        for state in (ServerState.RUNNING_MANAGED, ServerState.RUNNING_EXTERNAL, ServerState.UNKNOWN):
            self.lifecycle.state = state
            self.recovery.startup()
            self.assertEqual((self.profile_path.read_bytes(), self.native.read_bytes()), before)
            self.assertEqual(self.operations.recovery_block_pair()[1], OWNER)
            self.assertEqual(self.writer_events, [])
        self.operations.block_for_recovery("other owner", owner="APPLY_MISSION_MAP")
        self.lifecycle.state = ServerState.STOPPED
        self.recovery.startup()
        self.assertEqual(self.writer_events, ["acquire", "release"])
        self.assertEqual(self.operations.recovery_block_pair()[1], "APPLY_MISSION_MAP")
        self.assertEqual(self.repository.load("main").values.game_port, 2502)
        self.assertEqual(self.lifecycle.calls, 0)

    def test_missing_root_and_unknown_bytes_preserve_blocks_and_never_start(self):
        """Only an existing settings repair may pass missing-root admission; corruption stays blocked."""
        from dayz_serverman.application.configuration_recovery import OWNER
        self.interrupt()
        self.settings.value.dayz_root = None
        self.recovery.startup()
        self.assertEqual(self.operations.recovery_block_pair()[1], OWNER)
        self.assertEqual(self.writer_events, [])
        self.assertTrue(self.recovery.require_root_match(self.dayz))
        self.assertFalse(self.recovery.require_root_match(self.root))
        self.settings.value.dayz_root = str(self.dayz)
        self.native.write_bytes(b"unknown external file")
        self.recovery.startup()
        self.assertEqual(self.native.read_bytes(), b"unknown external file")
        self.assertEqual(self.operations.recovery_block_pair()[1], OWNER)
        self.assertEqual(self.lifecycle.calls, 0)

    def test_mutation_holds_writer_and_observer_composition_creates_no_artifacts(self):
        """Admission runs before writes; observer recovery is entirely inert."""
        from types import SimpleNamespace
        from unittest.mock import Mock
        from dayz_serverman.application.configuration_recovery import configure_recovery
        from dayz_serverman.application.installation_guard import InstallationGuard
        with self.recovery.mutation():
            self.assertTrue(self.mutex.held)
            self.assertEqual(self.writer_events, ["acquire"])
        self.assertEqual(self.writer_events, ["acquire", "release"])
        absent = self.root / "observer-operations"
        paths = SimpleNamespace(profiles=self.repository.root, operations=absent)
        service, coordinator = Mock(), Mock()
        configure_recovery(paths, service, coordinator, self.settings, self.operations,
                           InstallationGuard(self.lifecycle, self.mutex), observer=True)
        service.bind_publication.assert_not_called()
        coordinator.bind_admission.assert_not_called()
        self.assertFalse(absent.exists())

    def test_settings_repair_rejects_pending_configuration_root_redirect_without_write(self):
        """The existing repair checks the configuration owner without running recovery."""
        from unittest.mock import Mock
        from dayz_serverman.application.settings_repair import SettingsRepair
        from dayz_serverman.application.settings import SettingsValidationError
        self.interrupt()
        journals, restores = Mock(), Mock()
        journals.records.return_value = []
        restores.journals.records.return_value = []
        repair = SettingsRepair(self.settings, journals, self.root / "backup-recovery", restores,
                                configuration_recovery=self.recovery)
        before = self.profile_path.read_bytes(), self.native.read_bytes()
        with self.assertRaises(SettingsValidationError):
            repair._require_journal_match(self.root)
        repair._require_journal_match(self.dayz)
        self.assertEqual((self.profile_path.read_bytes(), self.native.read_bytes()), before)
        self.assertEqual(self.writer_events, [])
        active = self.publication.store.records()[0][0]
        active.write_bytes(b"malformed journal")
        with self.assertRaises(SettingsValidationError):
            repair._require_journal_match(self.dayz)

    def test_unknown_artifacts_survive_live_hooks_and_startup_without_compensation(self):
        """Stages and recovery copies are proven after hooks before any destructive engine step."""
        from tests.test_journaled_publication_commit import ConfigurationPairTests
        from dayz_serverman.repositories.configuration_journal import ConfigurationPublicationError
        for boundary in ("BEFORE_COMMIT", "BEFORE_PUBLISH", "BEFORE_COMPENSATE", "STARTUP"):
            for artifact in ("native", "profile", "recovery", "extra"):
                with self.subTest(boundary=boundary, artifact=artifact):
                    fixture = ConfigurationPairTests()
                    fixture.setUp()
                    try:
                        publication = fixture.publisher()
                        damaged = []
                        def tamper_artifact():
                            journal = publication.store.records()[0][1]
                            paths = {"native": journal.groups[0].staging_path,
                                     "profile": journal.profile_stage, "recovery": journal.groups[0].recovery_path,
                                     "extra": str(Path(journal.groups[0].recovery_path).with_name("unknown.extra"))}
                            path = Path(paths[artifact])
                            path.write_bytes(b"unknown artifact bytes")
                            damaged.append(path)
                        def hook(phase, _index):
                            if boundary == "STARTUP" and phase == "COMMITTING":
                                raise OSError("interrupt for startup")
                            if boundary == "BEFORE_COMPENSATE" and phase == "BEFORE_COMMIT":
                                raise OSError("force compensation")
                            if phase == boundary:
                                tamper_artifact()
                        publication.fault = hook
                        with self.assertRaises(ConfigurationPublicationError) as caught:
                            fixture.publish(publication)
                        self.assertTrue(caught.exception.recovery_required)
                        if boundary == "STARTUP":
                            tamper_artifact()
                        before = fixture.native.read_bytes(), fixture.profile_path.read_bytes()
                        self.assertTrue(publication.inspect(fixture.dayz)["blocked"])
                        self.assertEqual((fixture.native.read_bytes(), fixture.profile_path.read_bytes()), before)
                        self.assertEqual(damaged[0].read_bytes(), b"unknown artifact bytes")
                        self.assertEqual(len(publication.store.records()), 1)
                    finally:
                        fixture.doCleanups()
