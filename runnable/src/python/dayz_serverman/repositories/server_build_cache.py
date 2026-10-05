"""Disposable persisted outcome and branch facts of the DayZ server build check.

The file holds no installation fact, path, account or SteamCMD output. It
shares the atomic write and the validators of the mod check cache.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

from ..domain.server_build import BRANCH_PATTERN, MAX_BRANCHES, MAX_BUILD_ID, BranchFact, BuildCheckRecord
from .update_check_cache import (
    MAX_FUTURE_SECONDS,
    UpdateCheckCacheRepository,
    _attempt,
    _is_integer,
    _reject_constant,
    _require_utc,
)

# A larger file is ignored as a whole
MAX_FILE_BYTES = 64 * 1024
_ROOT_FIELDS = {"schema_version", "last_attempt", "last_success_at", "branches"}
_BRANCH_FIELDS = {"build_id", "time_updated"}
_BRANCH = re.compile(BRANCH_PATTERN)


class ServerBuildCacheRepository(UpdateCheckCacheRepository):
    """Persist the build check record in one JSON file that may be lost at any time."""

    def load(self) -> BuildCheckRecord | None:  # type: ignore[override]
        """Return the stored record, or None ("never checked") for any unusable file."""
        try:
            with self._path.open("rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                return None
            latest = self._clock() + timedelta(seconds=MAX_FUTURE_SECONDS)
            return _record(json.loads(raw.decode("utf-8"), parse_constant=_reject_constant), latest)
        except (OSError, ValueError, TypeError, RecursionError):
            return None

    def save(self, record: BuildCheckRecord) -> bool:  # type: ignore[override]
        """Write the record atomically; return False and log when the write fails."""
        try:
            attempt = record.last_attempt
            kept = list(record.branches.items())[:MAX_BRANCHES]
            self._write({
                "schema_version": 1,
                "last_attempt": attempt.to_dict() if attempt is not None else None,
                "last_success_at": record.last_success_at,
                "branches": {name: fact.to_dict() for name, fact in kept},
            })
            return True
        except (OSError, ValueError, TypeError) as error:
            self._log_failure(error)
            return False

    def _log_failure(self, error: Exception) -> None:
        """Emit the write-failure event of the build check and swallow logging failures."""
        if self._logger is None:
            return
        try:
            self._logger.emit(
                "server_build.cache_write_failed", level="WARNING",
                fields={"error_type": type(error).__name__},
            )
        except (OSError, TypeError, ValueError):
            return


def _record(raw: Any, latest: Any) -> BuildCheckRecord:
    """Return the validated record; raise ValueError for any malformed part."""
    if not isinstance(raw, dict) or set(raw) != _ROOT_FIELDS:
        raise ValueError("server build cache has the wrong shape")
    if not _is_integer(raw["schema_version"]) or raw["schema_version"] != 1:
        raise ValueError("server build cache has an unknown version")
    if raw["last_success_at"] is not None:
        _require_utc(raw["last_success_at"], latest)
    branches = raw["branches"]
    if not isinstance(branches, dict) or len(branches) > MAX_BRANCHES:
        raise ValueError("server build cache branches are invalid")
    return BuildCheckRecord(
        _attempt(raw["last_attempt"], latest), raw["last_success_at"],
        {name: _branch(name, value) for name, value in branches.items()},
    )


def _branch(name: Any, raw: Any) -> BranchFact:
    """Return one validated branch fact."""
    if not isinstance(name, str) or _BRANCH.fullmatch(name) is None:
        raise ValueError("server build cache branch name is invalid")
    if not isinstance(raw, dict) or set(raw) != _BRANCH_FIELDS:
        raise ValueError("server build cache branch has the wrong shape")
    build, time_updated = raw["build_id"], raw["time_updated"]
    if not _is_integer(build) or not 1 <= build <= MAX_BUILD_ID:
        raise ValueError("server build cache build id is invalid")
    if time_updated is not None and (not _is_integer(time_updated) or time_updated <= 0):
        raise ValueError("server build cache branch time is invalid")
    return BranchFact(build, time_updated)
