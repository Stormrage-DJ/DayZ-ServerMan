"""Validated persistence for daily lifecycle schedules."""

from __future__ import annotations

from collections.abc import Mapping

from ..domain.models import RecordState, RecordUnavailable, RevisionConflict
from ..domain.profiles import ProfileValidationError
from ..domain.schedules import DailySchedule, ScheduleValidationError
from .json_store import VersionedJsonRepository


class ScheduleStorageError(RuntimeError):
    """Schedule storage is unavailable or requires recovery."""

    def __init__(self, message: str, *, recovery_required: bool = False) -> None:
        """Record the failure message and whether recovery is required."""
        super().__init__(message)
        self.recovery_required = recovery_required


class ScheduleRevisionConflict(ScheduleStorageError):
    """Schedule storage changed since it was read."""


class ScheduleRepository:
    """Map the versioned JSON record to validated schedule objects."""

    def __init__(self, repository: VersionedJsonRepository) -> None:
        """Store the versioned record repository used for persistence."""
        self._repository = repository

    def load(self) -> tuple[dict[str, DailySchedule], int | None]:
        """Return stored schedules and their revision, or empty when unset."""
        inspection = self._repository.inspect()
        # Treat a missing record as an empty schedule set
        if inspection.state == RecordState.MISSING:
            return {}, None
        # Reject unreadable records so callers can surface recovery
        if inspection.state != RecordState.VALID or inspection.document is None:
            raise ScheduleStorageError(
                "Schedule storage requires recovery.", recovery_required=True,
            )
        fields = inspection.document.fields
        # The record must contain exactly one schedules mapping
        if set(fields) != {"schedules"} or not isinstance(fields["schedules"], dict):
            raise ScheduleStorageError(
                "Schedule storage requires recovery.", recovery_required=True,
            )
        try:
            # Rebuild every entry through domain validation
            schedules = {
                profile_id: DailySchedule.parse(profile_id, value)
                for profile_id, value in fields["schedules"].items()
            }
        except (ProfileValidationError, ScheduleValidationError) as error:
            raise ScheduleStorageError(
                "Schedule storage requires recovery.", recovery_required=True,
            ) from error
        return schedules, inspection.document.revision

    def save(
        self, schedules: Mapping[str, DailySchedule], expected_revision: int | None,
    ) -> int:
        """Persist schedules sorted by profile and return the new revision."""
        # Sort keys so repeated saves produce identical bytes
        fields = {"schedules": {
            profile_id: schedule.to_record()
            for profile_id, schedule in sorted(schedules.items())
        }}
        try:
            document = self._repository.save(fields, expected_revision)
        except RevisionConflict as error:
            raise ScheduleRevisionConflict(str(error)) from error
        except (OSError, RecordUnavailable, ValueError) as error:
            raise ScheduleStorageError(
                "Schedule storage could not be updated.",
            ) from error
        return document.revision
