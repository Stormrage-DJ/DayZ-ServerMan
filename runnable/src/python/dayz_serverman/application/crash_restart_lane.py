"""Operation lane adapter of the crash restart: list lifecycle work, submit the crash start, read a state."""

from __future__ import annotations

from collections.abc import Callable

from .lifecycle_coordinator import LifecycleCoordinator
from .operations.manager import OperationManager
from .operations.models import OperationState
from .profiles import ProfileService
from .settings import SettingsService

# Operation kinds that start, stop or restart the server
LIFECYCLE_KINDS = frozenset({"START_SERVER", "STOP_SERVER", "RESTART_SERVER", "APPLY_MODS_AND_RESTART"})


class CrashRestartLane:
    """Give the crash restart flow its view on the operation lane."""

    def __init__(self, profiles: ProfileService, settings: SettingsService,
                 coordinator: LifecycleCoordinator, operations: OperationManager) -> None:
        """Bind the services that supply the revisions and the lane."""
        self._profiles = profiles
        self._settings = settings
        self._coordinator = coordinator
        self._operations = operations

    def lifecycle_operation_ids(self) -> frozenset[str]:
        """Return the ids of the recent operations that start, stop or restart the server."""
        return frozenset(
            item.operation_id for item in self._operations.list_recent() if item.kind in LIFECYCLE_KINDS
        )

    def submit(self, profile_id: str, still_wanted: Callable[[], bool]) -> str:
        """Queue the crash start with the current revisions and return its operation id."""
        profile_revision = self._profiles.read(profile_id).revision
        settings_revision = self._settings.load().revision
        if settings_revision is None:
            raise RuntimeError("settings revision is unavailable")
        return self._coordinator.start_after_crash(
            profile_id, profile_revision, settings_revision, still_wanted,
        )["operation_id"]

    def state(self, operation_id: str) -> OperationState:
        """Return the state of one operation."""
        return self._operations.get(operation_id).state
