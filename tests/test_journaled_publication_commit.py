"""Cover untouched READY groups, commit-point safety and raising recovery hooks."""

from __future__ import annotations

from pathlib import Path
import unittest

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


class ConfigurationPairTests(unittest.TestCase):
    """Apply and recover real isolated profile/configuration pairs at durable boundaries."""

    def setUp(self):
        """Create two real repository targets without any live manager data."""
        import tempfile
        from dayz_serverman.repositories.profiles import ProfileRepository
        from tests.test_configuration_workflow import profile
        self.temporary = tempfile.TemporaryDirectory(prefix="configuration_pair_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dayz = self.root / "dayz"
        self.dayz.mkdir()
        self.native = self.dayz / "serverDZ.cfg"
        self.native.write_bytes(b'hostname = "Old";\nsteamQueryPort = 2305;\n')
        self.repository = ProfileRepository(self.root / "profiles")
        self.record = self.repository.save(profile().values, None)
        self.profile_path = self.repository.root / "main.json"
        self.old_profile = self.profile_path.read_bytes()
        self.old_native = self.native.read_bytes()

    def publisher(self, hook=None):
        """Bind the configuration adapter to this fixture's exact manager roots."""
        from dayz_serverman.repositories.configuration_publication import ConfigurationPublication
        return ConfigurationPublication(self.repository.root, self.root / "operations", hook)

    def publish(self, publisher):
        """Request a real mixed change with one successor profile revision."""
        from dayz_serverman.repositories.configuration_common import digest_bytes
        return publisher.publish(self.dayz, self.record, 4, b'hostname = "New";\nsteamQueryPort = 2505;\n',
                                 digest_bytes(self.old_native), 2502)

    def test_faults_before_and_after_commit_choose_the_durable_direction(self):
        """Precommit compensates; postcommit replay finishes once without revision rollback."""
        from dayz_serverman.repositories.configuration_journal import ConfigurationPublicationError
        for boundary in ("READY", "AFTER_PUBLISH", "BEFORE_COMMIT", "COMMITTING", "AFTER_PROFILE", "COMMITTED"):
            with self.subTest(boundary=boundary):
                self.native.write_bytes(self.old_native)
                self.profile_path.write_bytes(self.old_profile)
                def fail(phase, _index):
                    if phase == boundary:
                        raise OSError("injected " + boundary)
                publication = self.publisher(fail)
                with self.assertRaises(ConfigurationPublicationError) as caught:
                    self.publish(publication)
                committed = boundary in ("COMMITTING", "AFTER_PROFILE", "COMMITTED")
                self.assertEqual(caught.exception.recovery_required, committed)
                publication.fault = lambda *_args: None
                self.assertFalse(publication.inspect(self.dayz)["blocked"])
                self.assertFalse(publication.inspect(self.dayz)["blocked"])
                current = self.repository.load("main")
                self.assertEqual(current.values.game_port, 2502 if committed else 2302)
                self.assertEqual(current.revision, self.record.revision + int(committed))
                self.assertEqual(self.native.read_bytes(), b'hostname = "New";\nsteamQueryPort = 2505;\n'
                                 if committed else self.old_native)
                self.assertEqual(list(publication.store.records()), [])

    def test_uncertain_commit_marker_uses_persisted_state_not_local_phase(self):
        """A failed save may have durably crossed the commit point already."""
        from dayz_serverman.repositories.configuration_journal import ConfigurationPublicationError
        for after_write in (False, True):
            with self.subTest(after_write=after_write):
                self.native.write_bytes(self.old_native)
                self.profile_path.write_bytes(self.old_profile)
                publication = self.publisher()
                save = publication.store.save
                def fail(journal):
                    if journal.phase == "COMMITTING":
                        if after_write:
                            save(journal)
                        raise OSError("uncertain commit marker")
                    save(journal)
                publication.store.save = fail
                with self.assertRaises(ConfigurationPublicationError) as caught:
                    self.publish(publication)
                self.assertEqual(caught.exception.recovery_required, after_write)
                publication.store.save = save
                self.assertFalse(publication.inspect(self.dayz)["blocked"])
                self.assertEqual(self.repository.load("main").values.game_port, 2502 if after_write else 2302)

    def test_unknown_bytes_and_changed_roots_block_without_overwriting(self):
        """Recovery refuses unrelated profile/config edits and unsafe target mappings."""
        from dayz_serverman.repositories.configuration_journal import ConfigurationPublicationError
        def stop(phase, _index):
            if phase == "COMMITTING":
                raise OSError("interrupted")
        publication = self.publisher(stop)
        with self.assertRaises(ConfigurationPublicationError):
            self.publish(publication)
        new_native = self.native.read_bytes()
        self.profile_path.write_bytes(b"external profile bytes")
        self.assertTrue(publication.inspect(self.dayz)["blocked"])
        self.assertEqual(self.profile_path.read_bytes(), b"external profile bytes")
        self.profile_path.write_bytes(self.old_profile)
        self.native.write_bytes(b"external configuration bytes")
        self.assertTrue(publication.inspect(self.dayz)["blocked"])
        self.assertEqual(self.native.read_bytes(), b"external configuration bytes")
        self.native.write_bytes(new_native)
        self.assertTrue(publication.inspect(self.root)["blocked"])
        journal = publication.store.records()[0][1]
        journal.groups[0].target_path = str(self.root / "outside.cfg")
        publication.store.save(journal)
        self.assertTrue(publication.inspect(self.dayz)["blocked"])
        self.assertEqual(self.profile_path.read_bytes(), self.old_profile)

    def test_apply_branches_use_one_operation_and_keep_native_profile_ownership(self):
        """Game-only and mixed updates advance once and never emit a virtual assignment."""
        from dayz_serverman.application.configuration import ConfigurationService
        from dayz_serverman.application.profiles import ProfileService
        from tests.test_configuration_workflow import FakeSettings
        settings = FakeSettings(self.dayz)
        service = ConfigurationService(ProfileService(self.repository, settings), settings)
        service.bind_publication(self.publisher())
        for updates in ({"game_port": 2502}, {"game_port": 2602, "steamQueryPort": 2605},
                        {"game_port": 2602}):
            loaded = service.load("main", "server")
            before = self.repository.load("main")
            service.apply("main", "server", before.revision, 4, loaded["digest"], None, updates, lambda *_args: None)
            current = self.repository.load("main")
            self.assertEqual(current.revision, before.revision + int(before.values.game_port != updates["game_port"]))
            self.assertEqual(current.values.game_port, updates["game_port"])
            self.assertNotIn(b"game_port", self.native.read_bytes())

    def test_unknown_profile_stage_survives_precommit_and_postcommit_failure(self):
        """Never delete or publish a successor stage whose bytes are no longer proven."""
        from dayz_serverman.repositories.configuration_journal import ConfigurationPublicationError
        for boundary in ("BEFORE_COMMIT", "COMMITTING"):
            with self.subTest(boundary=boundary):
                publication = self.publisher()
                def tamper(phase, _index):
                    if phase == boundary:
                        journal = publication.store.records()[0][1]
                        Path(journal.profile_stage).write_bytes(b"unknown stage")
                        raise OSError("interrupted with unknown stage")
                publication.fault = tamper
                with self.assertRaises(ConfigurationPublicationError) as caught:
                    self.publish(publication)
                self.assertTrue(caught.exception.recovery_required)
                journal = publication.store.records()[0][1]
                self.assertTrue(publication.inspect(self.dayz)["blocked"])
                self.assertEqual(Path(journal.profile_stage).read_bytes(), b"unknown stage")
                self.assertEqual(self.profile_path.read_bytes(), self.old_profile)
                # Resolve the deliberate fixture damage before exercising the next boundary.
                from dayz_serverman.repositories.configuration_journal import successor_bytes
                Path(journal.profile_stage).write_bytes(successor_bytes(journal.old_profile, journal.new_port))
                self.assertFalse(publication.inspect(self.dayz)["blocked"])
                self.native.write_bytes(self.old_native)
                self.profile_path.write_bytes(self.old_profile)

    def test_native_edits_at_profile_and_commit_hooks_never_report_success(self):
        """Every observable finalization boundary must retain a mismatched pair for recovery."""
        from dayz_serverman.repositories.configuration_journal import ConfigurationPublicationError
        for boundary in ("BEFORE_PROFILE", "AFTER_PROFILE", "COMMITTED"):
            with self.subTest(boundary=boundary):
                fixture = ConfigurationPairTests()
                fixture.setUp()
                try:
                    def tamper(phase, _index):
                        if phase == boundary:
                            fixture.native.write_bytes(b"external native bytes")
                    publication = fixture.publisher(tamper)
                    with self.assertRaises(ConfigurationPublicationError) as caught:
                        fixture.publish(publication)
                    self.assertTrue(caught.exception.recovery_required)
                    self.assertEqual(fixture.native.read_bytes(), b"external native bytes")
                    self.assertEqual(len(publication.store.records()), 1)
                    self.assertTrue(publication.inspect(fixture.dayz)["blocked"])
                    self.assertEqual(fixture.repository.load("main").values.game_port,
                                     2302 if boundary == "BEFORE_PROFILE" else 2502)
                finally:
                    fixture.doCleanups()
