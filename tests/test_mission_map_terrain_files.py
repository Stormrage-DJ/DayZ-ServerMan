"""Cover the read-only terrain capability reads of synthetic mission folders through the shared-file adapter."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dayz_serverman.adapters.windows import shared_files
from dayz_serverman.domain.mission_map_geometry import Bounds
from dayz_serverman.domain.mission_map_terrain import (
    AREAFLAGS_MISSING, LIMITS_INVALID, LIMITS_MISSING, MISSION_FILE_UNREADABLE, TIER_HEADER_INVALID, TerrainDataError,
)
from dayz_serverman.repositories import mission_map_terrain
from dayz_serverman.repositories.mission_map_terrain import (
    read_capabilities, read_header, read_value_flags, read_value_rows,
)
from tests.test_mission_map_terrain import areaflags_bytes


def limits_text(*names: str) -> str:
    """Return a cfglimitsdefinition.xml with usage flags and the given value flags in order."""
    values = "".join(f'<value name="{name}"/>' for name in names)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<lists><usageflags><usage name="Military"/>'
            f"</usageflags><valueflags>{values}</valueflags></lists>\n")


class TerrainFileTests(unittest.TestCase):
    """Read synthetic missions in a temporary folder; nothing outside it is touched."""

    def setUp(self) -> None:
        """Create a temporary mpmissions folder."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.missions = Path(self.temporary.name) / "mpmissions"

    def mission(self, name: str, areaflags: bytes | None, limits: str | bytes | None) -> Path:
        """Write a mission folder with the given areaflags.map and cfglimitsdefinition.xml; None leaves a file out."""
        root = self.missions / name
        root.mkdir(parents=True)
        if areaflags is not None:
            (root / "areaflags.map").write_bytes(areaflags)
        if limits is not None:
            payload = limits.encode("utf-8") if isinstance(limits, str) else limits
            (root / "cfglimitsdefinition.xml").write_bytes(payload)
        return root

    def test_four_value_bits_mission(self) -> None:
        """A Pripyat-like 4-bit mission detects bounds, holds four tiers and lists Unique as not holdable."""
        root = self.mission("dayzOffline.Pripyat", areaflags_bytes(8, 8, 20480, 20480, 16, 4),
                           limits_text("Tier1", "Tier2", "Tier3", "Tier4", "Unique"))
        result = read_capabilities(root).to_dict(Bounds(0.0, 0.0, 20480.0, 20480.0), True)
        self.assertEqual(result["terrain_id"], "Pripyat")
        self.assertEqual(result["header"], {"columns": 8, "rows": 8, "world_width": 20480, "world_height": 20480,
                                            "value_bits": 4})
        self.assertEqual(result["detected_bounds"], {"x_min": 0.0, "z_min": 0.0, "width": 20480.0, "height": 20480.0})
        self.assertEqual(result["holdable_tiers"], ["Tier1", "Tier2", "Tier3", "Tier4"])
        self.assertEqual(result["tier_writes"], {"available": True, "reason": None})
        self.assertEqual(result["unsupported"], [{"operation": "tier:Unique", "reason": "tier_not_holdable"}])

    def test_eight_value_bits_mission(self) -> None:
        """A Chernarus-like 8-bit mission holds all five flags and lists nothing as unsupported."""
        root = self.mission("serverman-test20261001-2.chernarusplus", areaflags_bytes(8, 4, 15360, 15360, 32, 8),
                           limits_text("Tier1", "Tier2", "Tier3", "Tier4", "Unique"))
        capabilities = read_capabilities(root)
        self.assertEqual(capabilities.terrain_id, "chernarusplus")
        self.assertEqual(capabilities.header.value_bits, 8)
        self.assertEqual(capabilities.holdable_tiers, ("Tier1", "Tier2", "Tier3", "Tier4", "Unique"))
        self.assertEqual(capabilities.unsupported(), [])
        self.assertEqual(capabilities.to_dict()["tier_writes"], {"available": False, "reason": "bounds_not_confirmed"})

    def test_size_mismatch_gives_manual_bounds_only(self) -> None:
        """A file that breaks the A7 formula gives no header, no detected bounds and tier_header_invalid."""
        root = self.mission("broken.enoch", areaflags_bytes(8, 4, 12800, 12800, 32, 4) + b"\0",
                           limits_text("Tier1", "Tier2", "Tier3", "Unique"))
        result = read_capabilities(root).to_dict(Bounds(0.0, 0.0, 12800.0, 12800.0), True)
        self.assertIsNone(result["header"])
        self.assertIsNone(result["detected_bounds"])
        self.assertEqual(result["holdable_tiers"], [])
        self.assertEqual(result["tier_writes"], {"available": False, "reason": TIER_HEADER_INVALID})
        self.assertEqual({item["reason"] for item in result["unsupported"]}, {TIER_HEADER_INVALID})

    def test_missing_files_give_their_reasons(self) -> None:
        """A missing areaflags.map and a missing limits file each give their own reason; the family stays."""
        root = self.mission("custom.deerisle", None, None)
        capabilities = read_capabilities(root)
        self.assertEqual(capabilities.terrain_id, "deerisle")
        self.assertEqual((capabilities.header_reason, capabilities.limits_reason), (AREAFLAGS_MISSING, LIMITS_MISSING))
        self.assertEqual(capabilities.to_dict()["tier_writes"]["reason"], AREAFLAGS_MISSING)
        no_limits = self.mission("only.enoch", areaflags_bytes(8, 4, 12800, 12800, 32, 4), None)
        result = read_capabilities(no_limits).to_dict()
        self.assertIsNotNone(result["detected_bounds"])
        self.assertEqual(result["tier_writes"]["reason"], LIMITS_MISSING)

    def test_invalid_limits_files_give_limits_invalid(self) -> None:
        """Malformed XML, invalid UTF-8, a missing valueflags section and a bad name give limits_invalid."""
        cases = {"malformed": "<lists><valueflags>", "encoding": b"\xff\xfe<lists/>",
                 "no section": "<lists><usageflags/></lists>", "bad name": limits_text("Tier 1")}
        for index, (name, payload) in enumerate(cases.items()):
            root = self.mission(f"case{index}.enoch", None, payload)
            with self.subTest(name), self.assertRaises(TerrainDataError) as caught:
                read_value_flags(root / "cfglimitsdefinition.xml")
            self.assertEqual(caught.exception.reason, LIMITS_INVALID)
        with_bom = self.mission("bom.enoch", None, b"\xef\xbb\xbf" + limits_text("Tier1", "Unique").encode())
        self.assertEqual(read_value_flags(with_bom / "cfglimitsdefinition.xml"), ("Tier1", "Unique"))

    def test_unreadable_file_gives_mission_file_unreadable(self) -> None:
        """An operating-system error other than a missing file is reported as unreadable."""
        root = self.mission("locked.enoch", areaflags_bytes(8, 4, 12800, 12800, 32, 4), limits_text("Tier1"))
        with mock.patch.object(mission_map_terrain, "open_shared", side_effect=PermissionError(13, "denied")):
            with self.assertRaises(TerrainDataError) as caught:
                read_header(root / "areaflags.map")
        self.assertEqual(caught.exception.reason, MISSION_FILE_UNREADABLE)

    def test_row_range_read_uses_d9_indexing(self) -> None:
        """Rows read from the file give each cell (i, j) of a 4-bit layer with odd columns and of an 8-bit layer."""
        for value_bits in (4, 8):
            cells = {(i, j): (i + 2 * j) % 16 for i in range(5) for j in range(6)}
            root = self.mission(f"rows{value_bits}.enoch", areaflags_bytes(5, 6, 50, 60, 16, value_bits, cells), None)
            path = root / "areaflags.map"
            header = read_header(path)
            for first, count in ((0, 6), (1, 3), (5, 1)):
                with self.subTest(value_bits=value_bits, first=first):
                    rows = read_value_rows(path, header, first, count)
                    for j in range(first, first + count):
                        self.assertEqual(list(rows.row(j)), [cells[(i, j)] for i in range(5)])

    def test_row_read_of_a_shortened_file_is_refused(self) -> None:
        """A file that became shorter after its header was read gives tier_header_invalid, not partial rows."""
        root = self.mission("short.enoch", areaflags_bytes(4, 4, 40, 40, 16, 8), None)
        path = root / "areaflags.map"
        header = read_header(path)
        path.write_bytes(path.read_bytes()[:-3])
        with self.assertRaises(TerrainDataError) as caught:
            read_value_rows(path, header, 2, 2)
        self.assertEqual(caught.exception.reason, TIER_HEADER_INVALID)

    def test_reads_use_the_shared_adapter_and_change_nothing(self) -> None:
        """Every read opens through shared_files, and the mission folder keeps its names and bytes."""
        root = self.mission("dayzOffline.enoch", areaflags_bytes(8, 4, 12800, 12800, 32, 4),
                           limits_text("Tier1", "Tier2", "Tier3", "Unique"))
        before = {path.name: path.read_bytes() for path in root.iterdir()}
        opened: list[str] = []

        def tracking_open(path, *arguments, **keywords):
            """Record the opened name, then open through the adapter."""
            opened.append(Path(path).name)
            return shared_files.open_shared(path, *arguments, **keywords)

        with mock.patch.object(mission_map_terrain, "open_shared", side_effect=tracking_open), \
                mock.patch.object(shared_files, "open_shared", wraps=shared_files.open_shared) as adapter:
            capabilities = read_capabilities(root)
            read_value_rows(root / "areaflags.map", capabilities.header, 0, 4)
        self.assertEqual(opened, ["areaflags.map", "areaflags.map"])
        self.assertEqual(adapter.call_count, 3)
        self.assertEqual({path.name: path.read_bytes() for path in root.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()
