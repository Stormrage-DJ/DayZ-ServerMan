"""One newest-build run: preflight, the anonymous app-information command, and its exit table."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from ..adapters.windows.steamcmd import SteamCmdRunResult
from ..adapters.windows.steamcmd_paths import SteamCmdExecutable
from ..domain.models import ManagerSettings
from ..domain.server_build import RUN_DEADLINE_SECONDS, SERVER_APP_ID, BranchFact, BuildCheckRecord
from ..repositories.steam_app_info import AppInfoUnreadable, connected, read_branches


class ExecutablePreflightPort(Protocol):
    """Validation of the SteamCMD root and executable only."""

    # Return the two paths with their identities; raise when they are unusable
    def inspect_executable(self, settings: ManagerSettings) -> SteamCmdExecutable: ...


class AppInfoPort(Protocol):
    """One anonymous app-information run under owned supervision."""

    # Revalidate, launch, supervise and return the bounded output
    def run_app_info(
        self, paths: SteamCmdExecutable, cancellation_requested: Callable[[], bool],
    ) -> SteamCmdRunResult: ...


class BuildCachePort(Protocol):
    """Disposable persistence of the build check record; neither call raises."""

    # Return the stored record, or None when nothing usable is stored
    def load(self) -> BuildCheckRecord | None: ...

    # Write the record; False reports a failed write
    def save(self, record: BuildCheckRecord) -> bool: ...


def start_thread(work: Callable[[], None]) -> threading.Thread:
    """Run one check on its own daemon thread."""
    thread = threading.Thread(target=work, name="dayz-serverman-server-build-check", daemon=True)
    thread.start()
    return thread


@dataclass(frozen=True)
class RunOutcome:
    """Result of one run: branch facts, or a failure code; or nothing to record."""

    branches: dict[str, BranchFact] = field(default_factory=dict)
    error_code: str | None = None
    exit_code: int | None = None
    duration_ms: int = 0
    # No attempt is recorded: SteamCMD is not set up, or a shutdown cancelled the run
    not_configured: bool = False
    shutdown: bool = False
    # The SteamCMD process tree was not proven gone; the guard is poisoned
    unproven: bool = False


def steamcmd_configured(settings: ManagerSettings) -> bool:
    """Return whether the SteamCMD root and executable are both set."""
    return bool(settings.steamcmd_root) and bool(settings.steamcmd_executable)


def run_newest_build_check(
    settings: ManagerSettings, preflight: ExecutablePreflightPort, app_info: AppInfoPort,
    shutdown: threading.Event, monotonic: Callable[[], float],
    deadline_seconds: float = RUN_DEADLINE_SECONDS,
) -> RunOutcome:
    """Run the command once and classify its result with the exit table of 14.4."""
    if not steamcmd_configured(settings):
        return RunOutcome(not_configured=True)
    try:
        paths = preflight.inspect_executable(settings)
    except (OSError, RuntimeError, ValueError):
        return RunOutcome(error_code="STEAMCMD_UNAVAILABLE")
    started = monotonic()

    def cancellation_requested() -> bool:
        """End the run at shutdown or when the deadline has passed."""
        return shutdown.is_set() or monotonic() - started >= deadline_seconds

    try:
        run = app_info.run_app_info(paths, cancellation_requested)
    except (OSError, RuntimeError, ValueError):
        # A changed path identity before the launch, or a launch that failed
        return RunOutcome(error_code="STEAMCMD_UNAVAILABLE")
    duration = int((monotonic() - started) * 1000)
    return classify_run(run, shutdown.is_set(), duration)


def classify_run(run: SteamCmdRunResult, shutdown: bool, duration_ms: int) -> RunOutcome:
    """Map one supervised run to an outcome; first match wins."""
    if not run.termination_confirmed:
        return RunOutcome(error_code="STEAMCMD_EXIT_UNPROVEN", duration_ms=duration_ms, unproven=True)
    if run.cancelled and shutdown:
        return RunOutcome(shutdown=True, duration_ms=duration_ms)
    if run.cancelled:
        return RunOutcome(error_code="TIMEOUT", duration_ms=duration_ms)
    if run.exit_code != 0:
        return RunOutcome(error_code="STEAMCMD_FAILED", exit_code=run.exit_code, duration_ms=duration_ms)
    if not connected(run.lines):
        return RunOutcome(error_code="NOT_CONNECTED", exit_code=0, duration_ms=duration_ms)
    try:
        branches = read_branches(run.lines, SERVER_APP_ID)
    except AppInfoUnreadable:
        return RunOutcome(error_code="OUTPUT_UNREADABLE", exit_code=0, duration_ms=duration_ms)
    return RunOutcome(branches=branches, exit_code=0, duration_ms=duration_ms)
