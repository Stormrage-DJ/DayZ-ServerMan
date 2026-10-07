"""The Ctrl+C handler of a CLI run (design 6.7): a flag outside a question, KeyboardInterrupt at a question."""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class Interrupts:
    """Records Ctrl+C; never raises outside a question, so no synchronous write is cut."""

    def __init__(self) -> None:
        """Start without a request and outside a question."""
        self._requested = threading.Event()
        self._asking = False
        self._previous: Any = None
        self._installed = False

    @property
    def requested(self) -> bool:
        """Report whether Ctrl+C was pressed and not yet taken."""
        return self._requested.is_set()

    def take(self) -> bool:
        """Return whether Ctrl+C was pressed, and clear the request (the waiter of 3.2 reads it)."""
        pressed = self._requested.is_set()
        self._requested.clear()
        return pressed

    def handle(self, _signal_number: int, _frame: object) -> None:
        """SIGINT handler: raise only while a question waits for input, else set the flag."""
        if self._asking:
            raise KeyboardInterrupt
        self._requested.set()

    @contextmanager
    def question(self) -> Iterator[None]:
        """Mark the time while a confirmation question waits; Ctrl+C then raises KeyboardInterrupt."""
        self._asking = True
        try:
            yield
        finally:
            self._asking = False

    def install(self) -> None:
        """Install the handler; outside the main thread (some tests) the default handling stays."""
        try:
            self._previous = signal.signal(signal.SIGINT, self.handle)
            self._installed = True
        except ValueError:
            self._installed = False

    def restore(self) -> None:
        """Put back the handler that was there before `install`."""
        if self._installed:
            signal.signal(signal.SIGINT, self._previous)
            self._installed = False
