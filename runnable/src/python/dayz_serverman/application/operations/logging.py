"""Best-effort structured logging for durable operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...observability.structured_log import StructuredLogger
from .models import PendingOperation


class OperationLog:
    """Emit best-effort structured events for the operation lane."""
    def __init__(self, logger: StructuredLogger | None) -> None:
        """Store the optional structured logger used for emission."""
        self._logger = logger

    def emit(
        self,
        event: str,
        pending: PendingOperation | None = None,
        fields: Mapping[str, Any] | None = None,
        *,
        level: str = "INFO",
    ) -> None:
        """Emit a structured event, degrading silently when logging fails."""
        # Skip emission when no logger is configured
        if self._logger is None:
            return
        try:
            # Forward the event with correlation and operation identifiers
            self._logger.emit(
                event,
                level=level,
                correlation_id=pending.correlation_id if pending is not None else None,
                operation_id=pending.record.operation_id if pending is not None else None,
                fields=fields,
            )
        except (OSError, TypeError, ValueError):
            # Logging failures must never disturb operation work
            return

