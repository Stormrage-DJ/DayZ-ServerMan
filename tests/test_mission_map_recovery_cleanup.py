"""D4 no-journal cleanup stays in manager storage and preserves plan evidence and referenced content."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from dayz_serverman.repositories.mission_map_contents import ContentStore
from dayz_serverman.repositories.mission_map_layout import association_folder
from tests.test_mission_map_journal_paths import snapshot
from tests.test_mission_map_recovery_phases import Crash, RecoveryFixture
from tests.test_mission_map_records import VANILLA, sha


class OrphanCleanupTests(RecoveryFixture):
    """No active journal permits only manager cleanup; recovery never invents original records."""

    def test_no_journal_removes_orphan_records_content_and_preserves_plan_evidence(self):
        """An interrupted stage before PREPARED is orphaned; plan leftovers become dated evidence."""
        journal, updates, candidates = self.candidates()

        def fault(phase, _index):
            """Simulate loss after manager staging, before any durable journal or DayZ staging."""
            if phase == "AFTER_STAGE_RECORDS":
                raise Crash()

        with self.assertRaises(Crash):
            self.run_commit(journal, updates, candidates, self.service(fault))
        folder = association_folder(self.paths.mission_map, journal.profile_id, journal.mission_key)
        plan_stage = folder / ".plan.json.a12.tmp"
        plan_stage.write_bytes(b"interrupted plan")
        outside = self.dayz / "orphan.json.staged-operation"
        outside.write_bytes(b"other owner's file")
        before = snapshot(self.dayz)
        self.assertFalse(self.recovery().inspect()["blocked"])
        self.assertEqual(snapshot(self.dayz), before)
        self.assertFalse(any(self.paths.mission_map.rglob("*.staged-*")))
        self.assertFalse(any(self.paths.mission_map.rglob("files/*")))
        evidence = list(folder.glob("plan.interrupted-*.json"))
        self.assertEqual([path.read_bytes() for path in evidence], [b"interrupted plan"])
        self.assertFalse(any(self.paths.mission_map.rglob("original.json")))

    def test_cleanup_preserves_unknown_staging_names(self):
        """Only baseline and applied commit staging belongs to an association folder."""
        folder = association_folder(self.paths.mission_map, "main", self.mission_key())
        folder.mkdir(parents=True)
        unknown = folder / "plan.json.staged-operation"
        unknown.write_bytes(b"other record")
        self.assertFalse(self.recovery().inspect()["blocked"])
        self.assertEqual(unknown.read_bytes(), b"other record")

    def test_referenced_content_survives_while_unused_content_is_swept(self):
        """Protected original and baseline content remains after a complete commit."""
        journal, updates, candidates = self.candidates()
        self.run_commit(journal, updates, candidates)
        folder = association_folder(self.paths.mission_map, journal.profile_id, journal.mission_key)
        unused = ContentStore(folder).write(b"unused")
        self.assertFalse(self.recovery().inspect()["blocked"])
        self.assertEqual(ContentStore(folder).read(sha(VANILLA)), VANILLA)
        self.assertFalse((folder / "files" / unused).exists())

    def test_unresolved_journal_preserves_orphan_content_and_commit_stages(self):
        """A still unresolved COMMITTING journal prevents broad manager cleanup."""
        journal = self.crash_at("AFTER_COMMITTING")
        (self.paths.root / journal.records[0].staging_path).unlink()
        folder = association_folder(self.paths.mission_map, journal.profile_id, journal.mission_key)
        ContentStore(folder).write(b"must remain")
        before = snapshot(Path(self.temp.name))
        self.assertTrue(self.recovery().inspect()["blocked"])
        self.assertEqual(snapshot(Path(self.temp.name)), before)

    def test_missing_area_has_no_side_effect_and_reads_no_settings(self):
        """A fresh manager without editor records needs no installation check."""
        recovery = self.recovery()
        before = snapshot(Path(self.temp.name))
        with patch.object(recovery.settings, "load", side_effect=AssertionError("unused settings")):
            self.assertFalse(recovery.inspect()["blocked"])
        self.assertEqual(snapshot(Path(self.temp.name)), before)
