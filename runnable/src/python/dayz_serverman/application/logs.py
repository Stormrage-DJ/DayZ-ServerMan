"""Bounded read-only access to manager-owned diagnostic logs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import open_shared
from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from .log_activity import manager_activity


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
            content, truncated = _tail_text(path, whole_records=source != "server")
        except OSError as error:
            raise ApplicationCallError(
                ErrorCode.STORAGE_FAILURE,
                "The selected log could not be read.",
                retryable=True,
            ) from error
        # Return the newest lines and flag truncation in either form
        lines = content.splitlines()
        if source == "manager":
            lines = manager_activity(lines)
        return {
            "source": source,
            "lines": lines[-maximum:],
            "truncated": truncated or len(lines) > maximum,
            "revision": _revision(path),
        }


def _tail_text(path: Path, whole_records: bool = False) -> tuple[str, bool]:
    """Read at most the last bounded chunk of a text log."""
    # A missing or non-file target reads as empty and untruncated
    if not path.is_file():
        return "", False
    # Clamp the read window to the configured tail size
    size = path.stat().st_size
    offset = max(0, size - TAIL_BYTES)
    # A rotation between the size check and the open also reads as empty and untruncated
    try:
        with open_shared(path) as stream:
            stream.seek(offset)
            data = stream.read(TAIL_BYTES)
    except FileNotFoundError:
        return "", False
    # Drop the partial first line unless the window starts at byte zero
    if offset:
        separator = data.find(b"\n")
        data = data[separator + 1 :] if separator >= 0 else b""
    # An append-only record log drops a last line that a writer has not finished yet
    if whole_records and not data.endswith(b"\n"):
        data = data[: data.rfind(b"\n") + 1]
    return data.decode("utf-8", errors="replace"), offset > 0


def _revision(path: Path) -> str:
    """Return a cheap opaque identity for the current log contents."""
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    return f"{stat.st_size}:{stat.st_mtime_ns}"
