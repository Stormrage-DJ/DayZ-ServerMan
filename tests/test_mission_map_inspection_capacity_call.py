"""Verify that the Inspect capacity step runs in a call of its own, without the reader side (6.9 QA F1)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dayz_serverman.application import mission_map_inspection_run
from dayz_serverman.application.mission_map_evaluation import ObjectTierRead
from dayz_serverman.application.mission_map_inspection_run import InspectionRun, InspectTarget, PlanSource
from dayz_serverman.domain.mission_map_plan import PlanForm, parse_plan
from dayz_serverman.repositories.mission_map_mapgroup_read import POS, PROTO
from dayz_serverman.repositories.mission_map_read_pass import MissionReadPass
from tests.mission_map_inspection_support import RecordingReader, StepClock, write_mission
from tests.mission_map_mapgroup_support import POSITIONS, PROTOTYPE
from tests.test_mission_map_plan import circle, stored_plan


class CapacityCallTests(unittest.TestCase):
    """The capacity step runs in a call of its own, without the reader side (QA F1)."""

    def test_capacity_has_its_own_call_and_the_proof_follows(self) -> None:
        """No slice and no sampling share the capacity call; the reader is free; a proof pass always follows."""
        temp = tempfile.TemporaryDirectory(prefix="serverman_capacity_call_")
        self.addCleanup(temp.cleanup)
        mission = Path(temp.name)
        write_mission(mission)
        (mission / PROTO).write_bytes(PROTOTYPE)
        (mission / POS).write_bytes(POSITIONS)
        plan = parse_plan(stored_plan([circle("c1", x=500.0, z=500.0, radius=50.0)]), PlanForm.STORED)
        reader, events, call = RecordingReader(), [], [0]
        real_slice, real_sample = MissionReadPass.read_slice, ObjectTierRead.advance
        real_capacity = mission_map_inspection_run.object_capacity

        def read_slice(read, deadline):
            """Record a reader slice and its call."""
            events.append(("slice", call[0], read._retain))
            return real_slice(read, deadline)

        def sample(read, deadline=None):
            """Record a sampling step and its call."""
            events.append(("sample", call[0], None))
            return real_sample(read, deadline)

        def capacity(*arguments):
            """Record the capacity step, its call and whether the reader side is held."""
            events.append(("capacity", call[0], reader.held))
            return real_capacity(*arguments)
        run = InspectionRun(mission, plan, PlanSource("saved", 0, None), InspectTarget(object_id="c1"), reader,
                            lambda: True, clock=StepClock(0.01))
        with mock.patch.object(MissionReadPass, "read_slice", read_slice), \
                mock.patch.object(ObjectTierRead, "advance", sample), \
                mock.patch.object(mission_map_inspection_run, "object_capacity", capacity):
            while True:
                call[0] += 1
                answer = run.advance()
                if answer["status"] != "running":
                    break
        self.assertEqual(answer["status"], "done")
        self.assertTrue(answer["result"]["capacity"])
        [step] = [event for event in events if event[0] == "capacity"]
        self.assertFalse(step[2])
        self.assertEqual([event for event in events if event[1] == step[1]], [step])
        before = {event[0] for event in events if event[1] < step[1]}
        after = [event for event in events if event[1] > step[1]]
        self.assertEqual(before, {"slice", "sample"})
        self.assertTrue(after and all(event[:1] == ("slice",) and event[2] is False for event in after))


if __name__ == "__main__":
    unittest.main()
