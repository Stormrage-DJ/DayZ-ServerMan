"""Pure timing rules of the remote update check: state, triggers, next due and backoff."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum

from .update_check import AttemptOutcome, CheckAttempt


# Check interval in seconds: default 30 minutes, accepted range 15 to 60 minutes
DEFAULT_INTERVAL_SECONDS = 30 * 60
MIN_INTERVAL_SECONDS = 15 * 60
MAX_INTERVAL_SECONDS = 60 * 60
# A non-forced request runs only when the last attempt is at least this old
MIN_REQUEST_AGE_SECONDS = 300
# A forced request is ignored when the last attempt is younger than this
FORCED_DEBOUNCE_SECONDS = 5
# Wall-clock budget of one run for all batches together
RUN_DEADLINE_SECONDS = 10.0
# Largest id set of one run; excess ids in numeric order get no fact
MAX_IDS_PER_RUN = 1_000
# First backoff step after a failed attempt; it doubles with every further failure
BACKOFF_BASE_SECONDS = 60


class CheckState(str, Enum):
    """Freshness of the remote facts, derived on every read."""

    NEVER = "NEVER"
    OK = "OK"
    STALE = "STALE"
    FAILED = "FAILED"


def utc_text(moment: datetime) -> str:
    """Return the moment as ISO-8601 UTC text with milliseconds."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds")


def parse_utc(text: str | None) -> datetime | None:
    """Return the moment of stored UTC text, or None when it is absent or unreadable."""
    if text is None:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    # Stored text always carries the UTC offset; a naive value is unusable
    return moment if moment.tzinfo is not None else None


def validate_interval(seconds: float) -> float:
    """Return the interval; raise ValueError outside 15 to 60 minutes."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ValueError("update check interval is invalid")
    if not MIN_INTERVAL_SECONDS <= seconds <= MAX_INTERVAL_SECONDS:
        raise ValueError("update check interval must be 15 to 60 minutes")
    return float(seconds)


def check_state(
    attempt: CheckAttempt | None, last_success_at: str | None,
    now: datetime, interval_seconds: float,
) -> CheckState:
    """Classify the stored attempt data against the current time."""
    if attempt is None:
        return CheckState.NEVER
    # A failed attempt keeps the older facts but is reported as failed
    if attempt.outcome is not AttemptOutcome.OK:
        return CheckState.FAILED
    # Facts stay fresh for two intervals after the last success
    success = parse_utc(last_success_at)
    if success is None:
        return CheckState.STALE
    age = (now - success).total_seconds()
    # A negative age means the clock moved backwards: the facts are not trusted
    return CheckState.OK if 0 <= age <= 2 * interval_seconds else CheckState.STALE


def backoff_seconds(failures: int, interval_seconds: float) -> float:
    """Return the wait after the given number of consecutive failures."""
    # No failure in this process means no wait, also after a restart
    if failures < 1:
        return 0.0
    # Cap the exponent so a long failure series cannot overflow
    step = BACKOFF_BASE_SECONDS * (2 ** min(failures - 1, 16))
    return float(min(interval_seconds, step))


def attempt_age_seconds(attempt: CheckAttempt | None, now: datetime) -> float | None:
    """Return the age of the last attempt; None when none exists or the clock moved back."""
    finished = parse_utc(attempt.finished_at) if attempt is not None else None
    if finished is None or finished > now:
        return None
    return (now - finished).total_seconds()


def next_due(
    attempt: CheckAttempt | None, failures: int, now: datetime, interval_seconds: float,
) -> datetime:
    """Return the moment at which the next scheduled check may start."""
    finished = parse_utc(attempt.finished_at) if attempt is not None else None
    # Never checked, or an attempt time in the future: the check is due now
    if finished is None or finished > now:
        return now
    # After success wait one interval; after failure wait the backoff
    if attempt.outcome is AttemptOutcome.OK:
        return finished + timedelta(seconds=interval_seconds)
    return finished + timedelta(seconds=backoff_seconds(failures, interval_seconds))


def in_backoff(
    attempt: CheckAttempt | None, failures: int, now: datetime, interval_seconds: float,
) -> bool:
    """Return whether a failed attempt still blocks non-forced checks."""
    if attempt is None or attempt.outcome is AttemptOutcome.OK:
        return False
    return now < next_due(attempt, failures, now, interval_seconds)


def scheduled_run_allowed(
    *, enabled: bool, checking: bool, attempt: CheckAttempt | None, failures: int,
    now: datetime, interval_seconds: float,
) -> bool:
    """Decide the "start, interval" trigger of the scheduler."""
    if not enabled or checking:
        return False
    return now >= next_due(attempt, failures, now, interval_seconds)


def requested_run_allowed(
    *, enabled: bool, checking: bool, attempt: CheckAttempt | None, failures: int,
    now: datetime, interval_seconds: float, fact_missing: bool,
) -> bool:
    """Decide a non-forced request: profile switch or a finished Workshop update."""
    if not enabled or checking or in_backoff(attempt, failures, now, interval_seconds):
        return False
    # Run when the last attempt is old enough or a configured id has no fact
    age = attempt_age_seconds(attempt, now)
    return age is None or age >= MIN_REQUEST_AGE_SECONDS or fact_missing


def forced_run_allowed(
    *, checking: bool, attempt: CheckAttempt | None, now: datetime,
) -> bool:
    """Decide "Check now": it ignores the switch and backoff, but is debounced."""
    if checking:
        return False
    age = attempt_age_seconds(attempt, now)
    return age is None or age >= FORCED_DEBOUNCE_SECONDS
