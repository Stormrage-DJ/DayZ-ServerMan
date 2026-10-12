"""Inject failures and crashes at the journal, group and record boundaries of D4."""

from __future__ import annotations

from pathlib import Path

from dayz_serverman.application.folder_writer_scope import WriterScope
from dayz_serverman.repositories.mission_map_layout import original_folder
from dayz_serverman.repositories.mission_map_publication import MissionMapPublication, MissionMapPublicationError
from tests.test_mission_map_commit_fixture import CommitFixture
from tests.test_mission_map_records import VANILLA, OUTPUT


# Every precommit hook boundary, including each independently staged manager record
PRECOMMIT = [("AFTER_STAGE_RECORD", index) for index in range(4)] + [
    ("AFTER_STAGE_RECORDS", -1), ("AFTER_PREPARED", -1), ("AFTER_STAGE_GROUP", 0),
    ("BEFORE_PUBLISH", 0), ("AFTER_PUBLISH", 0), ("AFTER_PUBLISHED", -1),
]
# Each boundary at or beyond the durable commit point must roll forward
POSTCOMMIT = [("AFTER_COMMITTING", -1)] + [("AFTER_PUBLISH_RECORD", index) for index in range(4)] + [("AFTER_COMMITTED", -1)]


class CommitFaultTests(CommitFixture):
    """The durable journal determines rollback versus roll-forward after a failure."""

    def isolated(self):
        """Create a disposable fixture for a phase-boundary subtest."""
        case = CommitFixture(methodName="runTest")
        case.setUp()
        self.addCleanup(case.tearDown)
        return case

    def test_caught_failure_before_commit_point_creates_no_original(self):
        """Every precommit storage failure returns to vanilla and removes staged original evidence."""
        for boundary in PRECOMMIT:
            with self.subTest(boundary=boundary):
                case = self.isolated()
                journal, updates, candidates = case.candidates()
                def failure(phase, index):
                    """Raise a recoverable storage error at this phase boundary."""
                    if (phase, index) == boundary:
                        raise OSError("phase fault")
                with self.assertRaises(OSError):
                    case.run_commit(journal, updates, candidates, case.service(failure))
                self.assertEqual((case.mission / "mapgroupproto.xml").read_bytes(), VANILLA)
                self.assertFalse((original_folder(case.paths.mission_map, journal.mission_key) / "original.json").exists())
                self.assertFalse(list(case.paths.root.rglob("*.staged-*")))
                self.assertFalse(list(case.paths.mission_map.rglob("files/*")))
                if case.repository.retired_path_for(journal.operation_id).exists():
                    self.assertEqual(case.repository.load(case.repository.retired_path_for(journal.operation_id)).result, "ROLLED_BACK")

    def test_crash_after_prepared_and_before_commit_point_recovers_rollback(self):
        """Restart compensates every durable precommit crash while the original stays unpublished."""
        for boundary in PRECOMMIT[5:]:
            with self.subTest(boundary=boundary):
                case = self.isolated()
                journal, updates, candidates = case.candidates()
                def crash(phase, index):
                    """Stop execution without the runtime failure handler."""
                    if (phase, index) == boundary:
                        raise KeyboardInterrupt("phase crash")
                with self.assertRaises(KeyboardInterrupt):
                    case.run_commit(journal, updates, candidates, case.service(crash))
                writer = WriterScope(None)
                with writer.held_for():
                    publication = MissionMapPublication(case.paths, case.repository, writer)
                    self.assertFalse(publication.inspect(case.dayz, case.record_hooks())["blocked"])
                self.assertEqual((case.mission / "mapgroupproto.xml").read_bytes(), VANILLA)
                self.assertFalse((original_folder(case.paths.mission_map, journal.mission_key) / "original.json").exists())
                self.assertFalse(list(case.paths.root.rglob("*.staged-*")))

    def test_crash_at_and_after_commit_point_recovers_forward(self):
        """Records finish exactly once, including a replace whose PUBLISHED save never ran."""
        for boundary in POSTCOMMIT:
            with self.subTest(boundary=boundary):
                case = self.isolated()
                journal, updates, candidates = case.candidates()
                def crash(phase, index):
                    """Stop execution past the durable commit point."""
                    if (phase, index) == boundary:
                        raise KeyboardInterrupt("phase crash")
                with self.assertRaises(KeyboardInterrupt):
                    case.run_commit(journal, updates, candidates, case.service(crash))
                writer = WriterScope(None)
                with writer.held_for():
                    publication = MissionMapPublication(case.paths, case.repository, writer)
                    self.assertFalse(publication.inspect(case.dayz, case.record_hooks())["blocked"])
                self.assertEqual((case.mission / "mapgroupproto.xml").read_bytes(), OUTPUT)
                self.assertTrue(all((case.paths.root / update.target_path).is_file() for update in updates))
                retired = case.repository.load(case.repository.retired_path_for(journal.operation_id))
                self.assertEqual(retired.result, "COMMITTED")
                self.assertFalse(list(case.paths.root.rglob("*.staged-*")))

    def test_one_shot_storage_failure_past_commit_point_finishes_committed(self):
        """Successful synchronous recovery reports COMMITTED after the irreversible journal transition."""
        journal, updates, candidates = self.candidates()
        failed = False
        def failure(phase, _index):
            """Fail once after COMMITTING, so the synchronous recovery can finish."""
            nonlocal failed
            if phase == "AFTER_COMMITTING" and not failed:
                failed = True
                raise OSError("one-shot failure")
        self.assertEqual(self.run_commit(journal, updates, candidates, self.service(failure))["result"], "COMMITTED")

    def test_missing_record_stage_after_commit_point_blocks_without_rollback(self):
        """A lost record stage keeps committed mission output and the active COMMITTING journal."""
        journal, updates, candidates = self.candidates()
        def missing(phase, _index):
            """Remove the first stage after its journal becomes the commit point."""
            if phase == "AFTER_COMMITTING":
                (self.paths.root / journal.records[0].staging_path).unlink()
        with self.assertRaises(MissionMapPublicationError) as caught:
            self.run_commit(journal, updates, candidates, self.service(missing))
        self.assertTrue(caught.exception.recovery_required)
        self.assertEqual((self.mission / "mapgroupproto.xml").read_bytes(), OUTPUT)
        self.assertEqual(self.repository.load(self.repository.path_for(journal.operation_id)).phase, "COMMITTING")

    def test_late_target_change_reports_retryable_conflict_and_preserves_edit(self):
        """Runtime TARGET_CHANGED is translated to the accepted readable preview conflict."""
        journal, updates, candidates = self.candidates()
        def edit(phase, _index):
            """Change a target after staging and before the READY recheck."""
            if phase == "BEFORE_PUBLISH":
                Path(journal.groups[0].target_path).write_bytes(b"admin edit")
        with self.assertRaises(MissionMapPublicationError) as caught:
            self.run_commit(journal, updates, candidates, self.service(edit))
        self.assertEqual(caught.exception.code, "REVISION_CONFLICT")
        self.assertEqual(caught.exception.details, {"reason": "target_changed"})
        self.assertTrue(caught.exception.retryable)
        self.assertIn("mapgroupproto.xml", str(caught.exception))
        self.assertEqual(Path(journal.groups[0].target_path).read_bytes(), b"admin edit")
        self.assertEqual(self.repository.records(), ())

    def test_target_changed_before_staging_rolls_back_prepared_without_a_block(self):
        """PREPARED proves no publication; a staging recheck refusal preserves the edit and discards records."""
        journal, updates, candidates = self.candidates()
        def edit(phase, _index):
            """Change the target after PREPARED, before any recovery or candidate staging byte."""
            if phase == "AFTER_PREPARED":
                Path(journal.groups[0].target_path).write_bytes(b"admin edit")
        with self.assertRaises(MissionMapPublicationError) as caught:
            self.run_commit(journal, updates, candidates, self.service(edit))
        self.assertEqual(caught.exception.code, "REVISION_CONFLICT")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(Path(journal.groups[0].target_path).read_bytes(), b"admin edit")
        self.assertEqual(self.repository.records(), ())
        self.assertTrue(all(not (self.paths.root / update.target_path).exists() for update in updates))
