"""Cooperative progress and cancellation boundary for operation work."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from .models import OperationCancelled, PendingOperation

if TYPE_CHECKING:
    from .manager import OperationManager


class OperationContext:
    """Expose cooperative progress checkpoints and cancellation to operation work."""
    def __init__(self, manager: OperationManager, pending: PendingOperation) -> None:
        """Bind the context to its manager and pending operation record."""
        self._manager = manager
        self._pending = pending

    @property
    def operation_id(self) -> str:
        """Return the public identifier of the pending operation."""
        return self._pending.record.operation_id

    @property
    def cancellation_requested(self) -> bool:
        """Report whether cancellation has been requested for the operation."""
        return self._pending.cancellation.is_set()

    def checkpoint(self, phase: str, progress_percent: int) -> None:
        """Report progress and stop at a safe point when cancellation was requested."""
        # Reject progress values outside the documented 0-100 contract
        if not 0 <= progress_percent <= 100:
            raise ValueError("progress_percent must be from 0 through 100")
        # Forward the checkpoint to the owning manager for publication
        self._manager._publication.progress(self._pending, phase, progress_percent)
        # Stop only at declared safe points so partial work stays recoverable
        if self._pending.cancellation.is_set() and phase in self._pending.safe_points:
            raise OperationCancelled("operation cancelled at a declared safe point")

    def record_evidence(self, evidence: Mapping[str, object]) -> None:
        """Persist bounded operation evidence before an external wait."""
        self._manager._publication.evidence(self._pending, evidence)

    def publish_detail(self, items: Iterable[Mapping[str, object]]) -> None:
        """Offer advisory per-item progress; at most one value per second is published."""
        self._manager._publication.detail(self._pending, items)

    def block_for_recovery(self, message: str) -> None:
        """Ask the manager to block the lane until recovery completes."""
        self._manager.block_for_recovery(message)
