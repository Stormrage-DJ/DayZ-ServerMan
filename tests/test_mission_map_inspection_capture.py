"""A181 tests 6 and 8: checks after a completed capture pass, and no grid or limits-file bound."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from time import monotonic
from unittest import mock

from dayz_serverman.adapters.windows.shared_files import open_shared
from dayz_serverman.application.mission_map_inspection_run import (
    InspectionRun, InspectTarget, PlanSource,
)
from dayz_serverman.application.mission_map_reader_port import NoReaderPort
from dayz_serverman.bridge.contracts import ErrorCode
from dayz_serverman.bridge.facade import ApplicationCallError
from dayz_serverman.domain.mission_map_plan import PlanForm, parse_plan
from dayz_serverman.domain.mission_map_terrain import TerrainDataError
from dayz_serverman.repositories import mission_map_read_pass
from dayz_serverman.repositories.mission_map_evaluation import read_snapshot, snapshot_from_pass
from dayz_serverman.repositories.mission_map_read_pass import MissionReadPass
from dayz_serverman.repositories.mission_map_terrain import read_capabilities, read_header, read_value_flags
from tests.mission_map_inspection_support import InspectionFixture, NAMES
from tests.test_mission_map_plan import circle, stored_plan
from tests.test_mission_map_terrain import areaflags_bytes
from tests.test_mission_map_terrain_files import limits_text

# Mission files of the 3.5 and 4.1 fixtures: None leaves a file out
VALID = areaflags_bytes(8, 4, 12800, 12800, 32, 4)
CASES = {
    "valid 4-bit": (VALID, limits_text(*NAMES)),
    "valid 8-bit": (areaflags_bytes(6, 4, 1000, 1000, 16, 8, {(1, 1): 255}), limits_text("Tier1", "Unique")),
    "missing areaflags": (None, limits_text("Tier1")),
    "missing limits": (VALID, None),
    "both missing": (None, None),
    "size mismatch": (VALID + b"x", limits_text("Tier1")),
    "short header": (b"bad", limits_text("Tier1")),
    "malformed limits": (VALID, "<lists><valueflags>"),
    "limits encoding": (VALID, b"\xff\xfe<lists/>"),
    "no valueflags section": (VALID, "<lists><usageflags/></lists>"),
    "bad flag name": (VALID, limits_text("Tier 1")),
    "limits with BOM": (VALID, b"\xef\xbb\xbf" + limits_text("Tier1", "Unique").encode()),
}


def capture(mission: Path) -> MissionReadPass:
    """Read a whole capture pass in one slice."""
    read = MissionReadPass(mission, retain=True)
    read.read_slice(float("inf"))
    return read


class CaptureCheckTests(unittest.TestCase):
    """Compare the checks after a pass with the 3.5 capability read and the 4.1 whole-file snapshot."""

    def setUp(self) -> None:
        """Create a temporary mpmissions folder."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_capture_")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def mission(self, name: str, area: bytes | None, limits: str | bytes | None) -> Path:
        """Write one mission folder of the fixture table."""
        root = self.base / name
        root.mkdir()
        if area is not None:
            (root / "areaflags.map").write_bytes(area)
        if limits is not None:
            (root / "cfglimitsdefinition.xml").write_bytes(limits.encode() if isinstance(limits, str) else limits)
        return root

    def test_6_a_completed_pass_gives_the_fixture_headers_flags_reasons_and_messages(self) -> None:
        """Capabilities equal the 3.5 read; object failures keep the 4.1 reasons and the 3.5 messages."""
        for index, (name, (area, limits)) in enumerate(CASES.items()):
            with self.subTest(name):
                root = self.mission(f"case{index}.enoch", area, limits)
                read = capture(root)
                snapshot = snapshot_from_pass(root, read.content(), read.digests())
                self.assertEqual(snapshot.capabilities, read_capabilities(root))
                try:
                    whole = read_snapshot(root)
                except TerrainDataError as error:
                    with self.assertRaises(TerrainDataError) as caught:
                        snapshot.require_tiers()
                    self.assertEqual(caught.exception.reason, error.reason)
                    self.assertEqual(str(caught.exception), self.message_of(root, caught.exception))
                    continue
                self.assertEqual(snapshot.require_tiers(), whole.values)
                self.assertEqual(snapshot.digests, (whole.areaflags_sha256, whole.limits_sha256))

    @staticmethod
    def message_of(root: Path, error: TerrainDataError) -> str:
        """Return the 3.5 message of the failing part; a missing file keeps the 4.1 unreadable message."""
        if error.reason == "mission_file_unreadable":
            name = "areaflags.map" if not (root / "areaflags.map").exists() else "cfglimitsdefinition.xml"
            return f"{name} could not be read"
        reader = read_header if error.reason == "tier_header_invalid" else read_value_flags
        name = "areaflags.map" if reader is read_header else "cfglimitsdefinition.xml"
        try:
            reader(root / name)
        except TerrainDataError as expected:
            return str(expected)
        raise AssertionError("the 3.5 reader accepted the file")

    def test_6_unreadable_limits_with_an_invalid_header_is_mission_file_unreadable(self) -> None:
        """An unreadable slice reports mission_file_unreadable before any content error."""
        root = self.mission("locked.enoch", b"bad", limits_text("Tier1"))

        def refuse(path):
            """Refuse the limits file only."""
            if Path(path).name == "cfglimitsdefinition.xml":
                raise PermissionError(13, "denied")
            return open_shared(path)
        with mock.patch.object(mission_map_read_pass, "open_shared", refuse):
            with self.assertRaises(TerrainDataError) as caught:
                capture(root)
        self.assertEqual(caught.exception.reason, "mission_file_unreadable")

    def test_8_no_grid_or_limits_bound(self) -> None:
        """A 4,098 by 4,100 grid and a limits file over 64 KiB give a complete object result."""
        usage = "".join(f'<usage name="Usage{index}"/>' for index in range(5000))
        values = "".join(f'<value name="{name}"/>' for name in NAMES)
        limits = f"<lists><usageflags>{usage}</usageflags><valueflags>{values}</valueflags></lists>"
        self.assertGreater(len(limits), 64 * 1024)
        area = areaflags_bytes(4098, 4100, 1000, 1000, 8, 4, {(410, 410): 0b0101})
        root = self.mission("large.chernarusplus", area, limits)
        plan = parse_plan(stored_plan([circle("c1", x=100.0, z=100.0, radius=10.0)]), PlanForm.STORED)
        run = InspectionRun(root, plan, PlanSource("saved", 0, None), InspectTarget(object_id="c1"),
                            NoReaderPort(), lambda: True)
        started = monotonic()
        answer = run.advance()
        while answer["status"] == "running":
            answer = run.advance()
        self.assertLess(monotonic() - started, 60)
        self.assertEqual(answer["status"], "done")
        self.assertEqual(answer["result"]["original_tiers"], ["Tier1", "Tier3"])
        self.assertGreater(answer["result"]["cell_count"], 1000)


class CaptureServiceTests(InspectionFixture):
    """The service binds capture failures by target."""

    def test_object_failures_and_point_availability(self) -> None:
        """An invalid header refuses an object read; a point gets the availability result."""
        self.saved([circle("c1")])
        (self.mission / "areaflags.map").write_bytes(b"bad")
        with self.assertRaises(ApplicationCallError) as caught:
            self.finish(self.start({"object": "c1"}))
        error = caught.exception
        self.assertEqual((error.code, error.details["reason"], error.retryable),
                         (ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT, "tier_header_invalid", False))
        answer, _phases = self.finish(self.start({"point": {"x": 100, "z": 100}}))
        self.assertEqual(answer["status"], "done")
        result = answer["result"]
        self.assertEqual((result["header_cell"], result["original_tier"], result["tier_source"]),
                         (None, None, "unknown"))
        self.assertEqual((result["plan_source"], result["plan_revision"]), ("saved", 0))

    def test_unreadable_file_is_a_retryable_storage_failure(self) -> None:
        """A sharing failure in a slice gives STORAGE_FAILURE with mission_file_unreadable."""
        self.saved([circle("c1")])
        with mock.patch.object(mission_map_read_pass, "open_shared", side_effect=PermissionError(32, "in use")):
            with self.assertRaises(ApplicationCallError) as caught:
                self.finish(self.start({"object": "c1"}))
        error = caught.exception
        self.assertEqual((error.code, error.details["reason"], error.retryable),
                         (ErrorCode.STORAGE_FAILURE, "mission_file_unreadable", True))
        self.assertEqual(self.jobs.live(), 0)


if __name__ == "__main__":
    unittest.main()
