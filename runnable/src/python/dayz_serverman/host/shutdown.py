"""Safe pywebview close coordination."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from ..application.shutdown import ShutdownCoordinator, ShutdownState


class SafeCloseController:
    """Coordinate an approved window close with the shutdown coordinator."""
    def __init__(self, window: Any, shutdown: ShutdownCoordinator) -> None:
        """Store the window and shutdown services with close state guards."""
        self._window = window
        self._shutdown = shutdown
        # Track the worker threads and approval flag shared between attempts
        self._close_request: threading.Thread | None = None
        self._watcher: threading.Thread | None = None
        self._lock = threading.Lock()
        self._close_approved = False

    def on_closing(self) -> bool:
        """Veto the native event until an asynchronous close is approved."""
        # Let a previously approved close proceed
        with self._lock:
            if self._close_approved:
                return True
        # Veto this attempt and run the close handshake off-thread
        self._start_close_request()
        return False

    def _start_close_request(self) -> None:
        """Start the close-request worker unless one is already running."""
        with self._lock:
            # Reuse an in-flight request instead of stacking workers
            if self._close_request is not None and self._close_request.is_alive():
                return
            self._close_request = threading.Thread(
                target=self._request_close,
                name="dayz-serverman-close-request",
                daemon=True,
            )
            self._close_request.start()

    def _request_close(self) -> None:
        """Run the close handshake from the request worker thread."""
        # Abort when the shell still holds unsaved work
        if not self._frontend_allows_close():
            return
        # Drain backend work and publish the shutdown state
        snapshot = self._shutdown.request_shutdown()
        self._render(snapshot.to_dict())
        # Close at once when nothing remains to stop
        if snapshot.state == ShutdownState.CLOSED:
            self._destroy_approved()
            return
        # Otherwise watch until the backend finishes closing
        self._start_watcher()

    def close_after_shutdown(self, timeout: float) -> bool:
        """Drain the backend and close an owned non-interactive window."""
        # Request the drain and wait for it within the timeout
        snapshot = self._shutdown.request_shutdown()
        if snapshot.state != ShutdownState.CLOSED:
            snapshot = self._shutdown.wait_for_close(timeout)
        self._render(snapshot.to_dict())
        if snapshot.state != ShutdownState.CLOSED:
            return False
        self._destroy_approved()
        return True

    def _frontend_allows_close(self) -> bool:
        """Ask the shell whether it can release the close."""
        evaluate = getattr(self._window, "evaluate_js", None)
        # Treat windows without a script bridge as not ready
        if evaluate is None:
            return False
        try:
            return evaluate("window.ServerManTransitions.requestNativeClose()") is True
        except Exception:
            return False

    def _start_watcher(self) -> None:
        """Start the shutdown watcher unless one is already running."""
        with self._lock:
            # Reuse a running watcher instead of stacking threads
            if self._watcher is not None and self._watcher.is_alive():
                return
            self._watcher = threading.Thread(
                target=self._wait_and_close,
                name="dayz-serverman-safe-close",
                daemon=True,
            )
            self._watcher.start()

    def _wait_and_close(self) -> None:
        """Poll the shutdown state and destroy the window once it closes."""
        # Keep polling until the backend reports closed
        while True:
            snapshot = self._shutdown.wait_for_close(0.1)
            self._render(snapshot.to_dict())
            if snapshot.state == ShutdownState.CLOSED:
                self._destroy_approved()
                return
            time.sleep(0.1)

    def _destroy_approved(self) -> None:
        """Approve future closes and destroy the native window."""
        # Approve the close before destroying the window
        with self._lock:
            self._close_approved = True
        self._window.destroy()

    def _render(self, snapshot: dict[str, str | None]) -> None:
        """Push one shutdown snapshot into the browser UI."""
        evaluate = getattr(self._window, "evaluate_js", None)
        if evaluate is None:
            return
        # Serialize the snapshot for the script call
        payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        evaluate(f"window.ServerManUi.renderShutdown({payload})")
