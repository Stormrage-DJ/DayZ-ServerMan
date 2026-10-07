"""Server lifecycle orchestration over process-control ports."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from pathlib import Path

from ..domain.lifecycle import (
    LaunchEvidence,
    LifecycleFailure,
    LifecycleSnapshot,
    ServerState,
    canonical_process_path,
)
from ..domain.models import ManagerSettings, RecordUnavailable
from ..domain.profiles import ProfileValidationError
from ..repositories.lifecycle_state import LifecycleStateRepository
from .arguments import build_launch_command
from .lifecycle_checks import configured_executable, require_profile, require_settings, require_state
from .lifecycle_ownership import LaunchOwnership
from .lifecycle_ports import (
    GracefulStopPort,
    InstallationMutexPort,
    LaunchRequest,
    ProcessInventoryPort,
    ProcessLauncherPort,
)
from .profiles import ProfileService
from .reconciliation import reconcile_server
from .settings import SettingsService, SettingsValidationError


class ServerLifecycleService:
    """Coordinate start, stop, and restart against reconciled process evidence."""

    def __init__(
        self,
        settings: SettingsService,
        profiles: ProfileService,
        inventory: ProcessInventoryPort,
        launcher: ProcessLauncherPort,
        stopper: GracefulStopPort,
        mutex: InstallationMutexPort,
        state: LifecycleStateRepository,
        logs_root: Path,
        ownership: LaunchOwnership | None = None,
        wall_clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        """Bind lifecycle collaborators and initialize the shared evidence state."""
        self._settings = settings
        self._profiles = profiles
        self._inventory = inventory
        self._launcher = launcher
        self._stopper = stopper
        self._mutex = mutex
        self._state = state
        self._logs_root = logs_root
        # The evidence and phase fields shared across bridge threads live in one ownership holder
        self._ownership = ownership if ownership is not None else LaunchOwnership()
        # Wall clock of the ownership record's launch time (9.2)
        self._wall_clock_ns = wall_clock_ns

    def status(self) -> LifecycleSnapshot:
        """Return the reconciled server state, failing closed when context is unreadable."""
        # Honor an in-flight transition phase before reconciling
        phase, evidence = self._ownership.snapshot()
        if phase is not None:
            return LifecycleSnapshot(phase)
        # Report recovery-required when state or settings cannot be read
        try:
            self._state.ensure_writable()
            settings = self._settings.load()
            executable = configured_executable(settings)
        except (LifecycleFailure, RecordUnavailable, SettingsValidationError):
            return LifecycleSnapshot(ServerState.UNKNOWN, diagnostic_code="RECOVERY_REQUIRED")
        # Without evidence of its own, the session adopts a server that the ownership record proves (A6)
        if evidence is None:
            evidence = self._ownership.adopt(executable, self._inventory)
        # Compare recorded evidence with the live process inventory
        return reconcile_server(
            executable,
            self._inventory,
            evidence,
            self._launcher.retains_handle,
        )

    def shutdown_safe(self) -> bool:
        """Return True when no transition is in flight and the server is stopped."""
        # A cached transition or evidence means shutdown is not safe yet
        phase, evidence = self._ownership.snapshot()
        if phase is not None:
            return False
        if evidence is None:
            return True
        # OD3: an adopted server does not hold the window open; it stays adoptable after the close
        if self._ownership.adopted_launch() is not None:
            return True
        # Fall back to a full reconciling status check
        return self.status().state == ServerState.STOPPED

    def start(
        self,
        profile_id: str,
        expected_profile_revision: int,
        expected_settings_revision: int,
        *,
        before_change: Callable[[], None] | None = None,
    ) -> LifecycleSnapshot:
        """Start the server after verifying settings and taking the install mutex.

        `before_change` runs right before the launch, the first change (6.5 marker).
        """
        # Require current settings with a configured DayZ root
        settings = require_settings(self._settings, expected_settings_revision)
        assert settings.dayz_root is not None
        # Serialize start against other lifecycle control through the mutex
        with self._mutex.guard(settings.dayz_root):
            return self._start_locked(profile_id, expected_profile_revision, settings, before_change)

    def stop(
        self, expected_settings_revision: int, *, before_change: Callable[[], None] | None = None,
    ) -> LifecycleSnapshot:
        """Stop the managed server after verifying settings and the mutex.

        `before_change` runs right before the stop request, the first change (6.5 marker).
        """
        settings = require_settings(self._settings, expected_settings_revision)
        assert settings.dayz_root is not None
        # Serialize stop against other lifecycle control through the mutex
        with self._mutex.guard(settings.dayz_root):
            return self._stop_locked(settings, before_change)

    def restart(
        self,
        profile_id: str,
        expected_profile_revision: int,
        expected_settings_revision: int,
        *,
        before_change: Callable[[], None] | None = None,
    ) -> LifecycleSnapshot:
        """Restart the managed server under a single install mutex.

        `before_change` runs right before the stop request, the first change (6.5 marker).
        """
        settings = require_settings(self._settings, expected_settings_revision)
        assert settings.dayz_root is not None
        with self._mutex.guard(settings.dayz_root):
            # Verify the profile before stopping anything
            require_profile(self._profiles, profile_id, expected_profile_revision)
            self._stop_locked(settings, before_change)
            # Re-read settings so the start uses fresh revisions
            refreshed = require_settings(self._settings, expected_settings_revision)
            return self._start_locked(profile_id, expected_profile_revision, refreshed)

    def _start_locked(
        self,
        profile_id: str,
        expected_profile_revision: int,
        settings: ManagerSettings,
        before_change: Callable[[], None] | None = None,
    ) -> LifecycleSnapshot:
        """Start under the mutex and confirm ownership through reconciliation."""
        # Require a writable state file and a fully stopped server
        self._state.ensure_writable()
        current = self._reconcile(settings)
        require_state(current, ServerState.STOPPED, "start")
        # Resolve the profile and verify its executable matches the configured one
        profile = require_profile(self._profiles, profile_id, expected_profile_revision)
        assert settings.dayz_root is not None
        try:
            command = build_launch_command(profile, settings.dayz_root)
        except ProfileValidationError as error:
            raise LifecycleFailure("PATH_INVALID", str(error)) from error
        expected_executable = configured_executable(settings)
        if canonical_process_path(command.executable) != canonical_process_path(expected_executable):
            raise LifecycleFailure(
                "PATH_INVALID",
                "The profile executable does not match the configured DayZ executable.",
            )
        # Every check passed: the launch is the first change (6.5 marker)
        if before_change is not None:
            before_change()
        # Launch with a fresh token that pins the process to this manager
        launch_token = secrets.token_urlsafe(24)
        started_after_ns = self._wall_clock_ns()
        self._ownership.set_phase(ServerState.STARTING)
        try:
            receipt = self._launcher.launch(
                LaunchRequest(
                    command.argv,
                    command.working_directory,
                    self._logs_root / "dayz-server.log",
                    launch_token,
                )
            )
            evidence = LaunchEvidence(
                receipt.pid,
                canonical_process_path(command.executable),
                receipt.creation_time_ns,
                launch_token,
                receipt.handle_token,
            )
            self._ownership.set(evidence)
            self._state.record_launch(evidence)
            # The ownership record lets a later manager session adopt this server (A6)
            self._ownership.record_launch(evidence, profile_id, started_after_ns)
            # Confirm managed ownership before reporting success
            confirmed = self._reconcile(settings)
            if confirmed.state != ServerState.RUNNING_MANAGED:
                raise LifecycleFailure(
                    "PROCESS_STATE_UNKNOWN",
                    "The launched server ownership could not be verified.",
                    recovery_required=True,
                )
            return confirmed
        except LifecycleFailure:
            raise
        except OSError as error:
            # Surface launch OS errors as retryable failures
            raise LifecycleFailure(
                "LAUNCH_FAILED",
                "The DayZ server could not be started.",
                retryable=True,
            ) from error
        finally:
            # Clear the transition phase even when launch fails
            self._ownership.set_phase(None)

    def _stop_locked(
        self, settings: ManagerSettings, before_change: Callable[[], None] | None = None,
    ) -> LifecycleSnapshot:
        """Stop under the mutex and verify the stopped state before recording it."""
        # Require a writable state file and a managed running server
        self._state.ensure_writable()
        current = self._reconcile(settings)
        require_state(current, ServerState.RUNNING_MANAGED, "stop")
        # Require proven ownership evidence before requesting a graceful stop
        evidence = self._ownership.current()
        if evidence is None:
            raise LifecycleFailure("EXTERNAL_PROCESS", "Server ownership is not proven.")
        # Every check passed: the stop request is the first change (6.5 marker)
        if before_change is not None:
            before_change()
        self._ownership.set_phase(ServerState.STOPPING)
        try:
            self._stopper.request_stop(evidence)
            confirmed = self._reconcile(settings)
            if confirmed.state != ServerState.STOPPED:
                raise LifecycleFailure(
                    "PROCESS_STATE_UNKNOWN",
                    "Graceful stop did not produce a verified stopped state.",
                    retryable=True,
                )
            # Record the stop, then release the retained handle
            self._state.record_stopped()
            self._ownership.clear()
            if evidence.handle_token is not None:
                self._launcher.release_handle(evidence.handle_token)
            self._ownership.set(None)
            return confirmed
        finally:
            # Clear the transition phase even when the stop fails
            self._ownership.set_phase(None)

    def _reconcile(self, settings: ManagerSettings) -> LifecycleSnapshot:
        """Reconcile the configured executable against live processes and evidence."""
        executable = configured_executable(settings)
        phase, evidence = self._ownership.snapshot()
        # Outside a transition, a session without evidence adopts the server of the record (A6)
        if evidence is None and phase is None:
            evidence = self._ownership.adopt(executable, self._inventory)
        return reconcile_server(executable, self._inventory, evidence, self._launcher.retains_handle)
