"""Exact native group and container capacity targets of one baseline source (R07, R08, task 6.7).

A target is a native lootmax ceiling of an ordinary group and its containers.
It is not an item supply quota for each category and not a predicted live item
quantity. Every target starts from the working baseline value, so a reapply
never compounds a generated value. mission_map_capacity_targets applies this
to the placements of a plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction


# Smallest and largest effective adjustment in percent: global -50 with Sparse, global +100 with Very Rich
ADJUSTMENT_RANGE = (-75, 200)
# Effective adjustments are whole steps of 25 percent, so each factor is a quarter step from 1/4 to 3
ADJUSTMENT_STEP = 25
# Refusal reasons: no proved ceiling, a container name that is not unique, a value above the supported limit
CEILING_UNRESOLVED = "ceiling_unresolved"
CONTAINER_AMBIGUOUS = "container_ambiguous"
CEILING_UNSUPPORTED = "ceiling_unsupported"


@dataclass(frozen=True)
class Ceiling:
    """One effective baseline ceiling: an explicit native integer or one proved default.

    A None value is a ceiling that the source resolution did not prove; problem
    names the reason, for example a missing or duplicate default.
    """

    value: int | None
    explicit: bool
    problem: str | None = None

    def __post_init__(self) -> None:
        """Accept only a nonnegative integer or an unresolved ceiling."""
        if self.value is not None and (isinstance(self.value, bool) or not isinstance(self.value, int)
                                       or self.value < 0):
            raise ValueError("a resolved ceiling must be a nonnegative integer")


@dataclass(frozen=True)
class BaselineContainer:
    """One container of a baseline group, with its known distinct native point count, if any."""

    identity: str
    ceiling: Ceiling
    points: int | None = None

    def __post_init__(self) -> None:
        """Accept only a nonnegative integer point count or an unknown count."""
        if self.points is not None and (isinstance(self.points, bool) or not isinstance(self.points, int)
                                        or self.points < 0):
            raise ValueError("a point count must be a nonnegative integer")


@dataclass(frozen=True)
class BaselineSource:
    """The working-baseline capacity of one ordinary group, named by [relative path, group name].

    points_proved is true only when candidate evidence proves that every point
    count is usable; otherwise the point bound is an upper bound only.
    """

    identity: tuple[str, str]
    group: Ceiling
    containers: tuple[BaselineContainer, ...]
    points_proved: bool = False


@dataclass(frozen=True)
class FieldTarget:
    """The requested rational and the realized integer of one group or container ceiling."""

    identity: str | None
    explicit: bool
    baseline: int
    requested: Fraction
    realized: int

    @property
    def difference(self) -> Fraction:
        """Return the rounding difference, realized minus requested."""
        return self.realized - self.requested


@dataclass(frozen=True)
class CapacityTarget:
    """The capacity target of one source at one effective adjustment.

    achievable is min(group, sum(min(container, points))) when every point count
    is known, else None. qualified is true when that value is an upper bound only.
    """

    source: tuple[str, str]
    adjustment: int
    factor: Fraction
    group: FieldTarget
    containers: tuple[FieldTarget, ...]
    achievable: int | None
    qualified: bool

    @property
    def changed(self) -> bool:
        """Return whether any realized ceiling differs from its baseline value."""
        return any(item.realized != item.baseline for item in (self.group, *self.containers))


class CapacityRefusal(ValueError):
    """A required ceiling is unresolved, ambiguous or above the supported native limit."""

    def __init__(self, source: tuple[str, str], field: str | None, reason: str) -> None:
        """Keep the source, the field (None for the group ceiling) and the reason."""
        super().__init__(f"{source[0]} {source[1]} {'group' if field is None else field}: {reason}")
        self.source, self.field, self.reason = source, field, reason


def half_up(target: Fraction) -> int:
    """Return the nearest integer of a nonnegative rational, with exact halves upward (A127, A130)."""
    if target < 0:
        raise ValueError("a capacity target cannot be negative")
    return (2 * target.numerator + target.denominator) // (2 * target.denominator)


def capacity_factor(adjustment: int) -> Fraction:
    """Return the exact factor (100 + adjustment) / 100 of an allowed effective adjustment."""
    low, high = ADJUSTMENT_RANGE
    if (isinstance(adjustment, bool) or not isinstance(adjustment, int) or not low <= adjustment <= high
            or adjustment % ADJUSTMENT_STEP):
        raise ValueError(f"the effective adjustment {adjustment!r} is not an allowed step")
    return Fraction(100 + adjustment, 100)


def capacity_target(source: BaselineSource, adjustment: int, *, maximum: int | None = None) -> CapacityTarget:
    """Return the target of one source; raise CapacityRefusal when a required value is not supported.

    maximum is the supported native limit when evidence proves one; None checks
    no limit, because there is no universal limit.
    """
    factor = capacity_factor(adjustment)
    names = [container.identity for container in source.containers]
    if len(set(names)) != len(names):
        duplicate = next(name for name in names if names.count(name) > 1)
        raise CapacityRefusal(source.identity, duplicate, CONTAINER_AMBIGUOUS)
    # Each ceiling is rounded on its own: no quota, redistribution or positive minimum
    group = _field(source.identity, None, source.group, factor, maximum)
    containers = tuple(_field(source.identity, item.identity, item.ceiling, factor, maximum)
                       for item in source.containers)
    achievable = None
    if all(item.points is not None for item in source.containers):
        achievable = min(group.realized, sum(min(target.realized, item.points)
                                             for target, item in zip(containers, source.containers)))
    return CapacityTarget(source.identity, adjustment, factor, group, containers, achievable,
                          not source.points_proved)


def _field(source: tuple[str, str], identity: str | None, ceiling: Ceiling, factor: Fraction,
           maximum: int | None) -> FieldTarget:
    """Return the exact requested and realized value of one baseline ceiling."""
    if ceiling.value is None:
        raise CapacityRefusal(source, identity, ceiling.problem or CEILING_UNRESOLVED)
    requested = ceiling.value * factor
    realized = half_up(requested)
    if maximum is not None and realized > maximum:
        raise CapacityRefusal(source, identity, CEILING_UNSUPPORTED)
    return FieldTarget(identity, ceiling.explicit, ceiling.value, requested, realized)
