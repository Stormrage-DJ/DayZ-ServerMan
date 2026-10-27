"""Mid-write failures of leaf 8.8: the previous valid state comes back, and partial success is never reported."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from dayz_serverman.application.installation_guard import InstallationGuard
from dayz_serverman.application.mission_map_recovery import MissionMapRecovery
from dayz_serverman.repositories.mission_map_publication import MissionMapPublicationError
from tests.mission_map_restore_support import PLAN, RestoreFixture

# Boundaries before the commit point of Restore previous state after one Apply: the baseline is unchanged, so
# two records (applied, ledger) and four groups
PREVIOUS_PRECOMMIT = [("AFTER_REVALIDATE", -1), ("AFTER_STAGE_RECORD", 0), ("AFTER_STAGE_RECORD", 1),
                      ("AFTER_STAGE_RECORDS", -1), ("AFTER_PREPARED", -1), ("AFTER_STAGE_GROUP", 3),
                      ("BEFORE_PUBLISH", 0), ("AFTER_PUBLISH", 0), ("AFTER_PUBLISH", 2), ("AFTER_PUBLISHED", -1)]
# Restore original also changes the baseline: three records
ORIGINAL_PRECOMMIT = PREVIOUS_PRECOMMIT[:3] + [("AFTER_STAGE_RECORD", 2)] + PREVIOUS_PRECOMMIT[3:]
# Boundaries at and after the commit point
POSTCOMMIT = [("AFTER_COMMITTING", -1), ("AFTER_PUBLISH_RECORD", 0), ("AFTER_PUBLISH_RECORD", 1),
              ("AFTER_COMMITTED", -1)]


class Crash(BaseException):
    """A process end at a boundary: no failure handler runs."""


class RestoreFaultCase(RestoreFixture):
    """One isolated fixture after one Apply, for each boundary."""

    def runTest(self) -> None:
        """Unused: the fixture serves subtests."""

    def recovery(self) -> MissionMapRecovery:
        """Return the 8.5 recovery over the fixture's guard ports and association locks."""
        settings = SimpleNamespace(load=lambda: self.settings)
        return MissionMapRecovery(self.paths, settings, InstallationGuard(self.ports, self.ports),  # type: ignore[arg-type]
                                  self.ports, self.plans._locks)

    def records_area(self) -> dict[str, bytes]:
        """Return the manager files outside the journal folders."""
        return {name: data for name, data in self.snapshot()[1].items() if "operations" not in name}


class RestoreFaultTests(unittest.TestCase):
    """A failure before the commit point restores the files and records; after it, the restore commits."""

    def case(self) -> RestoreFaultCase:
        """Return a fixture with one committed Apply."""
        case = RestoreFaultCase()
        case.setUp()
        self.addCleanup(case.tearDown)
        case.save(PLAN, 25)
        case.run_apply()
        return case

    def test_failure_before_the_commit_point_restores_the_previous_state(self) -> None:
        """Files and every editor record stay as they were; no journal and no staging file stays."""
        for mode, boundaries in (("previous", PREVIOUS_PRECOMMIT), ("original", ORIGINAL_PRECOMMIT)):
            for boundary in boundaries:
                with self.subTest(mode=mode, boundary=boundary):
                    case = self.case()
                    files, records = case.snapshot()[0], case.records_area()
                    reached: list[tuple] = []

                    def failure(phase: str, index: int, boundary: tuple = boundary) -> None:
                        """Raise a storage failure at one boundary."""
                        reached.append((phase, index))
                        if (phase, index) == boundary:
                            raise OSError("phase fault")
                    case.fault = failure
                    with self.assertRaises(OSError):
                        case.run_restore(mode)
                    self.assertIn(boundary, reached)
                    self.assertEqual((case.snapshot()[0], case.records_area()), (files, records))
                    self.assertFalse(list(case.journals.root.glob("*.json")))
                    self.assertFalse(list(case.paths.root.rglob("*.staged-*")))

    def test_failure_after_the_commit_point_finishes_the_restore(self) -> None:
        """One failure past COMMITTING rolls forward in the same call; the result is the whole restore."""
        for mode in ("previous", "original"):
            for boundary in POSTCOMMIT:
                with self.subTest(mode=mode, boundary=boundary):
                    case = self.case()
                    review = case.restore_review(mode)
                    failed: list[str] = []

                    def failure(phase: str, index: int, boundary: tuple = boundary) -> None:
                        """Fail once at one boundary after the commit point."""
                        if (phase, index) == boundary and not failed:
                            failed.append(phase)
                            raise OSError("one-shot failure")
                    case.fault = failure
                    self.assertEqual(case.run_restore(mode, review.fingerprint)["result"], "COMMITTED")
                    self.assertEqual(failed, [boundary[0]])
                    for path, data in review.context.target.items():
                        self.assertEqual(case.mission_state()[path], data)
                    self.assertEqual(case.records()[2][0]["operation_kind"], f"restore_{mode}")

    def test_crash_is_recovered_by_8_5(self) -> None:
        """A crash while publishing rolls back; a crash at COMMITTING rolls forward; no block stays."""
        for boundary, committed in ((("AFTER_PUBLISH", 1), False), (("AFTER_COMMITTING", -1), True)):
            with self.subTest(boundary=boundary):
                case = self.case()
                before = case.mission_state()
                target = case.restore_review("original").context.target

                def crash(phase: str, index: int, boundary: tuple = boundary) -> None:
                    """End the process at one boundary."""
                    if (phase, index) == boundary:
                        raise Crash()
                case.fault = crash
                with self.assertRaises(Crash):
                    case.run_restore("original")
                case.fault = None
                self.assertFalse(case.recovery().inspect()["blocked"])
                self.assertFalse(list(case.journals.root.glob("*.json")))
                state = case.mission_state()
                expected = dict(before, **target) if committed else before
                self.assertEqual(state, expected)
                kind = case.records()[2][0]["operation_kind"]
                self.assertEqual(kind, "restore_original" if committed else "apply")
                self.assertFalse(list(case.paths.root.rglob("*.staged-*")))

    def test_records_only_restore_crash_is_recovered_by_8_5(self) -> None:
        """Restore original over the captured files changes no file; a crash at PREPARED or COMMITTING recovers."""
        for boundary, committed in (("AFTER_PREPARED", False), ("AFTER_COMMITTING", True)):
            with self.subTest(boundary=boundary):
                case = self.case()
                case.run_restore("previous")
                files = case.mission_state()

                def crash(phase: str, _index: int, boundary: str = boundary) -> None:
                    """End the process at one boundary."""
                    if phase == boundary:
                        raise Crash()
                case.fault = crash
                with self.assertRaises(Crash):
                    case.run_restore("original")
                case.fault = None
                self.assertFalse(case.recovery().inspect()["blocked"])
                self.assertEqual(case.mission_state(), files)
                kind = case.records()[2][0]["operation_kind"]
                self.assertEqual(kind, "restore_original" if committed else "restore_previous")

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
            case.run_restore("previous")
        self.assertTrue(failed.exception.recovery_required)
        self.assertEqual(len(list(case.journals.root.glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
