"""Persistent daily lifecycle scheduling through the existing operation lane."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.profiles import ProfileValidationError
from ..domain.schedules import DailySchedule, ScheduleStatus
from ..domain.schedules import ScheduleValidationError
from ..observability.structured_log import StructuredLogger
from ..repositories.profiles import ProfileNotFound, ProfileStorageError
from ..repositories.schedules import (
    ScheduleRepository,
    ScheduleRevisionConflict,
    ScheduleStorageError,
)
from .lifecycle import ServerLifecycleService
from .lifecycle_coordinator import LifecycleCoordinator
from .preferences import PreferenceCoordinator
from .profiles import ProfileService
from .schedule_dispatch import ScheduleDispatcher
from .settings import SettingsService


class ScheduleCoordinator:
    """Own schedule storage, wall-clock wakeups, durable claims, and dispatch."""

    def __init__(
        self,
        repository: ScheduleRepository,
        profiles: ProfileService,
        settings: SettingsService,
        preferences: PreferenceCoordinator,
        lifecycle: ServerLifecycleService,
        lifecycle_coordinator: LifecycleCoordinator,
        logger: StructuredLogger,
        *,
        clock: Callable[[], datetime] = datetime.now,
        poll_seconds: float = 30.0,
    ) -> None:
        """Store the repository, dispatcher, clock, and worker state."""
        self._repository = repository
        self._profiles = profiles
        self._dispatcher = ScheduleDispatcher(
            profiles, settings, preferences, lifecycle, lifecycle_coordinator,
        )
        self._logger = logger
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_runs: dict[str, datetime] = {}

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the bridge handler table for schedule calls."""
        return {
            "get_lifecycle_schedule": self.get_schedule,
            "save_lifecycle_schedule": self.save_schedule,
        }

    def get_schedule(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return the stored schedule and computed next run for a profile."""
        _exact_fields(parameters, {"profile_id"})
        profile_id = self._require_profile(parameters.get("profile_id"))
        # Read under the lock so claims cannot race the schedule view
        with self._lock:
            schedules, _revision = self._load_locked()
            schedule = schedules.get(profile_id, DailySchedule(profile_id))
            next_run = self._next_runs.get(profile_id) or schedule.next_occurrence(self._clock())
            return schedule.to_view(next_run)

    def save_schedule(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and persist one profile schedule, then wake the worker."""
        _exact_fields(parameters, {"profile_id", "hour", "minute", "action"})
        profile_id = self._require_profile(parameters.get("profile_id"))
        # Build the requested schedule from validated field values
        try:
            requested = DailySchedule.requested(
                profile_id, parameters.get("hour"), parameters.get("minute"),
                parameters.get("action"),
            )
        except (ProfileValidationError, ScheduleValidationError) as error:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error)) from error
        # Preserve claim history and persist the new schedule atomically
        with self._lock:
            schedules, revision = self._load_locked()
            prior = schedules.get(profile_id)
            if prior is not None:
                requested = DailySchedule(
                    profile_id, requested.hour, requested.minute, requested.action,
                    prior.last_claimed_date, prior.last_triggered_local,
                    prior.last_status, prior.last_operation_id,
                )
            schedules[profile_id] = requested
            self._save_locked(schedules, revision)
            next_run = requested.next_occurrence(self._clock())
            if next_run is None:
                self._next_runs.pop(profile_id, None)
            else:
                self._next_runs[profile_id] = next_run
        # Wake the worker so the change is picked up without waiting for the poll
        self._wake.set()
        self._log("schedule.saved", fields={
            "profile_id": profile_id,
            "action": requested.action.value if requested.action else None,
            "hour": requested.hour,
            "minute": requested.minute,
        })
        return requested.to_view(next_run)

    def delete_profile(self, profile_id: str) -> None:
        """Remove the schedule and pending wakeup for a deleted profile."""
        with self._lock:
            schedules, revision = self._load_locked()
            if profile_id in schedules:
                del schedules[profile_id]
                self._save_locked(schedules, revision)
            self._next_runs.pop(profile_id, None)
        self._wake.set()

    def start(self) -> None:
        """Start the background scheduler thread once, if it is not running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            # Load stored schedules so wakeups exist before the worker starts
            try:
                schedules, _revision = self._load_locked()
                self._refresh_next_runs(schedules, self._clock())
            except ApplicationCallError as error:
                self._next_runs.clear()
                self._log("schedule.storage_unavailable", level="ERROR", fields={
                    "error_code": error.code.value,
                })
            self._stop.clear()
            self._wake.clear()
            # One daemon worker owns every due check
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="dayz-serverman-scheduler",
                daemon=True,
            )
            self._thread.start()
        self._log("schedule.started")

    def stop(self, timeout: float = 2.0) -> bool:
        """Stop the scheduler thread and report whether it exited within the timeout."""
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        # Signal the worker and wake it from its poll wait
        self._stop.set()
        self._wake.set()
        thread.join(timeout)
        stopped = not thread.is_alive()
        if stopped:
            with self._lock:
                self._thread = None
            self._log("schedule.stopped")
        return stopped

    def run_due(self, now: datetime | None = None) -> int:
        """Claim and dispatch every schedule due at the given moment."""
        effective_now = now or self._clock()
        # Claim due schedules durably before any dispatch happens
        claimed = self._claim_due(effective_now)
        for schedule in claimed:
            self._dispatch(schedule)
        return len(claimed)

    def _worker_loop(self) -> None:
        """Poll for due schedules until shutdown is requested."""
        while not self._stop.is_set():
            self._wake.clear()
            try:
                self.run_due()
            except ApplicationCallError as error:
                self._log("schedule.storage_unavailable", level="ERROR", fields={
                    "error_code": error.code.value,
                })
            # Wait for the poll interval or an earlier explicit wakeup
            self._wake.wait(self._poll_seconds)

    def _claim_due(self, now: datetime) -> tuple[DailySchedule, ...]:
        """Claim every due schedule durably and advance its next run."""
        with self._lock:
            schedules, revision = self._load_locked()
            # Rebuild missing wakeups from persisted schedules
            for profile_id, schedule in schedules.items():
                if schedule.action is not None and profile_id not in self._next_runs:
                    next_run = schedule.next_occurrence(now)
                    if next_run is not None:
                        self._next_runs[profile_id] = next_run
            # A schedule is due when its next run passed and an action is set
            due_ids = [
                profile_id for profile_id, next_run in self._next_runs.items()
                if next_run <= now and schedules.get(profile_id, DailySchedule(profile_id)).action
            ]
            if not due_ids:
                return ()
            # Claim each due schedule before any dispatch happens
            claimed: list[DailySchedule] = []
            for profile_id in due_ids:
                schedule = schedules[profile_id].claimed(now)
                schedules[profile_id] = schedule
                claimed.append(schedule)
                next_run = schedule.next_occurrence(now)
                if next_run is not None:
                    self._next_runs[profile_id] = next_run
            # Persist claims and advanced next runs in one locked write
            self._save_locked(schedules, revision)
            return tuple(claimed)

    def _dispatch(self, schedule: DailySchedule) -> None:
        """Submit one claimed schedule and record the observed outcome."""
        try:
            result = self._dispatcher.execute(schedule)
            # Record the terminal status whether the run was queued or skipped
            self._record_outcome(schedule, result.status, result.operation_id)
            if result.status == ScheduleStatus.SKIPPED_NOT_RUNNING:
                # Log skips loudly because a running server was expected
                self._log("schedule.skipped", level="WARNING", fields={
                    "profile_id": schedule.profile_id,
                    "server_state": result.server_state.value,
                })
                return
            self._log("schedule.queued", operation_id=result.operation_id, fields={
                "profile_id": schedule.profile_id,
                "action": schedule.action.value if schedule.action else None,
            })
        except Exception as error:
            # Any dispatch failure is recorded as a queue failure
            self._record_outcome(schedule, ScheduleStatus.QUEUE_FAILED)
            self._log("schedule.queue_failed", level="ERROR", fields={
                "profile_id": schedule.profile_id,
                "error_type": type(error).__name__,
            })

    def _record_outcome(
        self, claimed: DailySchedule, status: ScheduleStatus,
        operation_id: str | None = None,
    ) -> None:
        """Write a dispatch outcome onto the matching durable claim."""
        with self._lock:
            schedules, revision = self._load_locked()
            current = schedules.get(claimed.profile_id)
            # Ignore outcomes whose claim was replaced by newer schedule activity
            if current is None or current.last_claimed_date != claimed.last_claimed_date:
                return
            schedules[claimed.profile_id] = current.outcome(status, operation_id)
            self._save_locked(schedules, revision)

    def _refresh_next_runs(
        self, schedules: Mapping[str, DailySchedule], now: datetime,
    ) -> None:
        """Rebuild the next-run map from persisted schedules."""
        self._next_runs = {
            profile_id: next_run
            for profile_id, schedule in schedules.items()
            if (next_run := schedule.next_occurrence(now)) is not None
        }

    def _load_locked(self) -> tuple[dict[str, DailySchedule], int | None]:
        """Load schedules, mapping storage failures to bridge errors."""
        try:
            return self._repository.load()
        except ScheduleStorageError as error:
            # Distinguish recovery-required stores from plain storage failures
            code = (
                ErrorCode.RECOVERY_REQUIRED if error.recovery_required
                else ErrorCode.STORAGE_FAILURE
            )
            raise ApplicationCallError(code, str(error)) from error

    def _save_locked(
        self, schedules: Mapping[str, DailySchedule], revision: int | None,
    ) -> None:
        """Persist schedules, mapping conflicts and failures to bridge errors."""
        try:
            self._repository.save(schedules, revision)
        except ScheduleRevisionConflict as error:
            # A concurrent writer holds a newer revision; the caller may retry
            raise ApplicationCallError(
                ErrorCode.REVISION_CONFLICT, str(error), retryable=True,
            ) from error
        except ScheduleStorageError as error:
            raise ApplicationCallError(ErrorCode.STORAGE_FAILURE, str(error)) from error

    def _require_profile(self, value: object) -> str:
        """Resolve a profile id or raise the matching bridge error."""
        try:
            profile = self._profiles.read(value)
            return profile.values.profile_id
        except ProfileValidationError as error:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error)) from error
        except ProfileNotFound as error:
            raise ApplicationCallError(ErrorCode.NOT_FOUND, str(error)) from error
        except ProfileStorageError as error:
            raise ApplicationCallError(
                ErrorCode.STORAGE_FAILURE, "Profile storage is unavailable.",
            ) from error

    def _log(
        self, event: str, *, level: str = "INFO",
        operation_id: str | None = None, fields: Mapping[str, Any] | None = None,
    ) -> None:
        """Emit one structured event and swallow logging failures."""
        try:
            self._logger.emit(
                event, level=level, operation_id=operation_id, fields=fields,
            )
        except (OSError, TypeError, ValueError):
            return


def _exact_fields(parameters: Mapping[str, Any], fields: set[str]) -> None:
    """Reject schedule requests whose fields do not match the contract."""
    if set(parameters) != fields:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "schedule parameters are invalid")
