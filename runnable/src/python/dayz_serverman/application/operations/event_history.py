"""Bounded in-memory operation event history."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from .models import EventCursorExpired, OperationEvent


class EventHistory:
    """Retain recent events by both count and elapsed age."""

    def __init__(
        self,
        *,
        count_limit: int,
        age_limit_seconds: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Configure the retention limits and the clock used for aging."""
        # Validate both retention limits before the history is used
        if count_limit < 1:
            raise ValueError("count_limit must be positive")
        if age_limit_seconds <= 0:
            raise ValueError("age_limit_seconds must be positive")
        self._count_limit = count_limit
        self._age_limit_seconds = age_limit_seconds
        # Default to the monotonic clock so age limits ignore wall-clock jumps
        self._clock = clock or time.monotonic
        self._entries: deque[tuple[float, OperationEvent]] = deque()

    def append(self, event: OperationEvent) -> None:
        """Add an event and trim history to the configured limits."""
        now = self._clock()
        # Drop entries that already exceeded the age limit
        self._prune(now)
        self._entries.append((now, event))
        # Trim the oldest entries beyond the count limit
        while len(self._entries) > self._count_limit:
            self._entries.popleft()

    def read(
        self,
        after_sequence: int,
        maximum: int,
        current_sequence: int,
    ) -> tuple[tuple[OperationEvent, ...], int]:
        """Return retained events after the cursor plus the cursor for the next read."""
        self._prune(self._clock())
        # Refuse cursors that point beyond the newest published sequence
        if after_sequence > current_sequence:
            raise ValueError("event cursor exceeds the current sequence")
        # Refuse cursors whose events already fell out of the retained window
        if self._cursor_is_expired(after_sequence, current_sequence):
            raise EventCursorExpired("event cursor is older than retained history")
        # Collect retained events after the cursor, capped by the page size
        events = tuple(
            event
            for _, event in self._entries
            if event.sequence > after_sequence
        )[:maximum]
        # Advance the cursor to the last returned event, or to the current sequence
        next_cursor = events[-1].sequence if events else max(after_sequence, current_sequence)
        return events, next_cursor

    def _cursor_is_expired(self, after_sequence: int, current_sequence: int) -> bool:
        """Report whether the cursor sits outside the retained history window."""
        if self._entries:
            return after_sequence < self._entries[0][1].sequence - 1
        return current_sequence > after_sequence

    def _prune(self, now: float) -> None:
        """Drop entries older than the age limit relative to the given time."""
        while self._entries and now - self._entries[0][0] > self._age_limit_seconds:
            self._entries.popleft()
