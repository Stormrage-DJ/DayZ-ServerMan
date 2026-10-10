"""Limits, enumerations, patterns and problem types of the mission map plan schema 1 (D1)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


# Format marker that identifies a plan document
PLAN_FORMAT = "dayz-serverman/mission-map-plan"
# The one schema version this build parses
PLAN_VERSION = 1

# Largest document in UTF-8 bytes (8 MiB)
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
# Deepest container nesting of a whole document; a scalar has depth 0
MAX_DOCUMENT_DEPTH = 16
# Largest number of loot zones and encounters in one plan
MAX_OBJECTS = 2000
# Vertex limits of one polygon and of all polygons in one plan
MIN_POLYGON_VERTICES = 3
MAX_POLYGON_VERTICES = 512
MAX_PLAN_VERTICES = 50000
# Smallest distance between two vertices of one polygon, in metres
MIN_VERTEX_SPACING = 0.001
# Smallest absolute polygon area, in square metres
MIN_POLYGON_AREA = 1
# Smallest circle radius, in metres; the largest is the larger bounds extent
MIN_RADIUS = 1.0
# Largest terrain width and height, and the largest absolute origin coordinate, in metres
MAX_TERRAIN_EXTENT = 100_000.0
MAX_TERRAIN_ORIGIN = 1_000_000.0
# Smallest absolute determinant a·e − b·d of an image calibration
MIN_CALIBRATION_DETERMINANT = 1e-12
# Longest object name after trimming
MAX_NAME_LENGTH = 80
# Inclusive range of the native infected population counts
INFECTED_COUNT_RANGE = (0, 100)

# Limits of an open bounded provider object (depth, keys per object, string length, list length)
OPEN_MAX_DEPTH = 4
OPEN_MAX_KEYS = 64
OPEN_MAX_STRING = 256
OPEN_MAX_LIST = 64

# Stable identifier: unique in the plan without regard to case
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}")
# Terrain identity, compared without regard to case
TERRAIN_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
# Value-flag tier name of the mission's limits definition
TIER_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}")
# Native infected event name and open provider object key
EVENT_NAME = re.compile(r"[A-Za-z0-9_]{1,64}")
OPEN_KEY = re.compile(r"[A-Za-z0-9_]{1,64}")
# Target key: the first 32 hexadecimal digits of a SHA-256 digest (D2)
TARGET_KEY = re.compile(r"[0-9a-f]{32}")
# Full lowercase SHA-256 digest
SHA256 = re.compile(r"[0-9a-f]{64}")
# Control characters that a name or file name must not hold
CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# Abundance presets and their zone adjustment in percent (R07)
ABUNDANCE_PERCENT = {"sparse": -25, "normal": 0, "rich": 50, "very_rich": 100}
# Default abundance of a new loot zone and of newly enabled linked loot (R07)
DEFAULT_ABUNDANCE = "rich"
# Allowed positions of the global abundance slider, in percent (R08)
GLOBAL_ABUNDANCE_STEPS = (-50, -25, 0, 25, 50, 75, 100)
# Sources of confirmed terrain bounds (R02)
BOUNDS_SOURCES = frozenset(("detected", "manual"))


class ObjectKind(str, Enum):
    """Kind of one plan object; it decides the other fields."""

    LOOT_CIRCLE = "loot_circle"
    LOOT_POLYGON = "loot_polygon"
    ENCOUNTER = "encounter"


class Provider(str, Enum):
    """Encounter provider named by an encounter object."""

    NATIVE_INFECTED = "native_infected"
    NATIVE_WOLF = "native_wolf"
    NATIVE_BEAR = "native_bear"
    AI_BANDITS = "ai_bandits"


# Providers with a native spawn radius; their linked loot uses the encounter radius (D6)
RADIUS_PROVIDERS = frozenset((Provider.NATIVE_INFECTED, Provider.NATIVE_WOLF, Provider.NATIVE_BEAR))
# Providers whose properties are an open bounded object (task 7 adapters check the keys)
OPEN_PROPERTY_PROVIDERS = frozenset((Provider.NATIVE_WOLF, Provider.NATIVE_BEAR, Provider.AI_BANDITS))


@dataclass(frozen=True)
class PlanProblem:
    """One failed D1 rule, at document level or for one object."""

    message: str
    # Position and identifier of the failing object; None for a document-level problem
    object_index: int | None = None
    object_id: str | None = None
    # True when the rule depends on the terrain bounds, so import may exclude the object (D7)
    bounds_dependent: bool = False


class PlanValidationError(ValueError):
    """Raised when a plan document fails D1; it lists every problem found."""

    def __init__(self, problems: tuple[PlanProblem, ...] | list[PlanProblem]) -> None:
        """Store the problems and use the first one as the message."""
        self.problems = tuple(problems)
        super().__init__(self.problems[0].message if self.problems else "plan document is invalid")


class PlanVersionError(PlanValidationError):
    """Raised for a plan document of a newer schema version, which a newer build created."""
    pass


class ObjectRuleError(ValueError):
    """Raised inside object validation for the first failed rule of one object."""

    def __init__(self, message: str, *, bounds_dependent: bool = False) -> None:
        """Store the message and whether the failed rule depends on the bounds."""
        self.bounds_dependent = bounds_dependent
        super().__init__(message)


def document_error(message: str) -> PlanValidationError:
    """Return a validation error with one document-level problem."""
    return PlanValidationError((PlanProblem(message),))


def finite_number(value: object, field: str) -> float:
    """Return a JSON number as a finite float, or raise ObjectRuleError."""
    # Booleans are JSON literals, not numbers
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ObjectRuleError(f"{field} must be a number")
    try:
        number = float(value)
    except OverflowError as error:
        raise ObjectRuleError(f"{field} must be a finite number") from error
    if not math.isfinite(number):
        raise ObjectRuleError(f"{field} must be a finite number")
    return number


def integer(value: object, field: str, low: int, high: int) -> int:
    """Return a JSON integer inside the inclusive range, or raise ObjectRuleError."""
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ObjectRuleError(f"{field} must be an integer from {low} to {high}")
    return value


def exact_fields(value: object, field: str, required: frozenset[str] | set[str]) -> dict:
    """Return a JSON object that holds exactly the required fields, or raise ObjectRuleError."""
    if not isinstance(value, dict):
        raise ObjectRuleError(f"{field} must be an object")
    # Unknown fields are refused in every closed object of D1
    unknown = sorted(set(value) - set(required))
    if unknown:
        raise ObjectRuleError(f"{field} has unknown fields: {', '.join(unknown)}")
    missing = sorted(set(required) - set(value))
    if missing:
        raise ObjectRuleError(f"{field} is missing fields: {', '.join(missing)}")
    return value


def matching_text(value: object, field: str, pattern: re.Pattern[str]) -> str:
    """Return a string that fully matches the pattern, or raise ObjectRuleError."""
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ObjectRuleError(f"{field} has an invalid value")
    return value


def plain_name(value: object, field: str, limit: int) -> str:
    """Return the trimmed text of 1 to limit characters without control characters."""
    if not isinstance(value, str):
        raise ObjectRuleError(f"{field} must be text")
    trimmed = value.strip()
    if not 1 <= len(trimmed) <= limit or CONTROL_CHARACTER.search(trimmed):
        raise ObjectRuleError(f"{field} must have 1 to {limit} characters without control characters")
    return trimmed


def json_depth(value: object) -> int:
    """Return the container nesting depth of a JSON value; a scalar has depth 0."""
    deepest, pending = 0, [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, (dict, list)):
            deepest = max(deepest, depth + 1)
            pending.extend((child, depth + 1) for child in (item.values() if isinstance(item, dict) else item))
    return deepest


def refuse_json_constant(name: str) -> None:
    """Refuse NaN and the infinities while JSON text is parsed; they are not JSON numbers."""
    raise ValueError(f"{name} is not a finite number")


def unique_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while parsing and refuse a key that appears twice."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"the key {key!r} appears twice in one object")
        result[key] = value
    return result
