"""Disposable persisted remote facts and outcome of the Workshop update check."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Collection, Mapping

from ..domain.profiles import WORKSHOP_ID
from ..domain.update_check import (
    MAX_CACHED_ITEMS,
    AttemptOutcome,
    CheckAttempt,
    RemoteFact,
    RemoteItemResult,
    UpdateCheckRecord,
)
from ..observability.structured_log import StructuredLogger

# A larger file is ignored as a whole
MAX_FILE_BYTES = 1024 * 1024
# A stored time more than this many seconds ahead of the clock rejects the file;
# the allowance covers only small clock corrections
MAX_FUTURE_SECONDS = 60
# ISO-8601 UTC with milliseconds, as the application writes it
_UTC_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00")
# Stable upper-case failure code text
_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
# Exact field sets of the persisted objects
_ROOT_FIELDS = {"schema_version", "last_attempt", "last_success_at", "items"}
_ATTEMPT_FIELDS = {"finished_at", "outcome", "error_code"}
_ITEM_FIELDS = {"result", "time_updated", "file_size", "checked_at"}


class UpdateCheckCacheRepository:
    """Persist remote facts in one JSON file that may be lost at any time."""

    def __init__(
        self, path: Path, logger: StructuredLogger | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Store the cache file path, the optional logger and the wall clock."""
        self._path = path
        self._logger = logger
        self._clock = clock

    def load(self) -> UpdateCheckRecord | None:
        """Return the stored record, or None ("never checked") for any unusable file."""
        try:
            # Read at most one byte above the cap so an oversized file is detected
            with self._path.open("rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                return None
            # No stored time may lie in the future beyond the small allowance
            latest = self._clock() + timedelta(seconds=MAX_FUTURE_SECONDS)
            # Parse strictly; one malformed part rejects the whole file
            return _record(
                json.loads(raw.decode("utf-8"), parse_constant=_reject_constant), latest,
            )
        except (OSError, ValueError, TypeError, RecursionError):
            return None

    def save(self, record: UpdateCheckRecord, configured_ids: Collection[str]) -> bool:
        """Write the record atomically; return False and log when the write fails."""
        try:
            # Drop ids that no readable profile configures, then apply the item cap
            kept = sorted(
                (key for key in record.items if key in configured_ids), key=int,
            )[:MAX_CACHED_ITEMS]
            attempt = record.last_attempt
            self._write({
                "schema_version": 1,
                "last_attempt": attempt.to_dict() if attempt is not None else None,
                "last_success_at": record.last_success_at,
                "items": {key: record.items[key].to_dict() for key in kept},
            })
            return True
        except (OSError, ValueError, TypeError) as error:
            # The cache is disposable: report the failure and keep the old file
            self._log_failure(error)
            return False

    def _write(self, value: dict[str, object]) -> None:
        """Write the document atomically through a temporary sibling file."""
        # Ensure the target directory exists before publishing
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Stage the document beside the target so the swap stays on one volume
        temporary = self._path.with_name(f".{self._path.name}.{uuid.uuid4().hex}.tmp")
        try:
            # Serialize, flush, and fsync the staged bytes before the swap
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            # Swap the staged file into place
            os.replace(temporary, self._path)
        finally:
            # Remove the staged file when any step fails
            if temporary.exists():
                temporary.unlink()

    def _log_failure(self, error: Exception) -> None:
        """Emit the write-failure event and swallow logging failures."""
        if self._logger is None:
            return
        try:
            self._logger.emit(
                "update_check.cache_write_failed", level="WARNING",
                fields={"error": type(error).__name__},
            )
        except (OSError, TypeError, ValueError):
            return


def _record(raw: Any, latest: datetime) -> UpdateCheckRecord:
    """Return the validated record; raise ValueError for any malformed part.

    `latest` is the newest time a stored attempt, success or fact may carry.
    """
    # Accept only the exact schema 1 root
    if not isinstance(raw, dict) or set(raw) != _ROOT_FIELDS:
        raise ValueError("update check cache has the wrong shape")
    if not _is_integer(raw["schema_version"]) or raw["schema_version"] != 1:
        raise ValueError("update check cache has an unknown version")
    last_success_at = raw["last_success_at"]
    if last_success_at is not None:
        _require_utc(last_success_at, latest)
    # Validate every item; the cap is part of the format
    items = raw["items"]
    if not isinstance(items, dict) or len(items) > MAX_CACHED_ITEMS:
        raise ValueError("update check cache items are invalid")
    return UpdateCheckRecord(
        _attempt(raw["last_attempt"], latest), last_success_at,
        {key: _fact(key, value, latest) for key, value in items.items()},
    )


def _attempt(raw: Any, latest: datetime) -> CheckAttempt | None:
    """Return the validated last attempt, or None when no attempt is recorded."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != _ATTEMPT_FIELDS:
        raise ValueError("update check attempt has the wrong shape")
    _require_utc(raw["finished_at"], latest)
    # The error code is null or stable code text
    code = raw["error_code"]
    if code is not None and (not isinstance(code, str) or _ERROR_CODE.fullmatch(code) is None):
        raise ValueError("update check error code is invalid")
    return CheckAttempt(raw["finished_at"], AttemptOutcome(raw["outcome"]), code)


def _fact(key: str, raw: Any, latest: datetime) -> RemoteFact:
    """Return one validated fact; raise ValueError when any field is malformed."""
    if WORKSHOP_ID.fullmatch(key) is None:
        raise ValueError("update check item id is invalid")
    if not isinstance(raw, Mapping) or set(raw) != _ITEM_FIELDS:
        raise ValueError("update check item has the wrong shape")
    result = RemoteItemResult(raw["result"])
    time_updated, file_size = raw["time_updated"], raw["file_size"]
    # A positive time and an optional size belong only to an OK fact
    if result is RemoteItemResult.OK:
        if not _is_integer(time_updated) or time_updated <= 0:
            raise ValueError("update check item time is invalid")
        if file_size is not None and (not _is_integer(file_size) or file_size < 0):
            raise ValueError("update check item size is invalid")
    elif time_updated is not None or file_size is not None:
        raise ValueError("update check item carries data without an OK result")
    _require_utc(raw["checked_at"], latest)
    return RemoteFact(result, time_updated, file_size, raw["checked_at"])


def _require_utc(value: Any, latest: datetime) -> None:
    """Raise ValueError unless the value is ISO-8601 UTC millisecond text not after `latest`."""
    if not isinstance(value, str) or _UTC_TEXT.fullmatch(value) is None:
        raise ValueError("update check timestamp is invalid")
    # Reject impossible calendar values that the pattern lets through
    moment = datetime.fromisoformat(value)
    # A time from the future is a moved clock or a hand-edited file: trust nothing
    if moment > latest:
        raise ValueError("update check timestamp lies in the future")


def _is_integer(value: Any) -> bool:
    """Return whether the value is an integer and not a boolean."""
    return isinstance(value, int) and not isinstance(value, bool)


def _reject_constant(name: str) -> None:
    """Refuse the non-standard JSON constants NaN and Infinity."""
    raise ValueError(f"non-standard JSON constant: {name}")
