"""Decorate process lifecycle evidence with bounded DayZ query readiness."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Callable, Protocol

from ..domain.lifecycle import LifecycleSnapshot, ServerReadiness, ServerState
from ..repositories.server_configuration import load_server_configuration
from .lifecycle import ServerLifecycleService
from .profiles import ProfileService
from .settings import SettingsService


# DayZ uses this query port when the legacy server config omits steamQueryPort
DEFAULT_STEAM_QUERY_PORT = 27_016
# Slow installations remain in a non-terminal starting state for two minutes
STARTUP_GRACE_SECONDS = 120.0


class ReadinessProbe(Protocol):
    """Check whether one local Steam query port answers."""

    def is_ready(self, port: int) -> bool:
        """Return whether the endpoint provides a valid information reply."""
        ...


class MissionReadinessProbe(Protocol):
    """Check whether the current DayZ mission accepts player connections."""

    def is_ready(self, directory: Path | None, started_after_ns: int) -> bool:
        """Return whether current runtime evidence confirms mission readiness."""
        ...


@dataclass(frozen=True)
class ReadinessTarget:
    """In-memory endpoint and monotonic origin for one managed launch."""

    query_port: int
    started_at: float
    started_after_ns: int
    rpt_directory: Path | None
    mission_ready: bool = False
    # Profile that the launch was requested for; None only for a target built without one
    profile_id: str | None = None


class ReadinessLifecycleService:
    """Preserve lifecycle control while adding current application readiness."""

    def __init__(
        self,
        lifecycle: ServerLifecycleService,
        profiles: ProfileService,
        settings: SettingsService,
        probe: ReadinessProbe,
        mission_probe: MissionReadinessProbe,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        """Bind the controlled lifecycle, query resolver, probe, and clock."""
        self._lifecycle = lifecycle
        self._profiles = profiles
        self._settings = settings
        self._probe = probe
        self._mission_probe = mission_probe
        self._clock = clock
        self._wall_clock_ns = wall_clock_ns
        self._target: ReadinessTarget | None = None
        self._lock = threading.RLock()

    def status(self) -> LifecycleSnapshot:
        """Return process evidence decorated with current query readiness."""
        return self._decorate(self._lifecycle.status())

    def shutdown_safe(self) -> bool:
        """Delegate shutdown safety to the authoritative lifecycle service."""
        return self._lifecycle.shutdown_safe()

    def start(
        self, profile_id: str, expected_profile_revision: int,
        expected_settings_revision: int,
    ) -> LifecycleSnapshot:
        """Start DayZ, remember its query target, and return initial readiness."""
        # Let the lifecycle service validate and establish process ownership first
        started_after_ns = self._wall_clock_ns()
        snapshot = self._lifecycle.start(
            profile_id, expected_profile_revision, expected_settings_revision,
        )
        self._remember_target(profile_id, started_after_ns)
        return self._decorate(snapshot)

    def stop(self, expected_settings_revision: int) -> LifecycleSnapshot:
        """Stop DayZ and clear readiness only after the process is verified absent."""
        snapshot = self._lifecycle.stop(expected_settings_revision)
        if snapshot.state == ServerState.STOPPED:
            self._clear_target()
        return snapshot

    def restart(
        self, profile_id: str, expected_profile_revision: int,
        expected_settings_revision: int,
    ) -> LifecycleSnapshot:
        """Restart DayZ and replace readiness timing with the new launch target."""
        started_after_ns = self._wall_clock_ns()
        snapshot = self._lifecycle.restart(
            profile_id, expected_profile_revision, expected_settings_revision,
        )
        self._remember_target(profile_id, started_after_ns)
        return self._decorate(snapshot)

    def _decorate(self, snapshot: LifecycleSnapshot) -> LifecycleSnapshot:
        """Attach readiness without weakening the process ownership conclusion."""
        # Clear stale launch context after the authoritative process disappears
        if snapshot.state == ServerState.STOPPED:
            self._clear_target()
            return snapshot
        with self._lock:
            target = self._target
        if target is None:
            return snapshot
        # Name the profile and the start time of the launch that this manager made
        if snapshot.state in (ServerState.RUNNING_MANAGED, ServerState.STOPPING):
            snapshot = replace(
                snapshot, profile_id=target.profile_id,
                started_at=_utc_text(target.started_after_ns),
            )
        if snapshot.state != ServerState.RUNNING_MANAGED:
            return snapshot
        query_ready = self._probe.is_ready(target.query_port)
        mission_ready = target.mission_ready or self._mission_probe.is_ready(
            target.rpt_directory,
            target.started_after_ns,
        )
        if mission_ready and not target.mission_ready:
            target = replace(target, mission_ready=True)
            with self._lock:
                if self._target is not None and self._target.started_at == target.started_at:
                    self._target = target
        # A query response proves discovery only; players also need a loaded mission
        if query_ready and mission_ready:
            readiness = ServerReadiness.READY
        elif self._clock() - target.started_at < STARTUP_GRACE_SECONDS:
            readiness = ServerReadiness.STARTING
        else:
            readiness = ServerReadiness.UNRESPONSIVE
        return replace(snapshot, readiness=readiness, query_port=target.query_port)

    def _remember_target(self, profile_id: str, started_after_ns: int) -> None:
        """Resolve and store the launched profile's query endpoint."""
        query_port = self._query_port(profile_id)
        rpt_directory = self._rpt_directory(profile_id)
        with self._lock:
            self._target = ReadinessTarget(
                query_port,
                self._clock(),
                started_after_ns,
                rpt_directory,
                profile_id=profile_id,
            )

    def _query_port(self, profile_id: str) -> int:
        """Return the configured Steam query port or the DayZ legacy default."""
        try:
            # Resolve the profile config inside the configured DayZ installation
            profile = self._profiles.read(profile_id)
            settings = self._settings.load()
            if settings.dayz_root is None:
                return DEFAULT_STEAM_QUERY_PORT
            root = Path(settings.dayz_root).resolve(strict=False)
            config = (root / profile.values.server_config).resolve(strict=False)
            config.relative_to(root)
            snapshot = load_server_configuration(config)
            value = snapshot.values.get("steamQueryPort")
            return value if isinstance(value, int) else DEFAULT_STEAM_QUERY_PORT
        except (OSError, RuntimeError, ValueError):
            # Readiness must not turn a successful launch into an operation failure
            return DEFAULT_STEAM_QUERY_PORT

    def _rpt_directory(self, profile_id: str) -> Path | None:
        """Resolve the selected profile's constrained runtime directory."""
        try:
            profile = self._profiles.read(profile_id)
            settings = self._settings.load()
            relative = profile.values.runtime_profile
            if settings.dayz_root is None or relative is None:
                return None
            root = Path(settings.dayz_root).resolve(strict=False)
            directory = root.joinpath(*PureWindowsPath(relative).parts).resolve(strict=False)
            directory.relative_to(root)
            return directory if directory.is_dir() else None
        except (OSError, RuntimeError, ValueError):
            return None

    def _clear_target(self) -> None:
        """Forget readiness context after a verified stop."""
        with self._lock:
            self._target = None


def _utc_text(wall_clock_ns: int) -> str:
    """Return a wall-clock time in nanoseconds as ISO-8601 UTC text with milliseconds."""
    moment = datetime.fromtimestamp(wall_clock_ns / 1_000_000_000, UTC)
    return moment.isoformat(timespec="milliseconds")
