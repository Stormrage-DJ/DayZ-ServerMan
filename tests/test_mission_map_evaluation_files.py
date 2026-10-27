"""Verify exact-byte snapshots, shared-read access and source freshness failures."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dayz_serverman.domain.mission_map_terrain import TerrainDataError
from dayz_serverman.repositories import mission_map_evaluation
from dayz_serverman.repositories.mission_map_evaluation import read_snapshot
from tests.test_mission_map_terrain import areaflags_bytes
from tests.test_mission_map_terrain_files import limits_text


class SnapshotTests(unittest.TestCase):
    """Keep every file action inside an isolated temporary mission."""

    def setUp(self) -> None:
        """Create a synthetic mission and register deterministic cleanup."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.area = areaflags_bytes(7, 6, 1000, 1000, 16, 4, {(0, 0): 1, (6, 5): 8})
        (self.root / "areaflags.map").write_bytes(self.area)
        (self.root / "cfglimitsdefinition.xml").write_text(limits_text("Tier1", "Tier2", "Tier3", "Tier4"), encoding="utf-8")

    def test_exact_snapshot_and_both_file_freshness_checks(self) -> None:
        """The hashed bytes supply header, flags and values; either changed file makes the snapshot stale."""
        with patch.object(mission_map_evaluation, "read_bytes_shared", wraps=mission_map_evaluation.read_bytes_shared) as read:
            snapshot = read_snapshot(self.root)
            self.assertEqual(read.call_count, 2)
            self.assertTrue(snapshot.is_fresh())
            self.assertEqual(read.call_count, 4)
        self.assertEqual(snapshot.areaflags_sha256, hashlib.sha256(self.area).hexdigest())
        self.assertEqual(snapshot.values.value(0, 0), 1)
        self.assertEqual(snapshot.values.value(6, 5), 8)
        for path in (self.root / "areaflags.map", self.root / "cfglimitsdefinition.xml"):
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            self.assertFalse(snapshot.is_fresh())
            self.assertEqual(snapshot.values.value(6, 5), 8)
            path.write_bytes(original)

    def test_invalid_and_unreadable_sources(self) -> None:
        """Invalid headers and unreadable files expose accepted failure reasons."""
        (self.root / "areaflags.map").write_bytes(b"bad")
        with self.assertRaises(TerrainDataError) as caught:
            read_snapshot(self.root)
        self.assertEqual(caught.exception.reason, "tier_header_invalid")
        (self.root / "areaflags.map").unlink()
        with self.assertRaises(TerrainDataError) as caught:
            read_snapshot(self.root)
        self.assertEqual(caught.exception.reason, "mission_file_unreadable")


if __name__ == "__main__":
    unittest.main()
