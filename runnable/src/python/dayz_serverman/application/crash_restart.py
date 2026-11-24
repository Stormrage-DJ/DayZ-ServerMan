"""Daemon thread that polls the crash restart flow while the owner window is open."""

from __future__ import annotations

import threading

from .crash_restart_flow import CrashRestartFlow, CrashRestartState

# Seconds between two status polls
CRASH_POLL_SECONDS = 5.0


class CrashRestartMonitor:
    """Run the flow poll in one background thread; it stops with the window."""

    def __init__(self, flow: CrashRestartFlow, *, poll_seconds: float = CRASH_POLL_SECONDS) -> None:
        """Bind the flow and the poll period; no thread runs yet."""
        self._flow = flow
        self._poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the daemon thread once; a second call while it is alive does nothing."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._worker_loop, name="dayz-serverman-crash-restart", daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> bool:
        """Stop the thread; return True when it ended within the timeout or never ran."""
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        self._stop.set()
        thread.join(timeout)
        stopped = not thread.is_alive()
        if stopped:
            with self._lock:
                self._thread = None
        return stopped

    def is_running(self) -> bool:
        """Return True while the thread is alive."""
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def tick(self) -> None:
        """Run one poll of the flow."""
        self._flow.tick()

    def read(self) -> CrashRestartState | None:
        """Return the last crash state of the flow."""
        return self._flow.read()

    def cancel(self) -> bool:
        """Drop a pending restart; True when one was pending."""
        return self._flow.cancel()

    def clear(self) -> bool:
        """Drop a pending restart and the stored state; True when something was dropped."""
        return self._flow.clear()

    def _worker_loop(self) -> None:
        """Poll, then wait for the next period or the stop signal."""
        while not self._stop.is_set():
            self._flow.tick()
            self._stop.wait(self._poll_seconds)
