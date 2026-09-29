"""Server lifecycle orchestration over process-control ports."""

from __future__ import annotations

import secrets
import threading
from pathlib import Path

from ..domain.lifecycle import (
    LaunchEvidence,
    LifecycleFailure,
    LifecycleSnapshot,
    ServerState,
    canonical_process_path,
)
from ..domain.models import ManagerSettings, RecordUnavailable
from ..domain.profiles import ProfileRecord, ProfileValidationError
from ..repositories.lifecycle_state import LifecycleStateRepository
from ..repositories.profiles import ProfileNotFound, ProfileStorageError
from .arguments import build_launch_command
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
        # Protect the evidence and phase fields shared across bridge threads
        self._evidence: LaunchEvidence | None = None
        self._phase: ServerState | None = None
        self._lock = threading.RLock()

    def status(self) -> LifecycleSnapshot:
        """Return the reconciled server state, failing closed when context is unreadable."""
        # Honor an in-flight transition phase before reconciling
        with self._lock:
            if self._phase is not None:
                return LifecycleSnapshot(self._phase)
            evidence = self._evidence
        # Report recovery-required when state or settings cannot be read
        try:
            self._state.ensure_writable()
            settings = self._settings.load()
            executable = self._configured_executable(settings)
        except (LifecycleFailure, RecordUnavailable, SettingsValidationError):
            return LifecycleSnapshot(ServerState.UNKNOWN, diagnostic_code="RECOVERY_REQUIRED")
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
        with self._lock:
            if self._phase is not None:
                return False
            if self._evidence is None:
                return True
        # Fall back to a full reconciling status check
        return self.status().state == ServerState.STOPPED

    def start(
        self,
        profile_id: str,
        expected_profile_revision: int,
        expected_settings_revision: int,
    ) -> LifecycleSnapshot:
        """Start the server after verifying settings and taking the install mutex."""
        # Require current settings with a configured DayZ root
        settings = self._require_settings(expected_settings_revision)
        assert settings.dayz_root is not None
        # Serialize start against other lifecycle control through the mutex
        with self._mutex.guard(settings.dayz_root):
            return self._start_locked(profile_id, expected_profile_revision, settings)

    def stop(self, expected_settings_revision: int) -> LifecycleSnapshot:
        """Stop the managed server after verifying settings and the mutex."""
        settings = self._require_settings(expected_settings_revision)
        assert settings.dayz_root is not None
        # Serialize stop against other lifecycle control through the mutex
        with self._mutex.guard(settings.dayz_root):
            return self._stop_locked(settings)

    def restart(
        self,
        profile_id: str,
        expected_profile_revision: int,
        expected_settings_revision: int,
    ) -> LifecycleSnapshot:
        """Restart the managed server under a single install mutex."""
        settings = self._require_settings(expected_settings_revision)
        assert settings.dayz_root is not None
        with self._mutex.guard(settings.dayz_root):
            # Verify the profile before stopping anything
            self._require_profile(profile_id, expected_profile_revision)
            self._stop_locked(settings)
            # Re-read settings so the start uses fresh revisions
            refreshed = self._require_settings(expected_settings_revision)
            return self._start_locked(profile_id, expected_profile_revision, refreshed)

    def _start_locked(
        self,
        profile_id: str,
        expected_profile_revision: int,
        settings: ManagerSettings,
    ) -> LifecycleSnapshot:
        """Start under the mutex and confirm ownership through reconciliation."""
        # Require a writable state file and a fully stopped server
        self._state.ensure_writable()
        current = self._reconcile(settings)
        self._require_state(current, ServerState.STOPPED, "start")
        # Resolve the profile and verify its executable matches the configured one
        profile = self._require_profile(profile_id, expected_profile_revision)
        assert settings.dayz_root is not None
        try:
            command = build_launch_command(profile, settings.dayz_root)
        except ProfileValidationError as error:
            raise LifecycleFailure("PATH_INVALID", str(error)) from error
        expected_executable = self._configured_executable(settings)
        if canonical_process_path(command.executable) != canonical_process_path(expected_executable):
            raise LifecycleFailure(
                "PATH_INVALID",
                "The profile executable does not match the configured DayZ executable.",
            )
        # Launch with a fresh token that pins the process to this manager
        launch_token = secrets.token_urlsafe(24)
        self._set_phase(ServerState.STARTING)
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
            self._set_evidence(evidence)
            self._state.record_launch(evidence)
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
            self._set_phase(None)

    def _stop_locked(self, settings: ManagerSettings) -> LifecycleSnapshot:
        """Stop under the mutex and verify the stopped state before recording it."""
        # Require a writable state file and a managed running server
        self._state.ensure_writable()
        current = self._reconcile(settings)
        self._require_state(current, ServerState.RUNNING_MANAGED, "stop")
        # Require proven ownership evidence before requesting a graceful stop
        evidence = self._current_evidence()
        if evidence is None:
            raise LifecycleFailure("EXTERNAL_PROCESS", "Server ownership is not proven.")
        self._set_phase(ServerState.STOPPING)
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
            if evidence.handle_token is not None:
                self._launcher.release_handle(evidence.handle_token)
            self._set_evidence(None)
            return confirmed
        finally:
            # Clear the transition phase even when the stop fails
            self._set_phase(None)

    def _reconcile(self, settings: ManagerSettings) -> LifecycleSnapshot:
        """Reconcile the configured executable against live processes and evidence."""
        return reconcile_server(
            self._configured_executable(settings),
            self._inventory,
            self._current_evidence(),
            self._launcher.retains_handle,
        )

    def _current_evidence(self) -> LaunchEvidence | None:
        """Return the current launch evidence under the lock."""
        with self._lock:
            return self._evidence

    def _set_evidence(self, evidence: LaunchEvidence | None) -> None:
        """Replace the stored launch evidence under the lock."""
        with self._lock:
            self._evidence = evidence

    def _set_phase(self, phase: ServerState | None) -> None:
        """Set or clear the in-flight transition phase under the lock."""
        with self._lock:
            self._phase = phase

    def _require_settings(self, expected_revision: int) -> ManagerSettings:
        """Load settings, enforce the revision guard, and require an executable."""
        # Map unreadable settings to a recovery-required failure
        try:
            settings = self._settings.load()
        except (RecordUnavailable, SettingsValidationError) as error:
            raise LifecycleFailure(
                "RECOVERY_REQUIRED",
                "Manager settings require recovery.",
                recovery_required=True,
            ) from error
        # Refuse a stale revision and confirm the executable is configured
        if settings.revision != expected_revision:
            raise LifecycleFailure("REVISION_CONFLICT", "The settings revision changed.")
        self._configured_executable(settings)
        return settings

    def _require_profile(self, profile_id: str, expected_revision: int) -> ProfileRecord:
        """Load the profile, enforce its revision, and map storage failures."""
        # Map missing, invalid, and stored profile failures onto lifecycle codes
        try:
            profile = self._profiles.read(profile_id)
        except ProfileNotFound as error:
            raise LifecycleFailure("NOT_FOUND", "The selected profile was not found.") from error
        except ProfileValidationError as error:
            raise LifecycleFailure("INVALID_REQUEST", str(error)) from error
        except ProfileStorageError as error:
            raise LifecycleFailure(
                "RECOVERY_REQUIRED" if error.recovery_required else "STORAGE_FAILURE",
                "The selected profile is unavailable.",
                recovery_required=error.recovery_required,
            ) from error
        # Refuse a stale revision so the launch uses the reviewed profile
        if profile.revision != expected_revision:
            raise LifecycleFailure("REVISION_CONFLICT", "The profile revision changed.")
        return profile

    @staticmethod
    def _configured_executable(settings: ManagerSettings) -> str:
        """Return the configured executable or fail with a path error."""
        # Both the root and the executable must be configured before launch
        if settings.dayz_root is None or settings.dayz_executable is None:
            raise LifecycleFailure("PATH_INVALID", "DayZ paths are not configured.")
        return settings.dayz_executable

    @staticmethod
    def _require_state(
        actual: LifecycleSnapshot,
        required: ServerState,
        action: str,
    ) -> None:
        """Raise a coded failure unless the snapshot already matches the required state."""
        if actual.state == required:
            return
        # Distinguish external, unknown, and control conflicts for the caller
        if actual.state == ServerState.RUNNING_EXTERNAL:
            code = "EXTERNAL_PROCESS"
        elif actual.state in {ServerState.UNKNOWN, ServerState.AMBIGUOUS}:
            code = "PROCESS_STATE_UNKNOWN"
        else:
            code = "CONTROL_CONFLICT"
        raise LifecycleFailure(code, f"Cannot {action} while server state is {actual.state.value}.")
