"""Remote Workshop facts and update-check results as validated values."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


# Steam application that owns every DayZ Workshop item
WORKSHOP_CONSUMER_APP_ID = 221100
# Bounds of one remote request and of the persisted fact set
MAX_IDS_PER_REQUEST = 200
MAX_CACHED_ITEMS = 1_000


class RemoteItemResult(str, Enum):
    """Answer class of one Workshop item in a remote check."""

    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    WRONG_APP = "WRONG_APP"
    INVALID = "INVALID"


class RemoteBatchFailure(str, Enum):
    """Reason why one remote request returned no item results."""

    NETWORK_UNREACHABLE = "NETWORK_UNREACHABLE"
    TIMEOUT = "TIMEOUT"
    TLS_FAILURE = "TLS_FAILURE"
    HTTP_STATUS = "HTTP_STATUS"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    RESPONSE_MALFORMED = "RESPONSE_MALFORMED"
    INTERNAL = "INTERNAL"


class AttemptOutcome(str, Enum):
    """Terminal outcome of one complete check run."""

    OK = "OK"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RemoteItem:
    """Remote answer for one requested Workshop id."""

    workshop_id: str
    result: RemoteItemResult
    time_updated: int | None = None
    file_size: int | None = None


@dataclass(frozen=True)
class RemoteBatchResult:
    """Outcome of one remote request: item results or one failure code."""

    items: tuple[RemoteItem, ...] = ()
    failure: RemoteBatchFailure | None = None

    @property
    def ok(self) -> bool:
        """Return whether the request produced item results."""
        return self.failure is None


@dataclass(frozen=True)
class RemoteFact:
    """Stored remote answer for one Workshop id with its check time."""

    result: RemoteItemResult
    time_updated: int | None
    file_size: int | None
    checked_at: str

    def to_dict(self) -> dict[str, object]:
        """Return the fact as its persisted JSON object."""
        return {
            "result": self.result.value,
            "time_updated": self.time_updated,
            "file_size": self.file_size,
            "checked_at": self.checked_at,
        }


@dataclass(frozen=True)
class CheckAttempt:
    """End time and outcome of the last check run."""

    finished_at: str
    outcome: AttemptOutcome
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the attempt as its persisted JSON object."""
        return {
            "finished_at": self.finished_at,
            "outcome": self.outcome.value,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class UpdateCheckRecord:
    """Complete persisted state of the remote check: attempt data and facts."""

    last_attempt: CheckAttempt | None = None
    last_success_at: str | None = None
    items: Mapping[str, RemoteFact] = field(default_factory=dict)
