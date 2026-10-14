"""A181 tests 12 and 16: a real D4 publication during an Inspect read, and D4 with an open shared reader."""

from __future__ import annotations

import unittest

from dayz_serverman.adapters.windows.shared_files import open_shared
from dayz_serverman.application.mission_map_inspection_run import InspectionRun, InspectTarget, PlanSource
from dayz_serverman.application.mission_map_reader_port import NoReaderPort
from dayz_serverman.domain.mission_map_plan import PlanForm, parse_plan
from tests.mission_map_inspection_support import StepClock, write_mission
from tests.test_mission_map_journal import NEW, OLD
from tests.test_mission_map_plan import circle, stored_plan
from tests.test_mission_map_publication import PublicationFixture


class PublicationDuringInspectTests(PublicationFixture):
    """The fixture mission holds a valid areaflags.map that a real D4 publication replaces."""

    def setUp(self) -> None:
        """Write a valid mission before the journal records its old state."""
        super().setUp()
        write_mission(self.mission)
        self.journal_ready = self.prepared()
        plan = parse_plan(stored_plan([circle("c1", x=500.0, z=500.0, radius=400.0)]), PlanForm.STORED)
        self.run = InspectionRun(self.mission, plan, PlanSource("saved", 0, None), InspectTarget(object_id="c1"),
                                 NoReaderPort(), lambda: True, clock=StepClock(0.2))

    def to_proof(self) -> None:
        """Advance until the next call starts the proof pass."""
        for _ in range(5000):
            if self.run.stage == "proof":
                return
            self.assertEqual(self.run.advance()["status"], "running")
        raise AssertionError("the read never reached its proof")

    def test_12a_publication_between_capture_and_proof_gives_stale(self) -> None:
        """The proof reads the published bytes, so the digests differ."""
        self.to_proof()
        self.publication().publish_groups(self.journal_ready)
        answers = [self.run.advance()]
        while answers[-1]["status"] == "running":
            answers.append(self.run.advance())
        self.assertEqual(answers[-1], {"status": "stale"})

    def test_12b_publication_between_two_proof_slices_gives_stale(self) -> None:
        """The second proof slice finds the new file ID of the published areaflags.map."""
        self.to_proof()
        self.assertEqual(self.run.advance()["status"], "running")
        self.assertEqual(self.run._pass.position, (1, 0))
        self.publication().publish_groups(self.journal_ready)
        self.assertEqual(self.run.advance(), {"status": "stale"})

    def test_the_unchanged_mission_proves_fresh(self) -> None:
        """Control: without a publication the same steps give done."""
        self.to_proof()
        answer = self.run.advance()
        while answer["status"] == "running":
            answer = self.run.advance()
        self.assertEqual(answer["status"], "done")


class SharedReaderPublicationTests(PublicationFixture):
    """A181 test 16: D4 publication and compensation succeed while a shared reader holds the target."""

    def test_16_publication_and_compensation_with_an_open_shared_reader(self) -> None:
        """The reader keeps reading the bytes that it opened; the target changes under it."""
        journal = self.prepared()
        target = self.mission / "areaflags.map"
        with open_shared(target) as reader:
            self.publication().publish_groups(journal)
            self.assertEqual(reader.read(), OLD["areaflags.map"])
        self.assertEqual(target.read_bytes(), NEW["areaflags.map"])
        added = self.mission / "env" / "zombie_territories.xml"
        with open_shared(target) as reader, open_shared(added) as other:
            self.assertTrue(self.publication().compensate(journal))
            self.assertEqual(reader.read(), NEW["areaflags.map"])
            self.assertEqual(other.read(), NEW["env/zombie_territories.xml"])
        self.assert_old_state()


if __name__ == "__main__":
    unittest.main()
