"""A181 tests 1 to 5 and 7: the resumable mission read pass, its signatures, handles and retained bytes."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dayz_serverman.adapters.windows.shared_files import open_shared, replace_file
from dayz_serverman.application.mission_map_inspection_run import (
    InspectionRun, InspectTarget, PlanSource,
)
from dayz_serverman.domain.mission_map_plan import PlanForm, parse_plan
from dayz_serverman.domain.mission_map_terrain import MISSION_FILE_UNREADABLE, TerrainDataError
from dayz_serverman.repositories import mission_map_read_pass
from dayz_serverman.repositories.mission_map_read_pass import MissionReadPass
from tests.mission_map_inspection_support import RecordingReader, StepClock, write_mission
from tests.test_mission_map_plan import circle, stored_plan
from tests.test_mission_map_terrain import areaflags_bytes


class RecordingStream:
    """A shared-read stream that records the clock time and the offset of each block read."""

    def __init__(self, stream, clock: StepClock, reads: list[tuple[str, float, int]], name: str) -> None:
        """Wrap one open stream."""
        self._stream, self._clock, self._reads, self._name = stream, clock, reads, name

    def __enter__(self):
        """Enter the wrapped stream."""
        self._stream.__enter__()
        return self

    def __exit__(self, *details):
        """Close the wrapped stream."""
        return self._stream.__exit__(*details)

    def fileno(self) -> int:
        """Return the descriptor of the wrapped stream."""
        return self._stream.fileno()

    def seek(self, offset: int) -> int:
        """Seek the wrapped stream."""
        return self._stream.seek(offset)

    def read(self, size: int) -> bytes:
        """Record the time and the offset, then read."""
        self._reads.append((self._name, self._clock.now, self._stream.tell()))
        return self._stream.read(size)


class ReadPassTests(unittest.TestCase):
    """Temporary mission folders; every pass opens and closes its handles inside one slice."""

    def setUp(self) -> None:
        """Create a mission folder with a 4,000-byte areaflags file and a limits file."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_read_pass_")
        self.addCleanup(temporary.cleanup)
        self.mission = Path(temporary.name) / "mpmissions" / "dayzOffline.test"
        self.mission.mkdir(parents=True)
        self.area = os.urandom(4000)
        (self.mission / "areaflags.map").write_bytes(self.area)
        self.limits = b"<lists/>" * 100
        (self.mission / "cfglimitsdefinition.xml").write_bytes(self.limits)

    def read_pass(self, clock: StepClock, *, retain: bool = False) -> MissionReadPass:
        """Return a pass with 100-byte blocks."""
        return MissionReadPass(self.mission, retain=retain, clock=clock, block_size=100)

    def test_1_slices_resume_at_the_exact_offset_and_no_block_starts_after_the_deadline(self) -> None:
        """The digests equal hashlib of the whole files; each slice starts where the last one stopped."""
        clock = StepClock(0.01)
        reads: list[tuple[str, float, int]] = []
        opener = lambda path: RecordingStream(open_shared(path), clock, reads, Path(path).name)  # noqa: E731
        read = self.read_pass(clock)
        slices = 0
        with mock.patch.object(mission_map_read_pass, "open_shared", opener):
            while not read.complete:
                before, first_read = read.position, len(reads)
                deadline = clock.now + 0.05
                read.read_slice(deadline)
                slices += 1
                self.assertTrue(all(moment < deadline for _name, moment, _offset in reads[first_read:]))
                if first_read < len(reads) and before[0] == 0:
                    self.assertEqual(reads[first_read][2], before[1])
        self.assertGreater(slices, 10)
        self.assertEqual(read.digests(), (hashlib.sha256(self.area).hexdigest(),
                                          hashlib.sha256(self.limits).hexdigest()))

    def test_2_no_handle_survives_a_slice(self) -> None:
        """Between slices the mission folder can be renamed and a file replaced (QF-2)."""
        read = self.read_pass(StepClock(0.01))
        read.read_slice(0.05)
        self.assertFalse(read.complete)
        moved = self.mission.with_name("dayzOffline.moved")
        os.rename(self.mission, moved)
        os.rename(moved, self.mission)
        (self.mission / "replacement.tmp").write_bytes(self.limits)
        replace_file(self.mission / "replacement.tmp", self.mission / "cfglimitsdefinition.xml")
        self.assertTrue((self.mission / "cfglimitsdefinition.xml").is_file())

    def test_3_a_replacement_with_restored_time_and_size_is_a_change(self) -> None:
        """Only a block already read differs; the new file ID still marks the pass changed."""
        read = self.read_pass(StepClock(0.01))
        read.read_slice(0.05)
        self.assertGreater(read.position[1], 0)
        status = os.stat(self.mission / "areaflags.map")
        replacement = self.mission / "areaflags.tmp"
        replacement.write_bytes(bytes([self.area[0] ^ 1]) + self.area[1:])
        os.utime(replacement, ns=(status.st_atime_ns, status.st_mtime_ns))
        replace_file(replacement, self.mission / "areaflags.map")
        read.read_slice(10.0)
        self.assertTrue(read.changed)
        self.assertFalse(read.complete)

    def test_4_time_and_size_changes_are_changes_and_an_unread_edit_changes_the_digest(self) -> None:
        """Same size with a new time, growth and truncation are changes; a hidden unread edit is stale."""
        path = self.mission / "areaflags.map"
        for case in ("time", "grow", "truncate", "hidden"):
            with self.subTest(case=case):
                path.write_bytes(self.area)
                read = self.read_pass(StepClock(0.01))
                read.read_slice(0.05)
                status = os.stat(path)
                with open(path, "r+b") as stream:
                    if case == "grow":
                        stream.seek(0, os.SEEK_END)
                        stream.write(b"x")
                    elif case == "truncate":
                        stream.truncate(3000)
                    else:
                        stream.seek(3900)
                        stream.write(b"changed")
                moved = status.st_mtime_ns + (10**9 if case == "time" else 0)
                os.utime(path, ns=(status.st_atime_ns, moved))
                while not read.changed and not read.complete:
                    read.read_slice(read._clock.now + 0.05)
                if case == "hidden":
                    self.assertTrue(read.complete)
                    self.assertNotEqual(read.digests()[0], hashlib.sha256(self.area).hexdigest())
                else:
                    self.assertTrue(read.changed)

    def test_4_a_file_that_gets_shorter_during_a_slice_is_a_change(self) -> None:
        """A read that ends before the signature size marks the pass changed, not complete (QA-4.2-3)."""
        clock = StepClock(0.01)
        reads: list[tuple[str, float, int]] = []

        class Shortened(RecordingStream):
            """A stream whose third block read finds the end of the file."""

            def read(self, size: int) -> bytes:
                """Return nothing from the third read on."""
                return b"" if len(reads) >= 2 else super().read(size)
        opener = lambda path: Shortened(open_shared(path), clock, reads, Path(path).name)  # noqa: E731
        read = self.read_pass(clock)
        with mock.patch.object(mission_map_read_pass, "open_shared", opener):
            read.read_slice(10.0)
        self.assertTrue(read.changed)
        self.assertFalse(read.complete)
        self.assertEqual(read.position, (0, 200))

    def test_5_unreadable_file_in_a_later_slice_is_mission_file_unreadable(self) -> None:
        """A sharing failure in any slice ends the pass with the accepted reason."""
        read = self.read_pass(StepClock(0.01))
        read.read_slice(0.05)

        def refuse(path):
            """Refuse the limits file only."""
            if Path(path).name == "cfglimitsdefinition.xml":
                raise PermissionError(32, "in use")
            return open_shared(path)
        with mock.patch.object(mission_map_read_pass, "open_shared", refuse):
            with self.assertRaises(TerrainDataError) as caught:
                read.read_slice(10.0)
        self.assertEqual(caught.exception.reason, MISSION_FILE_UNREADABLE)
        self.assertEqual(str(caught.exception), "cfglimitsdefinition.xml could not be read")

    def test_5_a_busy_reader_opens_nothing_and_keeps_the_progress(self) -> None:
        """A closed reader side returns running with unchanged progress and opens no mission file."""
        write_mission(self.mission)
        plan = parse_plan(stored_plan([circle("c1")]), PlanForm.STORED)
        reader = RecordingReader()
        run = InspectionRun(self.mission, plan, PlanSource("saved", 0, None), InspectTarget(object_id="c1"),
                            reader, lambda: True, clock=StepClock(0.2))
        reader.busy = True
        with mock.patch.object(mission_map_read_pass, "open_shared", side_effect=AssertionError) as opener:
            answers = [run.advance() for _ in range(3)]
        opener.assert_not_called()
        self.assertEqual(answers, [{"status": "running", "phase": "reading", "rows_done": None,
                                    "rows_total": None}] * 3)
        self.assertEqual((run._pass.position, run.stage), ((0, 0), "capture"))
        reader.busy = False
        self.assertEqual(run.advance()["status"], "running")
        self.assertGreater(run._pass.position, (0, 0))

    def test_a_slow_context_recheck_keeps_the_minimum_read_time(self) -> None:
        """The minimum read time starts after the recheck, so a slow recheck still reads (QA-4.2-6)."""
        write_mission(self.mission)
        plan = parse_plan(stored_plan([circle("c1")]), PlanForm.STORED)
        clock = StepClock(0.01)

        def slow_recheck() -> bool:
            """Take 0.6 s of the call, past the 0.5 s call target."""
            clock.now += 0.6
            return True
        run = InspectionRun(self.mission, plan, PlanSource("saved", 0, None), InspectTarget(object_id="c1"),
                            RecordingReader(), slow_recheck, clock=clock)
        run.advance()
        # The capture either completed or moved its cursor; a starved slice keeps (0, 0)
        self.assertTrue(run._pass is None or run._pass.position > (0, 0))

    def test_7_the_capture_retains_no_usage_layer(self) -> None:
        """Only the header, the value-bit count, the value layer and the limits bytes stay in memory."""
        area = areaflags_bytes(64, 64, 1000, 1000, 32, 4)
        write_mission(self.mission, area)
        read = MissionReadPass(self.mission, retain=True)
        read.read_slice(float("inf"))
        limits = (self.mission / "cfglimitsdefinition.xml").read_bytes()
        layer = 64 * 64 * 4 // 8
        self.assertEqual(read.retained_bytes, 20 + 4 + layer + len(limits))
        content = read.content()
        self.assertEqual(content.value_layer, area[-layer:])
        self.assertEqual((content.head, content.area_size), (area[:20], len(area)))
        self.assertLess(read.retained_bytes, len(area) // 4)
        # A proof pass keeps nothing
        proof = MissionReadPass(self.mission, retain=False)
        proof.read_slice(float("inf"))
        self.assertEqual(proof.retained_bytes, 0)
        self.assertEqual(proof.digests(), read.digests())


if __name__ == "__main__":
    unittest.main()
