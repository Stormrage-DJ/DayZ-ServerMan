"""Crash an adoption-shaped commit at each fault point, clean orphans and check the shared lookup (10.34, D4)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

from dayz_serverman.application.mission_map_locks import AssociationLocks
from dayz_serverman.repositories.mission_map_commit_records import CommitRecords
from dayz_serverman.repositories.mission_map_journal_store import MissionMapJournalRepository, journal_folder
from dayz_serverman.repositories.mission_map_layout import (
    association_folder, association_profiles, history_name, profile_folder,
)
from dayz_serverman.repositories.mission_map_publication import MissionMapPublication
from dayz_serverman.repositories.mission_map_recovery_cleanup import cleanup_orphans
from dayz_serverman.repositories.publication_contracts import RecoveryHooks
from tests.test_mission_map_retired import MOMENT, OLD, OPERATION, AdoptionFixture


# The A13 writer side as a test holds it
HELD = SimpleNamespace(held=True)


class Crash(Exception):
    """A simulated process stop at one fault point."""


class CrashTests(AdoptionFixture):
    """Every fault point of stage, publication and roll-forward, with recovery as at the next start."""

    def drive(self, fault: Callable[[str, int], None]) -> None:
        """Run the D4 commit order of a records-only journal, as the adoption lane of 10.35 runs it."""
        repository = MissionMapJournalRepository(journal_folder(self.paths), retire_hook=lambda phase: fault(phase, -1))
        journal = self.new_journal()
        records = CommitRecords(self.paths, repository, fault)
        publication = MissionMapPublication(self.paths, repository, HELD, fault_hook=fault)
        records.stage(journal, self.updates(journal))
        repository.save(journal)
        fault("AFTER_PREPARED", -1)
        publication.publish_groups(journal)
        publication.advance(journal)
        fault("AFTER_PUBLISHED", -1)
        publication.advance(journal)
        fault("AFTER_COMMITTING", -1)
        self.assertTrue(records.roll_forward(journal))
        publication.advance(journal)
        fault("AFTER_COMMITTED", -1)
        publication.cleanup(journal)
        self.assertTrue(records.rollback(journal))
        publication.retire(journal)

    def recover(self) -> dict[str, object]:
        """Resolve the journals by the D4 recovery table with the record hooks."""
        records = CommitRecords(self.paths, self.repository, lambda _phase, _index: None)
        return MissionMapPublication(self.paths, self.repository, HELD).inspect(
            self.dayz, RecoveryHooks(records.roll_forward, records.rollback))

    def reset(self) -> None:
        """Rebuild the start state: the same records, no journal."""
        shutil.rmtree(self.area)
        shutil.rmtree(journal_folder(self.paths))
        journal_folder(self.paths).mkdir(parents=True)
        self.build()

    def test_crash_at_each_point_gives_all_or_nothing(self) -> None:
        """Before COMMITTING the start state returns; at or after it the committed state results."""
        before = self.tree()
        points: list[tuple[str, int]] = []
        self.drive(lambda phase, index: points.append((phase, index)))
        after = self.tree()
        self.assertNotEqual(after, before)
        self.assertIn(("AFTER_RENAME_FOLDER", 1), points)
        self.assertIn(("AFTER_RENAME_FOLDER", 2), points)
        results = {"before": 0, "after": 0}
        for point in points:
            with self.subTest(point=point):
                self.reset()

                def fault(phase: str, index: int, point: tuple[str, int] = point) -> None:
                    if (phase, index) == point:
                        raise Crash(phase)
                with self.assertRaises(Crash):
                    self.drive(fault)
                # The durable phase at the stop decides the side; a retired journal had committed
                active = self.repository.path_for(OPERATION)
                durable = self.repository.load(active).phase if active.exists() else None
                retired = self.repository.retired_path_for(OPERATION).exists()
                expected = "after" if durable in ("COMMITTING", "COMMITTED") or retired else "before"
                report = self.recover()
                self.assertFalse(report["blocked"], report)
                self.assertEqual(self.repository.records(), ())
                self.assertEqual(self.tree(), after if expected == "after" else before)
                results[expected] += 1
        # Both sides of the commit point were crashed at least twice
        self.assertGreaterEqual(min(results.values()), 2, results)

    def test_history_folder_stays_after_recovery_of_a_committed_journal(self) -> None:
        """A crash after the last record publishes rolls forward and keeps the history folder."""
        def fault(phase: str, index: int) -> None:
            if phase == "AFTER_PUBLISH_RECORD" and index == 3:
                raise Crash(phase)
        with self.assertRaises(Crash):
            self.drive(fault)
        self.assertFalse(self.recover()["blocked"])
        self.assertTrue((profile_folder(self.area, "main") / history_name(self.key, MOMENT)).is_dir())


class OrphanCleanupTests(AdoptionFixture):
    """Without a journal, the orphan cleanup removes plan and marker staging and keeps the history folder."""

    def test_orphan_staging_of_the_new_kinds(self) -> None:
        """A stop before the first journal save leaves only staging that no journal names."""
        history = profile_folder(self.area, "main") / history_name(self.key, MOMENT)
        history.mkdir()
        old = association_folder(self.area, "main", OLD)
        for folder, name in ((self.active, "plan"), (old, "retired"), (history, "plan")):
            (folder / f"{name}.json.staged-op-lost").write_text("{}", encoding="utf-8")
        cleanup_orphans(self.paths, AssociationLocks())
        self.assertFalse((self.active / "plan.json.staged-op-lost").exists())
        self.assertFalse((old / "retired.json.staged-op-lost").exists())
        self.assertTrue((history / "plan.json.staged-op-lost").exists())


class LayoutProfilesTests(unittest.TestCase):
    """The one association_profiles copy gives the answers of the removed records copy (Architect 16:49:30 (e))."""

    def test_answers(self) -> None:
        """A key that is not a target key, a missing area and a history folder give no profile."""
        with tempfile.TemporaryDirectory(prefix="serverman_profiles_") as name:
            area = Path(name)
            self.assertEqual(association_profiles(area, "a" * 32), ())
            (area / "profiles" / "main" / ("a" * 32)).mkdir(parents=True)
            (area / "profiles" / "test" / ("a" * 32 + ".history-20261016T120000Z")).mkdir(parents=True)
            (area / "profiles" / "file").write_text("x", encoding="utf-8")
            for key, expected in (("a" * 32, ("main",)), ("b" * 32, ()), ("../x", ()), ("A" * 32, ()), ("", ())):
                with self.subTest(key=key):
                    self.assertEqual(association_profiles(area, key), expected)


if __name__ == "__main__":
    unittest.main()
