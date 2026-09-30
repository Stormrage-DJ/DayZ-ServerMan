"""Bounded read-only access to manager-owned diagnostic logs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from pathlib import Path
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError


# Log sources exposed through the bridge
LOG_SOURCES = frozenset(("manager", "manager_diagnostics", "server"))
# Upper bound for a single log request to keep responses bounded
MAX_LINES = 1000
# Only this many bytes of the file tail are read per request
TAIL_BYTES = 512 * 1024


class LogQueryService:
    """Serve bounded tail reads over the manager and server logs."""

    def __init__(self, manager_log: Path, server_log: Path) -> None:
        """Map log source names to their managed file paths."""
        self._paths = {
            "manager": manager_log,
            "manager_diagnostics": manager_log,
            "server": server_log,
        }

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the bridge handler table for log queries."""
        return {"read_log": self.read_log}

    def read_log(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return the newest lines of the selected log and its truncation state."""
        if set(parameters) != {"source", "maximum_lines"}:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "log parameters are invalid")
        source = parameters["source"]
        maximum = parameters["maximum_lines"]
        # Accept only known sources and limits within the response bound
        if source not in LOG_SOURCES:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "log source is invalid")
        if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= MAX_LINES:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "log line limit is invalid")
        path = self._paths[source]
        # Treat an unreadable file as a retryable storage failure
        try:
            content, truncated = _tail_text(path)
        except OSError as error:
            raise ApplicationCallError(
                ErrorCode.STORAGE_FAILURE,
                "The selected log could not be read.",
                retryable=True,
            ) from error
        # Return the newest lines and flag truncation in either form
        lines = content.splitlines()
        if source == "manager":
            lines = _manager_activity(lines)
        return {
            "source": source,
            "lines": lines[-maximum:],
            "truncated": truncated or len(lines) > maximum,
            "revision": _revision(path),
        }


def _tail_text(path: Path) -> tuple[str, bool]:
    """Read at most the last bounded chunk of a text log."""
    # A missing or non-file target reads as empty and untruncated
    if not path.is_file():
        return "", False
    # Clamp the read window to the configured tail size
    size = path.stat().st_size
    offset = max(0, size - TAIL_BYTES)
    with path.open("rb") as stream:
        stream.seek(offset)
        data = stream.read(TAIL_BYTES)
    # Drop the partial first line unless the window starts at byte zero
    if offset:
        separator = data.find(b"\n")
        data = data[separator + 1 :] if separator >= 0 else b""
    return data.decode("utf-8", errors="replace"), offset > 0


def _revision(path: Path) -> str:
    """Return a cheap opaque identity for the current log contents."""
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _manager_activity(lines: list[str]) -> list[str]:
    """Turn structured diagnostics into concise operator-facing activity."""
    activity: list[str] = []
    for line in lines:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(record, dict):
            continue
        rendered = _format_manager_record(record)
        if rendered is not None:
            activity.append(rendered)
    return activity


def _format_manager_record(record: Mapping[str, Any]) -> str | None:
    """Format one relevant manager record or hide routine diagnostics."""
    event = record.get("event")
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    level = str(record.get("level", "INFO"))
    timestamp = str(record.get("occurred_at", "")).replace("T", " ")[:19]
    prefix = f"{timestamp}  {level:<7}".strip()
    if event in {"bridge.request", "bridge.success", "operation.progress",
                 "shutdown.requested", "shutdown.closed", "operation_lane.draining",
                 "schedule.started", "schedule.stopped"}:
        return None
    if event == "operation.state":
        state = fields.get("state")
        if state not in {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}:
            return None
        action = _display_name(fields.get("kind", "Operation"))
        if state == "SUCCEEDED":
            message = f"{action} completed."
        elif state == "CANCELLED":
            message = f"{action} was cancelled."
        else:
            detail = fields.get("error_message") or fields.get("error_code") or "Unknown error"
            message = f"{action} failed: {detail}"
        return f"{prefix}  {message}"
    if event == "bridge.failure":
        method = _display_name(fields.get("method", "Request"))
        detail = fields.get("message") or fields.get("error_code") or "Unknown error"
        return f"{prefix}  {method} failed: {detail}"
    if isinstance(event, str):
        message = _display_name(event)
        details = ", ".join(
            f"{_display_name(key)}: {value}" for key, value in fields.items()
            if value not in (None, "")
        )
        return f"{prefix}  {message}{f' — {details}' if details else ''}"
    return None


def _display_name(value: object) -> str:
    """Convert an event or operation identifier into readable words."""
    return str(value).replace(".", " ").replace("_", " ").strip().title()
