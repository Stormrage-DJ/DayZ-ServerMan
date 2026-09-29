"""Durable JSON-lines logging with correlation and redaction."""

from __future__ import annotations

import json
import os
import re
import threading
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from ..security.sensitive import is_sensitive_name, redact_argument_tokens, redact_text

# Correlation identifier carried across nested calls in the current context
_CORRELATION_ID: ContextVar[str | None] = ContextVar("correlation_id", default=None)
# Event names must stay lowercase joins and dot paths for stable filtering
_EVENT_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,95}")


def _utc_now() -> str:
    """Return the current UTC time as a millisecond-precision ISO string."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class CorrelationScope:
    """Attach a correlation identifier to everything inside the context."""
    def __init__(self, correlation_id: str) -> None:
        """Store the identifier to activate on scope entry."""
        self._correlation_id = correlation_id
        self._token: Token[str | None] | None = None

    def __enter__(self) -> None:
        """Push the correlation identifier onto the context variable."""
        self._token = _CORRELATION_ID.set(self._correlation_id)

    def __exit__(self, _type: object, _value: object, _traceback: object) -> bool:
        """Restore the correlation identifier this scope replaced."""
        assert self._token is not None
        _CORRELATION_ID.reset(self._token)
        return False


def current_correlation_id() -> str | None:
    """Return the active correlation identifier, if any."""
    return _CORRELATION_ID.get()


class StructuredLogger:
    """Append redacted structured records to a JSON-lines log file."""
    def __init__(self, path: Path) -> None:
        """Resolve the log path and prepare the append lock."""
        self.path = path.resolve(strict=False)
        # Serialize appends so concurrent writers never interleave records
        self._lock = threading.Lock()

    def emit(
        self,
        event: str,
        *,
        level: str = "INFO",
        correlation_id: str | None = None,
        operation_id: str | None = None,
        fields: Mapping[str, Any] | None = None,
    ) -> None:
        """Append one structured record after validating and redacting it."""
        # Reject event names that would break stable log filtering
        if _EVENT_NAME.fullmatch(event) is None:
            raise ValueError("log event name is invalid")
        # Redact field values and fall back to the ambient correlation identifier
        record = {
            "schema_version": 1,
            "occurred_at": _utc_now(),
            "level": level,
            "event": event,
            "correlation_id": correlation_id or current_correlation_id(),
            "operation_id": operation_id,
            "fields": _redact(dict(fields or {})),
        }
        payload = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Append under the lock so records from concurrent threads stay whole
        with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            # Flush each record to disk before returning
            os.fsync(stream.fileno())

    def flush(self) -> None:
        """Records are flushed on every append; acquire the lock as a barrier."""
        with self._lock:
            return


def _redact(value: Any, key: str = "") -> Any:
    """Return a redacted copy of one field value for safe logging."""
    # Field names that look sensitive are masked whatever their value
    if is_sensitive_name(key):
        return "[REDACTED]"
    # Redact nested mappings by their own keys
    if isinstance(value, dict):
        return {str(item_key): _redact(item, str(item_key)) for item_key, item in value.items()}
    # String sequences may be command arguments, so redact them token-wise
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return redact_argument_tokens(value)
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    # Render unsupported values as text so the record stays JSON-serializable
    return str(value)
