"""Submit a claimed schedule through verified lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.lifecycle import ServerState
from ..domain.schedules import DailySchedule, ScheduleAction, ScheduleStatus
from .lifecycle import ServerLifecycleService
from .lifecycle_coordinator import LifecycleCoordinator
from .preferences import PreferenceCoordinator
from .profiles import ProfileService
from .settings import SettingsService


@dataclass(frozen=True)
class ScheduleDispatchResult:
    """Outcome of submitting one claimed schedule."""

    status: ScheduleStatus
    operation_id: str | None = None
    server_state: ServerState | None = None


class ScheduleDispatcher:
    """Resolve current revisions and submit one claimed schedule."""

    def __init__(
        self,
        profiles: ProfileService,
        settings: SettingsService,
        preferences: PreferenceCoordinator,
        lifecycle: ServerLifecycleService,
        lifecycle_coordinator: LifecycleCoordinator,
    ) -> None:
        """Store the collaborators needed to submit schedules."""
        self._profiles = profiles
        self._settings = settings
        self._preferences = preferences
        self._lifecycle = lifecycle
        self._lifecycle_coordinator = lifecycle_coordinator

    def execute(self, schedule: DailySchedule) -> ScheduleDispatchResult:
        """Submit one claimed schedule and report its dispatch outcome."""
        # Only a managed running server may execute a schedule
        status = self._lifecycle.status()
        if status.state != ServerState.RUNNING_MANAGED:
            return ScheduleDispatchResult(
                ScheduleStatus.SKIPPED_NOT_RUNNING,
                server_state=status.state,
            )
        # A schedule belongs to one profile: while the server runs with another profile, this
        # profile's server is not running, so the run is skipped. A server whose profile is not
        # known (started in another manager session) keeps the earlier behaviour.
        running = getattr(status, "profile_id", None)
        if running is not None and running != schedule.profile_id:
            return ScheduleDispatchResult(
                ScheduleStatus.SKIPPED_NOT_RUNNING,
                server_state=status.state,
            )
        # Resolve current revisions so a stale claim cannot act
        profile = self._profiles.read(schedule.profile_id)
        settings = self._settings.load()
        if settings.revision is None:
            raise RuntimeError("settings revision is unavailable")
        # Pass expected revisions and the backup preference with the request
        parameters = {
            "profile_id": schedule.profile_id,
            "expected_profile_revision": profile.revision,
            "expected_settings_revision": settings.revision,
            "backup_after_stop": self._preferences.backup_after_stop(schedule.profile_id),
        }
        # Route the claimed action through the matching lifecycle operation
        if schedule.action == ScheduleAction.RESTART:
            accepted = self._lifecycle_coordinator.restart_server(parameters)
        else:
            accepted = self._lifecycle_coordinator.stop_server(parameters)
        return ScheduleDispatchResult(ScheduleStatus.QUEUED, accepted["operation_id"])
