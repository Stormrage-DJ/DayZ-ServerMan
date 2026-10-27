"""Field checks of the D3 record shapes, and their error (moved from `mission_map_records.py` in 10.3)."""

from __future__ import annotations

import re
from typing import Any, Callable

from .mission_map_values import TARGET_KEY, ObjectRuleError, exact_fields, integer
from .profiles import ProfileValidationError, validate_profile_id, validate_relative_path


# Largest file size and revision a record may state: the exact JSON integer range
MAX_SAFE_INTEGER = 2**53 - 1


class RecordShapeError(ValueError):
    """Raised when a record breaks its D3 shape or its manifest digest does not match."""
    pass


def _guarded(check: Callable[[], object]) -> object:
    """Run one value check and report its failure as a record shape error."""
    try:
        return check()
    except (ObjectRuleError, ProfileValidationError) as error:
        raise RecordShapeError(str(error)) from error


def require_exact(value: object, fields: set[str] | frozenset[str], name: str) -> dict[str, Any]:
    """Return an object with exactly the fields."""
    return _guarded(lambda: exact_fields(value, name, fields))  # type: ignore[return-value]


def require_match(value: object, pattern: re.Pattern[str], field: str) -> None:
    """Check that a text fully matches the pattern."""
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise RecordShapeError(f"{field} has an invalid value")


def require_key(value: object) -> None:
    """Check a target key."""
    require_match(value, TARGET_KEY, "target key")


def require_profile(value: object) -> None:
    """Check a stable profile ID."""
    _guarded(lambda: validate_profile_id(value))


def require_relative(value: object) -> None:
    """Check a mission root relative to the DayZ root."""
    _guarded(lambda: validate_relative_path(value, "mission_root"))


def require_text(value: object, field: str) -> None:
    """Check a non-empty text."""
    if not isinstance(value, str) or not value:
        raise RecordShapeError(f"{field} must be non-empty text")


def _integer(value: object, field: str) -> None:
    """Check a non-negative JSON integer."""
    _guarded(lambda: integer(value, field, 0, MAX_SAFE_INTEGER))


def _list(value: object, field: str) -> list[Any]:
    """Return a JSON list."""
    if not isinstance(value, list):
        raise RecordShapeError(f"{field} must be a list")
    return value
