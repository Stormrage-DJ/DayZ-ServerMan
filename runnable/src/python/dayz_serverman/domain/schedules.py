"""Validated daily local-time lifecycle schedules."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Mapping

from .profiles import validate_profile_id


class ScheduleValidationError(ValueError):
    """A persisted or requested schedule violates the schedule contract."""


class ScheduleAction(str, Enum):
    """Lifecycle action a daily schedule triggers."""

    STOP = "stop"
    RESTART = "restart"


class ScheduleStatus(str, Enum):
    """Durable outcome state of the most recent claim or trigger."""

    CLAIMED = "CLAIMED"
    QUEUED = "QUEUED"
    SKIPPED_NOT_RUNNING = "SKIPPED_NOT_RUNNING"
    QUEUE_FAILED = "QUEUE_FAILED"


@dataclass(frozen=True)
class DailySchedule:
    """Validated daily schedule with claim and outcome bookkeeping."""

    profile_id: str
    hour: int = 4
    minute: int = 0
    action: ScheduleAction | None = None
    last_claimed_date: date | None = None
    last_triggered_local: datetime | None = None
    last_status: ScheduleStatus | None = None
    last_operation_id: str | None = None

    @classmethod
    def parse(cls, profile_id: object, value: object) -> "DailySchedule":
        """Parse and validate a persisted schedule record."""
        # Require the exact persisted field set
        expected = {
            "action", "hour", "minute", "last_claimed_date",
            "last_triggered_local", "last_status", "last_operation_id",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ScheduleValidationError("schedule fields are missing or unknown")
        return cls(
            profile_id=validate_profile_id(profile_id),
            hour=_bounded_integer(value["hour"], "hour", 0, 23),
            minute=_bounded_integer(value["minute"], "minute", 0, 59),
            action=_optional_enum(ScheduleAction, value["action"], "action"),
            last_claimed_date=_optional_date(value["last_claimed_date"]),
            last_triggered_local=_optional_datetime(value["last_triggered_local"]),
            last_status=_optional_enum(ScheduleStatus, value["last_status"], "last_status"),
            last_operation_id=_optional_identifier(value["last_operation_id"]),
        )

    @classmethod
    def requested(
        cls, profile_id: object, hour: object, minute: object, action: object,
    ) -> "DailySchedule":
        """Build a schedule for a requested hour, minute, and action."""
        normalized_action = _optional_enum(ScheduleAction, action, "action")
        return cls(
            validate_profile_id(profile_id),
            _bounded_integer(hour, "hour", 0, 23),
            _bounded_integer(minute, "minute", 0, 59),
            normalized_action,
        )

    def next_occurrence(self, now: datetime) -> datetime | None:
        """Return the next local trigger instant, or None while disabled."""
        # A disabled schedule has no next run
        if self.action is None:
            return None
        candidate = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        # Roll to tomorrow when today's slot passed or was already claimed
        if candidate < now or self.last_claimed_date == now.date():
            candidate += timedelta(days=1)
        return candidate

    def claimed(self, now: datetime) -> "DailySchedule":
        """Return a copy recorded as claimed at the given local time."""
        return replace(
            self,
            last_claimed_date=now.date(),
            last_triggered_local=now.replace(microsecond=0),
            last_status=ScheduleStatus.CLAIMED,
            last_operation_id=None,
        )

    def outcome(self, status: ScheduleStatus, operation_id: str | None = None) -> "DailySchedule":
        """Return a copy recording the trigger outcome."""
        # A queued trigger must carry its queued operation identifier
        if status == ScheduleStatus.QUEUED and operation_id is None:
            raise ScheduleValidationError("queued schedules require an operation identifier")
        return replace(self, last_status=status, last_operation_id=operation_id)

    def to_record(self) -> dict[str, Any]:
        """Return the persisted record fields for this schedule."""
        return {
            "action": self.action.value if self.action else None,
            "hour": self.hour,
            "minute": self.minute,
            "last_claimed_date": self.last_claimed_date.isoformat()
            if self.last_claimed_date else None,
            "last_triggered_local": self.last_triggered_local.isoformat()
            if self.last_triggered_local else None,
            "last_status": self.last_status.value if self.last_status else None,
            "last_operation_id": self.last_operation_id,
        }

    def to_view(self, next_run: datetime | None) -> dict[str, Any]:
        """Return the display view fields, including the next local run."""
        return {
            "profile_id": self.profile_id,
            "enabled": self.action is not None,
            **self.to_record(),
            "next_run_local": next_run.isoformat(timespec="minutes") if next_run else None,
        }


def _bounded_integer(value: object, field: str, minimum: int, maximum: int) -> int:
    """Return an integer inside the inclusive range, or raise."""
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ScheduleValidationError(f"{field} must be from {minimum} through {maximum}")
    return value


def _optional_enum(enum_type, value: object, field: str):
    """Return the enum member for an optional persisted value."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ScheduleValidationError(f"{field} is invalid")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ScheduleValidationError(f"{field} is invalid") from error


def _optional_date(value: object) -> date | None:
    """Return an ISO date value, rejecting non-canonical text."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ScheduleValidationError("last_claimed_date is invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ScheduleValidationError("last_claimed_date is invalid") from error
    # Reject text that would not round-trip exactly
    if parsed.isoformat() != value:
        raise ScheduleValidationError("last_claimed_date is invalid")
    return parsed


def _optional_datetime(value: object) -> datetime | None:
    """Return a timezone-free local wall-time value."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ScheduleValidationError("last_triggered_local is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ScheduleValidationError("last_triggered_local is invalid") from error
    # Wall time must stay offset-free so DST changes cannot shift it
    if parsed.tzinfo is not None:
        raise ScheduleValidationError("last_triggered_local must use local wall time")
    return parsed


def _optional_identifier(value: object) -> str | None:
    """Return a bounded operation identifier or None."""
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ScheduleValidationError("last_operation_id is invalid")
    return value
