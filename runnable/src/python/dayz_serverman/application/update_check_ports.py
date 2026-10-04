"""Outbound port used by the remote Workshop update check."""

from __future__ import annotations

from typing import Protocol, Sequence

from ..domain.update_check import RemoteBatchResult


class WorkshopRemoteCatalogPort(Protocol):
    """Report remote update time, size and availability for a bounded id set."""

    # Send one request for 1 to 200 ids within the remaining seconds; never raises
    def fetch(self, ids: Sequence[str], deadline_seconds: float) -> RemoteBatchResult: ...
