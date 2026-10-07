"""Decorate process lifecycle evidence with bounded DayZ query readiness."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Callable, Protocol

from ..domain.lifecycle import LifecycleSnapshot, ServerReadiness, ServerState
from ..domain.online_players import InformationAnswer
from .external_players import DEFAULT_STEAM_QUERY_PORT, ExternalServerMatch, read_profile_endpoint
from .lifecycle import ServerLifecycleService
from .lifecycle_ownership import LaunchOwnership
from .profiles import ProfileService
from .settings import SettingsService


# Slow installations remain in a non-terminal starting state for two minutes
STARTUP_GRACE_SECONDS = 120.0


class ReadinessProbe(Protocol):
    """Check whether one local Steam query port answers, and read the player count of that answer."""

    def information(self, port: int) -> InformationAnswer | None:
        """Return the valid information reply of the endpoint, or None when it gave none."""
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
        external: ExternalServerMatch | None = None,
        ownership: LaunchOwnership | None = None,
    ) -> None:
        """Bind the controlled lifecycle, query resolver, probe, clocks, outside-manager match and ownership record."""
        self._lifecycle = lifecycle
        self._profiles = profiles
        self._settings = settings
        self._probe = probe
        self._mission_probe = mission_probe
        self._clock = clock
        self._wall_clock_ns = wall_clock_ns
        self._external = external
        # Shared with the lifecycle service: stages the record's readiness hint, restores an adopted target (A6)
        self._ownership = ownership
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
        expected_settings_revision: int, *, before_change: Callable[[], None] | None = None,
    ) -> LifecycleSnapshot:
        """Start DayZ, remember its query target, and return initial readiness."""
        # Let the lifecycle service validate and establish process ownership first
        started_after_ns = self._wall_clock_ns()
        self._stage_readiness(profile_id)
        snapshot = self._lifecycle.start(
            profile_id, expected_profile_revision, expected_settings_revision, **_marker(before_change),
        )
        self._remember_target(profile_id, started_after_ns)
        return self._decorate(snapshot)

    def stop(
        self, expected_settings_revision: int, *, before_change: Callable[[], None] | None = None,
    ) -> LifecycleSnapshot:
        """Stop DayZ and clear readiness only after the process is verified absent."""
        snapshot = self._lifecycle.stop(expected_settings_revision, **_marker(before_change))
        if snapshot.state == ServerState.STOPPED:
            self._clear_target()
        return snapshot

    def restart(
        self, profile_id: str, expected_profile_revision: int,
        expected_settings_revision: int, *, before_change: Callable[[], None] | None = None,
    ) -> LifecycleSnapshot:
        """Restart DayZ and replace readiness timing with the new launch target."""
        started_after_ns = self._wall_clock_ns()
        self._stage_readiness(profile_id)
        snapshot = self._lifecycle.restart(
            profile_id, expected_profile_revision, expected_settings_revision, **_marker(before_change),
        )
        self._remember_target(profile_id, started_after_ns)
        return self._decorate(snapshot)

    def _decorate(self, snapshot: LifecycleSnapshot) -> LifecycleSnapshot:
        """Attach readiness without weakening the process ownership conclusion."""
        # Clear stale launch context after the authoritative process disappears
        if snapshot.state == ServerState.STOPPED:
            self._clear_target()
            return snapshot
        # A server outside the manager gains at most a player count; everything else stays as observed
        if snapshot.state == ServerState.RUNNING_EXTERNAL:
            return self._with_external_count(snapshot)
        with self._lock:
            target = self._target
        if target is None and snapshot.state == ServerState.RUNNING_MANAGED:
            target = self._restore_target()
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
        # One information answer proves the query endpoint and carries the player count (D18)
        answer = self._probe.information(target.query_port)
        query_ready = answer is not None
        count = answer.count if answer is not None else None
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
        return replace(
            snapshot, readiness=readiness, query_port=target.query_port,
            players=count.players if count is not None else None,
            max_players=count.max_players if count is not None else None,
        )

    def _with_external_count(self, snapshot: LifecycleSnapshot) -> LifecycleSnapshot:
        """Add the count of a server outside the manager when it answers as the selected profile (D18).

        Readiness, query port, profile and state stay as observed, so no guard or control changes.
        """
        match = self._external.find() if self._external is not None else None
        count = match[1].count if match is not None else None
        if count is None:
            return snapshot
        return replace(snapshot, players=count.players, max_players=count.max_players)

    def running_query_port(self) -> int | None:
        """Return the query port whose players may be read, or None when there is none.

        For a server that this manager runs it is the launch's port, found without a readiness query. For a
        server outside the manager it is the selected profile's port, and only after an A2S_INFO answer
        that matches that profile (D18 extension).
        """
        state = self._lifecycle.status().state
        if state == ServerState.RUNNING_EXTERNAL:
            match = self._external.find() if self._external is not None else None
            return match[0] if match is not None else None
        if state != ServerState.RUNNING_MANAGED:
            return None
        with self._lock:
            target = self._target
        if target is None:
            target = self._restore_target()
        return target.query_port if target is not None else None

    def _stage_readiness(self, profile_id: str) -> None:
        """Give the ownership record the launch's readiness hint before the start (9.2)."""
        if self._ownership is not None:
            self._ownership.stage_readiness(profile_id, self._query_port(profile_id), self._rpt_directory(profile_id))

    def _restore_target(self) -> ReadinessTarget | None:
        """Build the readiness target of an adopted server from its record (9.4), or return None.

        The profile gives D11 the running profile in every session; the grace counts
        from the recorded launch, not from the adoption.
        """
        launch = self._ownership.adopted_launch() if self._ownership is not None else None
        if launch is None:
            return None
        query_port = launch.query_port if launch.query_port is not None else self._query_port(launch.profile_id)
        rpt_directory = (Path(launch.rpt_directory) if launch.rpt_directory is not None
                         else self._rpt_directory(launch.profile_id))
        elapsed = max(0.0, (self._wall_clock_ns() - launch.started_after_ns) / 1_000_000_000)
        restored = ReadinessTarget(
            query_port, self._clock() - elapsed, launch.started_after_ns, rpt_directory, profile_id=launch.profile_id,
        )
        with self._lock:
            # A launch of this session that set its own target meanwhile wins
            if self._target is None:
                self._target = restored
            return self._target

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
            return read_profile_endpoint(self._profiles, self._settings, profile_id).query_port
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


def _marker(before_change: Callable[[], None] | None) -> dict[str, Callable[[], None]]:
    """Forward a first-change marker (6.5) only when one is given, so callers without one see no keyword."""
    return {} if before_change is None else {"before_change": before_change}


def _utc_text(wall_clock_ns: int) -> str:
    """Return a wall-clock time in nanoseconds as ISO-8601 UTC text with milliseconds."""
    moment = datetime.fromtimestamp(wall_clock_ns / 1_000_000_000, UTC)
    return moment.isoformat(timespec="milliseconds")
