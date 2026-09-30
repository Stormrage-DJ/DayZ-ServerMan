"""Lifecycle readiness decoration tests over controlled process snapshots."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.application.server_readiness import (  # noqa: E402
    DEFAULT_STEAM_QUERY_PORT,
    ReadinessLifecycleService,
)
from dayz_serverman.domain.lifecycle import LifecycleSnapshot, ServerReadiness, ServerState  # noqa: E402
from dayz_serverman.domain.models import ManagerSettings  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord  # noqa: E402
from tests.profile_fixtures import profile_payload  # noqa: E402


class FakeLifecycle:
    """Controllable authoritative lifecycle used by the readiness decorator."""

    def __init__(self) -> None:
        """Start stopped and record no lifecycle calls."""
        self.snapshot = LifecycleSnapshot(ServerState.STOPPED)

    def status(self) -> LifecycleSnapshot:
        """Return the current authoritative snapshot."""
        return self.snapshot

    def start(self, profile_id: str, profile_revision: int, settings_revision: int):
        """Publish one manager-owned process."""
        del profile_id, profile_revision, settings_revision
        self.snapshot = LifecycleSnapshot(ServerState.RUNNING_MANAGED, 700)
        return self.snapshot

    def stop(self, settings_revision: int):
        """Publish a verified stopped state."""
        del settings_revision
        self.snapshot = LifecycleSnapshot(ServerState.STOPPED)
        return self.snapshot

    def restart(self, profile_id: str, profile_revision: int, settings_revision: int):
        """Publish a replacement manager-owned process."""
        del profile_id, profile_revision, settings_revision
        self.snapshot = LifecycleSnapshot(ServerState.RUNNING_MANAGED, 701)
        return self.snapshot

    def shutdown_safe(self) -> bool:
        """Report safety only when the authoritative state is stopped."""
        return self.snapshot.state == ServerState.STOPPED


class FakeProbe:
    """Scripted readiness probe that records selected query ports."""

    def __init__(self) -> None:
        """Start not ready with no recorded calls."""
        self.ready = False
        self.ports: list[int] = []

    def is_ready(self, port: int) -> bool:
        """Record the port and return the scripted result."""
        self.ports.append(port)
        return self.ready


class FakeMissionProbe:
    """Scripted mission probe that records the selected runtime directory."""

    def __init__(self) -> None:
        """Start without mission-ready evidence."""
        self.ready = False
        self.calls: list[tuple[Path | None, int]] = []

    def is_ready(self, directory: Path | None, started_after_ns: int) -> bool:
        """Record the evidence boundary and return the scripted result."""
        self.calls.append((directory, started_after_ns))
        return self.ready


class ServerReadinessTests(unittest.TestCase):
    """Readiness states, timing, port resolution, and cleanup behavior."""

    def setUp(self) -> None:
        """Create one profile, config, lifecycle, probe, and controlled clock."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_readiness_")
        self.root = Path(self.temporary.name)
        config = self.root / "Config Files" / "serverDZ.cfg"
        config.parent.mkdir(parents=True)
        config.write_text('hostname = "test";\nsteamQueryPort = 2405;\n', encoding="utf-8")
        self.profile = ProfileRecord(7, ProfileInput.parse(profile_payload()))
        self.settings = ManagerSettings(
            4, str(self.root), str(self.root / "DayZServer_x64.exe"),
            None, None, None, None, MappingProxyType({}),
        )
        self.lifecycle = FakeLifecycle()
        self.probe = FakeProbe()
        self.mission_probe = FakeMissionProbe()
        self.now = 1_000.0
        self.service = ReadinessLifecycleService(
            self.lifecycle,
            _Profiles(self.profile),
            _Settings(self.settings),
            self.probe,
            self.mission_probe,
            clock=lambda: self.now,
            wall_clock_ns=lambda: 50_000,
        )

    def tearDown(self) -> None:
        """Remove the isolated configuration tree."""
        self.temporary.cleanup()

    def test_starting_ready_and_unresponsive_preserve_managed_process(self) -> None:
        """Probe evidence refines readiness without changing process ownership."""
        starting = self.service.start("livonia-main", 7, 4)
        self.assertEqual((starting.state, starting.readiness, starting.query_port), (
            ServerState.RUNNING_MANAGED, ServerReadiness.STARTING, 2405,
        ))
        self.now += 121
        unresponsive = self.service.status()
        self.assertEqual(unresponsive.readiness, ServerReadiness.UNRESPONSIVE)
        self.probe.ready = True
        query_only = self.service.status()
        self.assertEqual(query_only.readiness, ServerReadiness.UNRESPONSIVE)
        self.mission_probe.ready = True
        ready = self.service.status()
        self.assertEqual(ready.readiness, ServerReadiness.READY)
        self.assertEqual(self.probe.ports, [2405, 2405, 2405, 2405])
        self.assertEqual(self.mission_probe.calls[-1][1], 50_000)

    def test_missing_query_port_uses_legacy_default(self) -> None:
        """Legacy configs without steamQueryPort remain readiness-compatible."""
        config = self.root / "Config Files" / "serverDZ.cfg"
        config.write_text('hostname = "legacy";\n', encoding="utf-8")
        result = self.service.start("livonia-main", 7, 4)
        self.assertEqual(result.query_port, DEFAULT_STEAM_QUERY_PORT)
        self.assertEqual(self.probe.ports, [DEFAULT_STEAM_QUERY_PORT])

    def test_verified_stop_clears_readiness_target(self) -> None:
        """A stopped server cannot leak readiness into a later unrelated process."""
        self.service.start("livonia-main", 7, 4)
        self.assertEqual(self.service.stop(4).state, ServerState.STOPPED)
        self.lifecycle.snapshot = LifecycleSnapshot(ServerState.RUNNING_MANAGED, 999)
        status = self.service.status()
        self.assertIsNone(status.readiness)
        self.assertIsNone(status.query_port)


class _Profiles:
    """Return one fixed profile to the readiness decorator."""

    def __init__(self, profile: ProfileRecord) -> None:
        """Store the profile fixture."""
        self.profile = profile

    def read(self, profile_id: str) -> ProfileRecord:
        """Return the fixture after confirming its identifier."""
        if profile_id != self.profile.values.profile_id:
            raise LookupError(profile_id)
        return self.profile


class _Settings:
    """Return one fixed settings record to the readiness decorator."""

    def __init__(self, settings: ManagerSettings) -> None:
        """Store the settings fixture."""
        self.settings = settings

    def load(self) -> ManagerSettings:
        """Return the settings fixture."""
        return self.settings


if __name__ == "__main__":
    unittest.main()
