"""D4 recovery rows with real durable journal and manager-record candidates."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dayz_serverman.application.installation_guard import InstallationGuard
from dayz_serverman.application.mission_map_recovery import MissionMapRecovery
from dayz_serverman.repositories.mission_map_commit_records import CommitRecords
from tests.test_mission_map_commit_fixture import CommitFixture
from tests.test_mission_map_journal_paths import snapshot
from tests.test_mission_map_records import VANILLA, OUTPUT


class Crash(BaseException):
    """Simulate process loss without the live commit's exception compensation."""


class RecoveryFixture(CommitFixture):
    """Use real commit records and shared locks over disposable installation and manager roots."""

    def recovery(self):
        """Bind the startup recovery to the fixture's stopped installation and shared lock set."""
        settings = SimpleNamespace(load=lambda: SimpleNamespace(dayz_root=str(self.dayz)))
        return MissionMapRecovery(self.paths, settings, InstallationGuard(self.ports, self.ports), self.ports, self.locks)

    def crash_at(self, phase):
        """Leave the durable transaction at the requested crash boundary."""
        journal, updates, candidates = self.candidates()

        def fault(found, _index):
            """Raise process loss only at the chosen boundary."""
            if found == phase:
                raise Crash()

        with self.assertRaises(Crash):
            self.run_commit(journal, updates, candidates, self.service(fault))
        return self.repository.load(self.repository.path_for(journal.operation_id))

    def assert_finished(self, journal, result):
        """Check terminal retirement, target bytes and the absence of transaction staging."""
        self.assertEqual(self.repository.records(), ())
        retired = self.repository.load(self.repository.retired_path_for(journal.operation_id))
        self.assertEqual(retired.result, result)
        self.assertEqual((self.mission / "mapgroupproto.xml").read_bytes(), OUTPUT if result == "COMMITTED" else VANILLA)
        self.assertFalse(any(self.paths.mission_map.rglob("*.staged-*")))
        self.assertFalse(Path(journal.groups[0].staging_path).exists())


class RecoveryPhaseTests(RecoveryFixture):
    """Each D4 row either retires with its result or retains an unprovable transaction."""

    def test_prepared_discards_records_without_creating_an_original(self):
        """PREPARED removes staging and creates no protected original."""
        journal = self.crash_at("AFTER_PREPARED")
        self.assertFalse(self.recovery().inspect()["blocked"])
        self.assert_finished(journal, "ROLLED_BACK")
        self.assertFalse(any(self.paths.mission_map.rglob("original.json")))

    def test_publishing_compensates_a_replaced_group(self):
        """A crash after replace but before the group save compensates the proven new target."""
        journal = self.crash_at("AFTER_PUBLISH")
        self.assertEqual(journal.phase, "PUBLISHING")
        self.assertFalse(self.recovery().inspect()["blocked"])
        self.assert_finished(journal, "ROLLED_BACK")

    def test_published_compensates_before_the_commit_point(self):
        """PUBLISHED still rolls back the DayZ group and discards staged records."""
        journal = self.crash_at("AFTER_PUBLISHED")
        self.assertFalse(self.recovery().inspect()["blocked"])
        self.assert_finished(journal, "ROLLED_BACK")

    def test_compensating_resumes_in_reverse(self):
        """COMPENSATING resumes the prior state instead of publishing manager records."""
        journal = self.crash_at("AFTER_PUBLISHED")
        journal.phase = "COMPENSATING"
        self.repository.save(journal)
        self.assertFalse(self.recovery().inspect()["blocked"])
        self.assert_finished(journal, "ROLLED_BACK")

    def test_committing_rolls_records_forward(self):
        """All new DayZ groups allow original, baseline, applied and ledger publication."""
        journal = self.crash_at("AFTER_COMMITTING")
        self.assertFalse(self.recovery().inspect()["blocked"])
        self.assert_finished(journal, "COMMITTED")
        for entry in journal.records:
            self.assertTrue((self.paths.root / entry.target_path).exists())

    def test_committing_equal_manifest_is_idempotent(self):
        """An already replaced record with a READY state counts as published on restart."""
        journal = self.crash_at("AFTER_COMMITTING")
        entry = journal.records[0]
        (self.paths.root / entry.staging_path).replace(self.paths.root / entry.target_path)
        self.assertFalse(self.recovery().inspect()["blocked"])
        self.assert_finished(journal, "COMMITTED")

    def test_committing_old_target_blocks_without_any_write(self):
        """After the commit point an old group cannot trigger compensation."""
        journal = self.crash_at("AFTER_COMMITTING")
        Path(journal.groups[0].target_path).write_bytes(VANILLA)
        before = snapshot(Path(self.temp.name))
        self.assertTrue(self.recovery().inspect()["blocked"])
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_committing_missing_record_blocks(self):
        """A missing original stage and missing published original keep COMMITTING durable."""
        journal = self.crash_at("AFTER_COMMITTING")
        (self.paths.root / journal.records[0].staging_path).unlink()
        before = snapshot(Path(self.temp.name))
        self.assertTrue(self.recovery().inspect()["blocked"])
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_committed_finishes_cleanup_and_retirement(self):
        """A resolved COMMITTED transaction cleans stages and retains every published record."""
        journal = self.crash_at("AFTER_COMMITTED")
        self.assertFalse(self.recovery().inspect()["blocked"])
        self.assert_finished(journal, "COMMITTED")

    def test_rolled_back_finishes_cleanup_and_retirement(self):
        """A terminal rollback still discards staged manager records."""
        journal = self.crash_at("AFTER_PREPARED")
        journal.phase, journal.resolved, journal.result = "ROLLED_BACK", True, "ROLLED_BACK"
        self.repository.save(journal)
        self.assertFalse(self.recovery().inspect()["blocked"])
        self.assert_finished(journal, "ROLLED_BACK")

    def test_touched_third_state_blocks_without_write(self):
        """PUBLISHING cannot prove a target that another writer changed after replacement."""
        journal = self.crash_at("AFTER_PUBLISH")
        Path(journal.groups[0].target_path).write_bytes(b"external edit")
        before = snapshot(Path(self.temp.name))
        self.assertTrue(self.recovery().inspect()["blocked"])
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_ready_external_edit_survives_restart_and_terminal_cleanup(self):
        """A53 leaves READY targets unchanged during rollback and later terminal cleanup."""
        journal = self.crash_at("BEFORE_PUBLISH")
        target = Path(journal.groups[0].target_path)
        target.write_bytes(b"external edit")
        # A failed cleanup hook keeps the resolved journal for the next startup
        with patch.object(CommitRecords, "rollback", return_value=False):
            self.assertTrue(self.recovery().inspect()["blocked"])
        self.assertEqual(self.repository.load(self.repository.path_for(journal.operation_id)).phase, "ROLLED_BACK")
        self.assertFalse(self.recovery().inspect()["blocked"])
        self.assertEqual(target.read_bytes(), b"external edit")
        self.assertEqual(self.repository.records(), ())

    def test_raising_record_hook_keeps_committing(self):
        """A storage exception in roll-forward becomes a block without phase advancement."""
        journal = self.crash_at("AFTER_COMMITTING")
        before = snapshot(Path(self.temp.name))
        with patch.object(CommitRecords, "roll_forward", side_effect=OSError("unavailable")):
            self.assertTrue(self.recovery().inspect()["blocked"])
        self.assertEqual(snapshot(Path(self.temp.name)), before)
        self.assertEqual(self.repository.load(self.repository.path_for(journal.operation_id)).phase, "COMMITTING")
