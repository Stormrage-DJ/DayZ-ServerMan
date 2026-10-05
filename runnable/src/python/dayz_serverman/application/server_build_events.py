"""Structured events of the server build check: codes, counts and public build ids only.

Never logged: SteamCMD output, paths, manifest values other than build ids and
the state, the account name, the branch name and exception texts (14.11).
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from ..observability.structured_log import StructuredLogger
from .server_build_run import RunOutcome


class BuildCheckEvents:
    """Emit the check events; a skip is logged once per due period."""

    def __init__(self, logger: StructuredLogger | None) -> None:
        """Store the optional logger."""
        self._logger = logger
        self._lock = threading.Lock()
        self._skip_logged = False

    def started(self, trigger: str) -> None:
        """Log the start of a run and open a new due period for skips."""
        with self._lock:
            self._skip_logged = False
        self._log("server_build.check_started", {"trigger": trigger})

    def skipped(self, reason: str) -> None:
        """Log the first skip of a due period only."""
        with self._lock:
            first, self._skip_logged = not self._skip_logged, True
        if first:
            self._log("server_build.check_skipped", {"reason": reason})

    def completed(self, outcome: RunOutcome, view: Mapping[str, Any]) -> None:
        """Log the outcome of a run, and an unproven exit at level error."""
        self._log("server_build.check_completed", {
            "outcome": "OK" if outcome.error_code is None else "FAILED", "code": outcome.error_code,
            "exit_code": outcome.exit_code, "duration_ms": outcome.duration_ms,
            "branch_count": len(outcome.branches),
            "branch_kind": "PUBLIC" if view["installed_branch"] in (None, "public") else "OTHER",
            "state": view["state"], "installed_build": view["installed_build"],
            "available_build": view["available_build"],
        }, "INFO" if outcome.error_code is None else "WARNING")
        if outcome.unproven:
            self._log("server_build.steamcmd_exit_unproven", {"duration_ms": outcome.duration_ms}, "ERROR")

    def _log(self, event: str, fields: Mapping[str, Any], level: str = "INFO") -> None:
        """Emit one structured event and swallow logging failures."""
        if self._logger is None:
            return
        try:
            self._logger.emit(event, level=level, fields=fields)
        except (OSError, TypeError, ValueError):
            return
