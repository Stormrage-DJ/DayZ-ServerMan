"""Mid-write failures of leaf 8.7: the previous valid state comes back, and partial success is never reported."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from dayz_serverman.application.installation_guard import InstallationGuard
from dayz_serverman.application.mission_map_apply_choices import parse_choices
from dayz_serverman.application.mission_map_recovery import MissionMapRecovery
from dayz_serverman.repositories.mission_map_journal import MissionMapJournalError
from dayz_serverman.repositories.mission_map_publication import MissionMapPublicationError
from tests.mission_map_apply_support import NO_CHOICES, ApplyFixture
from tests.mission_map_mapgroup_support import zone
from tests.test_mission_map_infected import encounter

# One included infected encounter whose event the mission offers
INFECTED = encounter("e1", x=600.0, z=600.0, radius=40.0)
# Boundaries before the commit point of a first Apply: four records, four groups
PRECOMMIT = [("AFTER_REVALIDATE", -1)] + [("AFTER_STAGE_RECORD", index) for index in range(4)] + [
    ("AFTER_STAGE_RECORDS", -1), ("AFTER_PREPARED", -1), ("AFTER_STAGE_GROUP", 3), ("BEFORE_PUBLISH", 0),
    ("AFTER_PUBLISH", 0), ("AFTER_PUBLISH", 2), ("AFTER_PUBLISHED", -1)]
# Boundaries before the commit point of a second Apply: the baseline is unchanged, so two records and two groups
PRECOMMIT_AGAIN = [("AFTER_REVALIDATE", -1), ("AFTER_STAGE_RECORD", 0), ("AFTER_STAGE_RECORD", 1),
                   ("AFTER_STAGE_RECORDS", -1), ("AFTER_PREPARED", -1), ("AFTER_STAGE_GROUP", 1),
                   ("BEFORE_PUBLISH", 1), ("AFTER_PUBLISH", 0), ("AFTER_PUBLISH", 1), ("AFTER_PUBLISHED", -1)]
# Boundaries at and after the commit point
POSTCOMMIT = [("AFTER_COMMITTING", -1), ("AFTER_PUBLISH_RECORD", 0), ("AFTER_PUBLISH_RECORD", 3),
              ("AFTER_COMMITTED", -1)]


class Crash(BaseException):
    """A process end at a boundary: no failure handler runs."""


class ApplyFaultCase(ApplyFixture):
    """One isolated fixture with a saved plan for each boundary."""

    def runTest(self) -> None:
        """Unused: the fixture serves subtests."""

    def mission_files(self) -> dict[str, bytes]:
        """Return the bytes of every file of the verified mission."""
        return {str(path.relative_to(self.mission)): path.read_bytes() for path in self.mission.rglob("*")
                if path.is_file()}


class ApplyFaultTests(unittest.TestCase):
    """A failure before the commit point restores the files and records; after it, the Apply commits."""

    def case(self, *, applied: bool = False) -> ApplyFaultCase:
        """Return a fixture with a saved plan, and with one committed Apply when asked."""
        case = ApplyFaultCase()
        case.setUp()
        self.addCleanup(case.tearDown)
        case.save([zone("rich"), INFECTED], 25)
        if applied:
            case.run_apply()
            case.save([zone("very_rich"), INFECTED], 50, expected=0)
        return case

    def test_failure_before_the_commit_point_restores_the_previous_state(self) -> None:
        """Files, original, baseline and applied record stay as they were; no journal stays active."""
        for applied, boundaries in ((False, PRECOMMIT), (True, PRECOMMIT_AGAIN)):
            for boundary in boundaries:
                with self.subTest(applied=applied, boundary=boundary):
                    case = self.case(applied=applied)
                    before = case.snapshot()

                    def failure(phase: str, index: int, boundary: tuple = boundary) -> None:
                        """Raise a storage failure at one boundary."""
                        if (phase, index) == boundary:
                            raise OSError("phase fault")
                    case.fault = failure
                    with self.assertRaises(OSError):
                        case.run_apply()
                    self.assertEqual(case.snapshot()[0], before[0])
                    # Every editor record and content file is as before; only retired journals are new
                    self.assertEqual(self.records_area(case.snapshot()), self.records_area(before))
                    if not applied:
                        self.assertEqual(case.records(), (None, None, None))
                    self.assertFalse(list(case.journals.root.glob("*.json")))
                    self.assertFalse(list(case.paths.root.rglob("*.staged-*")))

    @staticmethod
    def records_area(snapshot: tuple) -> dict[str, bytes]:
        """Return the manager files outside the journal folders."""
        return {name: data for name, data in snapshot[1].items() if "operations" not in name}

    def test_failure_after_the_commit_point_finishes_the_apply(self) -> None:
        """One failure past COMMITTING rolls forward in the same call; the result is the whole Apply."""
        for boundary in POSTCOMMIT:
            with self.subTest(boundary=boundary):
                case = self.case()
                review = case.review()
                failed = []

                def failure(phase: str, index: int, boundary: tuple = boundary) -> None:
                    """Fail once at one boundary after the commit point."""
                    if (phase, index) == boundary and not failed:
                        failed.append(phase)
                        raise OSError("one-shot failure")
                case.fault = failure
                result = case.run_apply(fingerprint=review.fingerprint)
                self.assertEqual(result["result"], "COMMITTED")
                for path, data in review.candidates.contents().items():
                    self.assertEqual((case.mission / path).read_bytes() if data is not None else None, data)
                original, baseline, applied = case.records()
                self.assertIsNotNone(original and baseline and applied)

    def test_crash_during_publication_is_rolled_back_by_recovery(self) -> None:
        """A process end after two groups leaves a journal; the 8.5 recovery restores every file."""
        case = self.case(applied=True)
        before = case.mission_files()

        def crash(phase: str, index: int) -> None:
            """End the process after the second group."""
            if (phase, index) == ("AFTER_PUBLISH", 1):
                raise Crash()
        case.fault = crash
        with self.assertRaises(Crash):
            case.run_apply()
        self.assertNotEqual(case.mission_files(), before)
        settings = SimpleNamespace(load=lambda: case.settings)
        recovery = MissionMapRecovery(case.paths, settings, InstallationGuard(case.ports, case.ports),  # type: ignore[arg-type]
                                      case.ports, case.plans._locks)
        self.assertFalse(recovery.inspect()["blocked"])
        self.assertEqual(case.mission_files(), before)
        self.assertEqual(case.review().answer["external_changes"], [])

    def test_lost_record_stage_after_the_commit_point_requires_recovery(self) -> None:
        """A missing staged record past COMMITTING is never reported as success."""
        case = self.case()

        def lose(phase: str, _index: int) -> None:
            """Remove every staged record after the commit point."""
            if phase == "AFTER_COMMITTING":
                for stage in case.paths.root.rglob("*.staged-*"):
                    Path(stage).unlink()
        case.fault = lose
        with self.assertRaises(MissionMapPublicationError) as failed:
            case.run_apply()
        self.assertTrue(failed.exception.recovery_required)
        self.assertEqual(len(list(case.journals.root.glob("*.json"))), 1)


class RecordsOnlyJournalTests(unittest.TestCase):
    """A journal without a DayZ-side group: parsed before any write, recovered by 8.5 (Architect 01:18:47)."""

    def case(self, *, file_change: bool) -> ApplyFaultCase:
        """Return a fixture after one Apply and a saved plan change, with or without a file change."""
        case = ApplyFaultCase()
        case.setUp()
        self.addCleanup(case.tearDown)
        case.save([zone("rich"), INFECTED], 25)
        case.run_apply()
        extra = [] if file_change else [dict(zone("normal", 50.0, 50.0, 10.0), id="empty")]
        case.save([zone("very_rich" if file_change else "rich"), INFECTED, *extra], 50 if file_change else 25,
                  expected=0)
        return case

    def recovery(self, case: ApplyFaultCase) -> MissionMapRecovery:
        """Return the 8.5 recovery over the fixture's guard ports and association locks."""
        settings = SimpleNamespace(load=lambda: case.settings)
        return MissionMapRecovery(case.paths, settings, InstallationGuard(case.ports, case.ports),  # type: ignore[arg-type]
                                  case.ports, case.plans._locks)

    def test_malformed_journal_is_refused_before_any_write(self) -> None:
        """An invalid plan identity refuses in preflight, with and without groups; no staging boundary is reached."""
        for file_change in (True, False):
            with self.subTest(file_change=file_change):
                case = self.case(file_change=file_change)
                review = case.review()
                publication = case.service._publication(review, parse_choices(NO_CHOICES), "bad0001")
                self.assertEqual(bool(publication.journal.groups), file_change)
                publication.journal.plan_fingerprint = "not-a-digest"
                phases: list[str] = []
                case.fault = lambda phase, _index: phases.append(phase)
                before = case.snapshot()
                with self.assertRaises(MissionMapJournalError):
                    case.commit.commit(publication.journal, publication.updates, publication.candidates,
                                       revalidate=lambda: None, existing_profiles={"main"})
                self.assertEqual(phases, ["AFTER_REVALIDATE"])
                self.assertEqual(case.snapshot(), before)

    def test_records_only_crash_is_recovered_by_8_5(self) -> None:
        """A crash at PREPARED rolls back; a crash at COMMITTING rolls forward; the mission files never change."""
        for boundary, committed in (("AFTER_PREPARED", False), ("AFTER_COMMITTING", True)):
            with self.subTest(boundary=boundary):
                case = self.case(file_change=False)
                files = case.mission_files()

                def crash(phase: str, _index: int, boundary: str = boundary) -> None:
                    """End the process at one boundary."""
                    if phase == boundary:
                        raise Crash()
                case.fault = crash
                with self.assertRaises(Crash):
                    case.run_apply()
                case.fault = None
                self.assertEqual(len(list(case.journals.root.glob("*.json"))), 1)
                self.assertFalse(self.recovery(case).inspect()["blocked"])
                self.assertFalse(list(case.journals.root.glob("*.json")))
                _original, _baseline, (applied, revision) = case.records()
                self.assertEqual((revision, applied["plan_revision"]), (1, 1) if committed else (0, 0))
                self.assertEqual(case.mission_files(), files)
                self.assertFalse(list(case.paths.root.rglob("*.staged-*")))


if __name__ == "__main__":
    unittest.main()
