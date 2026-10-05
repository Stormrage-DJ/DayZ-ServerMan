"""The `server_build` part of the update status: installed read memo and the bridge view."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any

from ..domain.models import ManagerSettings
from ..domain.server_build import (
    BranchFact,
    BuildState,
    InstalledRead,
    Ownership,
    build_status,
    ownership_class,
)
from ..domain.update_check_rules import CheckState
from ..observability.structured_log import StructuredLogger
from ..repositories.server_build_manifest import read_installed

# The installed build is read again at most this often on status reads, in seconds
MEMO_SECONDS = 10.0


class InstalledMemo:
    """Read the installed manifest at most once per memo period per configured folder pair."""

    def __init__(
        self, monotonic: Callable[[], float], logger: StructuredLogger | None = None,
        reader: Callable[[str | None, str | None], InstalledRead] = read_installed,
        memo_seconds: float = MEMO_SECONDS,
    ) -> None:
        """Store the reader, the clock and the logger; nothing is read yet."""
        self._reader, self._monotonic, self._logger = reader, monotonic, logger
        self._memo_seconds = memo_seconds
        self._lock = threading.Lock()
        self._key: tuple[str | None, str | None] | None = None
        self._value: InstalledRead | None = None
        self._read_at = 0.0
        self._logged: tuple[str, str | None, str] | None = None

    def read(self, settings: ManagerSettings, *, force: bool = False) -> InstalledRead:
        """Return the installed read; the I/O runs without the lock held."""
        key = (settings.dayz_root, settings.steamcmd_root)
        with self._lock:
            fresh = self._monotonic() - self._read_at < self._memo_seconds
            if not force and key == self._key and self._value is not None and fresh:
                return self._value
        value = self._reader(*key)
        summary = (value.unknown_reason or "FOUND", value.signals.layout, ownership_class(value).value)
        with self._lock:
            self._key, self._value, self._read_at = key, value, self._monotonic()
            changed, self._logged = summary != self._logged, summary
        if changed:
            self._log(summary)
        return value

    def _log(self, summary: tuple[str, str | None, str]) -> None:
        """Log a changed result of the installed read: codes only, never a path or a value."""
        if self._logger is None:
            return
        try:
            self._logger.emit("server_build.installed_read", fields={
                "result": summary[0], "layout": summary[1], "ownership": summary[2],
            })
        except (OSError, TypeError, ValueError):
            return


def server_build_view(
    read: InstalledRead, check: Mapping[str, Any], branches: Mapping[str, BranchFact],
    *, steamcmd_configured: bool,
) -> dict[str, object]:
    """Return the `server_build` object of `get_update_status` (detailed design 14.8)."""
    state = CheckState(check["check_state"])
    status = build_status(
        read, state, branches, has_success=check["last_success_at"] is not None and bool(branches),
        steamcmd_configured=steamcmd_configured,
    )
    build = read.build
    return {
        "state": status.state.value,
        "reason": status.reason,
        "ownership": ownership_class(read).value,
        "installed_build": build.build_id if build is not None else None,
        "installed_branch": build.branch if build is not None else None,
        "target_build": status.target_build,
        "available_build": status.available_build,
        "available_time": status.available_time,
        "check_state": state.value,
        "checked_at": check["checked_at"],
        "last_success_at": check["last_success_at"],
        "error_code": check["error_code"],
        "checking": check["checking"],
        "waiting": check["waiting"],
        "revision": check["revision"],
        "paused": check["paused"],
    }


def internal_view(check: Mapping[str, Any] | None = None) -> dict[str, object]:
    """Return the view of an internal error; `server_build` never fails the status call."""
    values = check or {}
    return {
        "state": BuildState.COULD_NOT_CHECK.value, "reason": "FAILED",
        "ownership": Ownership.UNKNOWN.value, "installed_build": None, "installed_branch": None,
        "target_build": None, "available_build": None, "available_time": None,
        "check_state": CheckState.FAILED.value, "checked_at": values.get("checked_at"),
        "last_success_at": values.get("last_success_at"), "error_code": "INTERNAL",
        "checking": bool(values.get("checking", False)), "waiting": bool(values.get("waiting", False)),
        "revision": int(values.get("revision", 0)),
        "paused": bool(values.get("paused", False)),
    }
