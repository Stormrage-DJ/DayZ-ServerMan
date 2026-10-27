"""Fault injection in each D4 phase of the coordinated medical toggle (8.13), with the 8.5 recovery."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from dayz_serverman.application.installation_guard import InstallationGuard
from dayz_serverman.application.mission_map_recovery import MissionMapRecovery
from dayz_serverman.domain.medical_editor_texts import MEDICAL_KIND
from dayz_serverman.repositories.mission_map_journal_store import parse_journal
from dayz_serverman.repositories.mission_map_publication import MissionMapPublicationError
from tests.medical_editor_support import ToggleFixture

# Boundaries of one toggle before its commit point: two records (applied, ledger) and one group
PRECOMMIT = [("AFTER_REVALIDATE", -1), ("AFTER_STAGE_RECORD", 0), ("AFTER_STAGE_RECORD", 1),
             ("AFTER_STAGE_RECORDS", -1), ("AFTER_PREPARED", -1), ("AFTER_STAGE_GROUP", 0), ("BEFORE_PUBLISH", 0),
             ("AFTER_PUBLISH", 0), ("AFTER_PUBLISHED", -1)]
# Boundaries at and after the commit point
POSTCOMMIT = [("AFTER_COMMITTING", -1), ("AFTER_PUBLISH_RECORD", 0), ("AFTER_PUBLISH_RECORD", 1),
              ("AFTER_COMMITTED", -1)]


class Crash(BaseException):
    """A process end at a boundary: no failure handler runs."""


class FaultCase(ToggleFixture):
    """One isolated fixture after an Apply with medical off."""

    def runTest(self) -> None:
        """Unused: the fixture serves subtests."""

    def recovery(self) -> MissionMapRecovery:
        """Return the 8.5 recovery over the fixture's guard ports and association locks."""
        settings = SimpleNamespace(load=lambda: self.settings)
        return MissionMapRecovery(self.paths, settings, InstallationGuard(self.ports, self.ports),  # type: ignore[arg-type]
                                  self.ports, self.plans._locks)

    def manager_records(self) -> dict[str, bytes]:
        """Return the manager files outside the journal folders."""
        return {name: data for name, data in self.snapshot()[1].items() if "operations" not in name}

    def active_journals(self) -> list[Path]:
        """Return the active journal files."""
        return list(self.journals.root.glob("*.json"))


class ToggleFaultTests(unittest.TestCase):
    """A failure before the commit point keeps the state before the toggle; after it the toggle completes."""

    def case(self) -> FaultCase:
        """Return a fixture after one Apply with medical off and a stored medical baseline."""
        case = FaultCase()
        case.setUp()
        self.addCleanup(case.tearDown)
        case.applied_with_medical()
        return case

    def test_failure_before_the_commit_point_restores_the_state(self) -> None:
        """Mission files and every editor record stay; no journal and no staging file stays."""
        for boundary in PRECOMMIT:
            with self.subTest(boundary=boundary):
                case = self.case()
                files, records = case.snapshot()[0], case.manager_records()

                def failure(phase: str, index: int, boundary: tuple = boundary) -> None:
                    """Raise a storage failure at one boundary."""
                    if (phase, index) == boundary:
                        raise OSError("phase fault")
                case.fault = failure
                with self.assertRaises(OSError):
                    case.run_toggle(True)
                self.assertEqual(case.snapshot()[0], files)
                self.assertEqual(case.manager_records(), records)
                self.assertFalse(case.active_journals())
                self.assertFalse(list(case.paths.root.rglob("*.staged-*")))

    def test_failure_after_the_commit_point_finishes_the_toggle(self) -> None:
        """One failure past COMMITTING rolls forward in the same call."""
        for boundary in POSTCOMMIT:
            with self.subTest(boundary=boundary):
                case = self.case()
                expected = case.zone_preview(True)["proposed_digest"]
                failed: list[str] = []

                def failure(phase: str, index: int, boundary: tuple = boundary) -> None:
                    """Fail once at one boundary after the commit point."""
                    if (phase, index) == boundary and not failed:
                        failed.append(phase)
                        raise OSError("one-shot failure")
                case.fault = failure
                case.run_toggle(True)
                self.assertEqual(failed, [boundary[0]])
                self.assertEqual(hashlib.sha256(case.proto()).hexdigest(), expected)
                self.assertEqual(case.applied_record()[0]["operation_kind"], MEDICAL_KIND)

    def test_crash_in_each_phase_is_recovered_by_8_5(self) -> None:
        """A process end leaves a journal or staging; recovery rolls back before COMMITTING and forward after it."""
        for boundary in PRECOMMIT + POSTCOMMIT:
            with self.subTest(boundary=boundary):
                case = self.case()
                files, records = case.snapshot()[0], case.manager_records()
                expected = case.zone_preview(True)["proposed_digest"]

                def crash(phase: str, index: int, boundary: tuple = boundary) -> None:
                    """End the process at one boundary."""
                    if (phase, index) == boundary:
                        raise Crash()
                case.fault = crash
                with self.assertRaises(Crash):
                    case.run_toggle(True)
                case.fault = None
                for path in case.active_journals():
                    journal = parse_journal(json.loads(path.read_text(encoding="utf-8")), path.stem)
                    self.assertEqual((journal.operation_kind, journal.plan_id, journal.preview_fingerprint is None),
                                     (MEDICAL_KIND, None, False))
                self.assertFalse(case.recovery().inspect()["blocked"])
                self.assertFalse(case.active_journals())
                self.assertFalse(list(case.paths.root.rglob("*.staged-*")))
                if boundary in POSTCOMMIT:
                    self.assertEqual(hashlib.sha256(case.proto()).hexdigest(), expected)
                    self.assertEqual(case.applied_record()[0]["operation_kind"], MEDICAL_KIND)
                else:
                    self.assertEqual(case.snapshot()[0], files)
                    self.assertEqual(case.manager_records(), records)
                # The lane is free again: the next toggle commits
                case.run_toggle(True if boundary not in POSTCOMMIT else False)

    def test_lost_record_stage_after_the_commit_point_requires_recovery(self) -> None:
        """A missing staged record past COMMITTING is never reported as success, and recovery stays blocked."""
        case = self.case()

        def lose(phase: str, _index: int) -> None:
            """Remove every staged record after the commit point."""
            if phase == "AFTER_COMMITTING":
                for stage in case.paths.root.rglob("*.staged-*"):
                    Path(stage).unlink()
        case.fault = lose
        with self.assertRaises(MissionMapPublicationError) as failed:
            case.run_toggle(True)
        self.assertTrue(failed.exception.recovery_required)
        self.assertEqual(len(case.active_journals()), 1)
        case.fault = None
        self.assertTrue(case.recovery().inspect()["blocked"])


if __name__ == "__main__":
    unittest.main()
