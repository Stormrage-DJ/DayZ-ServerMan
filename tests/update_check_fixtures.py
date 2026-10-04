"""Reusable fakes for update-check tests: clock, catalog, cache and service builder."""
from __future__ import annotations

import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application.update_check import UpdateCheckService  # noqa: E402
from dayz_serverman.domain.update_check import (  # noqa: E402
    RemoteBatchFailure,
    RemoteBatchResult,
    RemoteItem,
    RemoteItemResult,
    UpdateCheckRecord,
)

# Fixed start of the fake wall clock
START = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)
# Remote update time every fake OK answer carries unless a test overrides it
REMOTE_TIME = 1_700_000_000


class FakeClock:
    """Wall clock that moves only when a test advances it."""

    def __init__(self) -> None:
        """Start at the fixed moment."""
        self.now = START

    def __call__(self) -> datetime:
        """Return the current fake time."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward, or backward for a negative value."""
        self.now += timedelta(seconds=seconds)


class FakeCatalog:
    """Remote catalog double that never opens a connection."""

    def __init__(self) -> None:
        """Start with OK answers, no failure and no blocking."""
        self.calls: list[tuple[tuple[str, ...], float]] = []
        self.failures: list[RemoteBatchFailure | None] = []
        self.results: dict[str, RemoteItemResult] = {}
        self.times: dict[str, int] = {}
        self.error: Exception | None = None
        self.entered = threading.Event()
        self.release: threading.Event | None = None

    def block(self) -> threading.Event:
        """Make every fetch wait until the returned event is set."""
        self.release = threading.Event()
        return self.release

    def fetch(self, ids, deadline_seconds):
        """Record the batch and return the prepared answer."""
        self.calls.append((tuple(ids), deadline_seconds))
        self.entered.set()
        # Hold the run inside the transport while a test inspects the service
        if self.release is not None:
            self.release.wait(10)
        if self.error is not None:
            raise self.error
        failure = self.failures.pop(0) if self.failures else None
        if failure is not None:
            return RemoteBatchResult(failure=failure)
        return RemoteBatchResult(items=tuple(self._item(key) for key in ids))

    def _item(self, key: str) -> RemoteItem:
        """Return the answer of one id."""
        result = self.results.get(key, RemoteItemResult.OK)
        if result is not RemoteItemResult.OK:
            return RemoteItem(key, result)
        return RemoteItem(key, result, self.times.get(key, REMOTE_TIME), 4096)


class FakeCache:
    """In-memory cache double that records every write."""

    def __init__(self, record: UpdateCheckRecord | None = None) -> None:
        """Store the record a load returns."""
        self.record = record
        self.saves: list[tuple[UpdateCheckRecord, frozenset[str]]] = []

    def load(self) -> UpdateCheckRecord | None:
        """Return the stored record."""
        return self.record

    def save(self, record, configured_ids) -> bool:
        """Record the write and report success."""
        self.saves.append((record, frozenset(configured_ids)))
        return True


class Harness:
    """One service with its fakes; runs are inline unless threads are requested."""

    def __init__(self, ids=("111", "222"), *, threaded: bool = False, record=None,
                 wait_seconds: float = 5.0, cache=None) -> None:
        """Build the service around a fake clock, catalog and cache."""
        self.clock = FakeClock()
        self.catalog = FakeCatalog()
        self.cache = cache if cache is not None else FakeCache(record)
        self.ids = list(ids)
        self.enabled: object = True
        self.id_error: Exception | None = None
        options = {} if threaded else {"start_worker": lambda work: work()}
        self.service = UpdateCheckService(
            self.catalog, self.cache, self._ids, automatic_enabled=lambda: self.enabled,
            clock=self.clock, wait_seconds=wait_seconds, **options,
        )

    def _ids(self):
        """Return the configured ids, or raise the prepared profile error."""
        if self.id_error is not None:
            raise self.id_error
        return list(self.ids)

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Wait until no run is active; return whether that happened in time."""
        end = threading.Event()
        for _ in range(int(timeout / 0.01)):
            if not self.service.snapshot().checking:
                return True
            end.wait(0.01)
        return False
