"""Tests for player-ready evidence in DayZ RPT files."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.adapters.mission_readiness import RptMissionReadinessProbe  # noqa: E402


class MissionReadinessProbeTests(unittest.TestCase):
    """Select only current RPT evidence and require the player-ready marker."""

    def setUp(self) -> None:
        """Create an isolated runtime profile directory."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_mission_ready_")
        self.runtime = Path(self.temporary.name)
        self.probe = RptMissionReadinessProbe()

    def tearDown(self) -> None:
        """Remove the isolated runtime profile directory."""
        self.temporary.cleanup()

    def test_current_rpt_requires_player_connect_marker(self) -> None:
        """Do not confuse ordinary startup output with a playable mission."""
        report = self.runtime / "DayZServer_x64_2026-09-30_15-00-00.RPT"
        report.write_bytes(b"Connected to Steam\nSteam policy response\n")
        started_after = report.stat().st_mtime_ns

        self.assertFalse(self.probe.is_ready(self.runtime, started_after))
        report.write_bytes(b"Connected to Steam\nPlayer connect enabled\n")
        self.assertTrue(self.probe.is_ready(self.runtime, started_after))

    def test_stale_ready_report_is_ignored(self) -> None:
        """A previous launch cannot make a new process appear ready."""
        report = self.runtime / "DayZServer_x64_2026-09-30_14-00-00.RPT"
        report.write_bytes(b"Player connect enabled\n")
        stale_time = report.stat().st_mtime_ns
        os.utime(report, ns=(stale_time - 10_000, stale_time - 10_000))

        self.assertFalse(self.probe.is_ready(self.runtime, stale_time))

    def test_marker_across_read_boundary_is_detected(self) -> None:
        """Chunk boundaries cannot hide valid mission-ready evidence."""
        report = self.runtime / "DayZServer_x64_2026-09-30_16-00-00.RPT"
        prefix = b"x" * (64 * 1024 - 8)
        report.write_bytes(prefix + b"Player connect enabled\n")

        self.assertTrue(self.probe.is_ready(self.runtime, report.stat().st_mtime_ns))


if __name__ == "__main__":
    unittest.main()
