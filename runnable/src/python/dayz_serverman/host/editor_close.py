"""Settle one editor through its Promise callback before native destruction."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


# The accepted acknowledgement and native destruction limits are seconds
ACK_TIMEOUT = 5.0


@dataclass
class _SettleAttempt:
    """Share one answer among all callers of an in-flight settle."""

    done: threading.Event = field(default_factory=threading.Event)
    answer: str | None = None
    reason: str = "editor"


class EditorCloseController:
    """Refuse native close until the document or administrator allows it."""

    def __init__(self, window: Any, profile_id: str, display_name: str, logger: Any,
                 state_changed: Callable[[str], None], close_requested: Callable[[], None]) -> None:
        """Retain one window and the registry callbacks that own its state."""
        self.window = window
        self.profile_id = profile_id
        self.display_name = display_name
        self._logger = logger
        self._state_changed = state_changed
        self._close_requested = close_requested
        self._lock = threading.Lock()
        self._attempt: _SettleAttempt | None = None
        self._close_worker: threading.Thread | None = None
        self._approved = False
        self.closed = threading.Event()

    def on_closed(self) -> None:
        """End any callback wait when the native window has closed."""
        self.closed.set()

    def on_closing(self) -> bool:
        """Refuse an unapproved native event and reuse the editor close worker."""
        with self._lock:
            if self._approved:
                return True
            if self._close_worker is None or not self._close_worker.is_alive():
                self._close_worker = threading.Thread(target=self._request_close, daemon=True,
                                                      name="dayz-serverman-editor-close")
                self._close_worker.start()
        return False

    def _request_close(self) -> None:
        """Ask only this editor to close; the registry never shuts down here."""
        if self.settle("editor") == "close":
            self._close_requested()

    def settle(self, reason: str) -> str:
        """Wait for one final answer, with five seconds only for acknowledgement."""
        # Join an existing attempt so native and application closes cannot duplicate dialogs
        with self._lock:
            owner = self._attempt is None
            if owner:
                self._attempt = _SettleAttempt(reason=reason)
            attempt = self._attempt
            # An application close that joins an editor settle changes the open dialog to its form (UI-UX 00:53:00)
            joined = not owner and reason == "application" and attempt.reason != reason and not attempt.done.is_set()
            if joined:
                attempt.reason = reason
        assert attempt is not None
        if owner:
            self._state_changed("SETTLING")
            answer = "close" if not self.window.events.loaded.is_set() else self._document_answer(reason)
            attempt.answer = answer
            self._state_changed("CLOSING" if answer == "close" else "OPEN")
            attempt.done.set()
            if answer == "cancel":
                with self._lock:
                    if self._attempt is attempt:
                        self._attempt = None
        else:
            if joined:
                self._notify(reason)
            while not attempt.done.wait(0.05):
                if self.closed.is_set():
                    return "close"
        return attempt.answer or "close"

    def _document_answer(self, reason: str) -> str:
        """Separate the immediate acknowledgement from the final Promise answer."""
        acknowledged = threading.Event()
        answered = threading.Event()
        result: dict[str, Any] = {}
        callback_lock = threading.Lock()

        def callback(value: object) -> None:
            """Keep the first Promise result, including an invalid value."""
            with callback_lock:
                if not answered.is_set():
                    result["answer"] = value
                    answered.set()

        def evaluate() -> None:
            """Call the native script bridge off-thread so acknowledgement can time out."""
            try:
                result["ack"] = self.window.evaluate_js(
                    f"window.ServerManMapEditor.settleForClose('{reason}')", callback)
            except Exception:
                result["cause"] = "evaluation_error"
            finally:
                result["acknowledged_at"] = time.monotonic()
                acknowledged.set()

        deadline = time.monotonic() + ACK_TIMEOUT
        threading.Thread(target=evaluate, daemon=True, name="dayz-serverman-editor-settle").start()
        # A closed window cannot retain edits or hold an acknowledgement wait
        while not acknowledged.wait(max(0.0, min(0.05, deadline - time.monotonic()))):
            if self.closed.is_set():
                return "close"
            if time.monotonic() >= deadline:
                return self._confirm("acknowledgement_timeout")
        if self.closed.is_set():
            return "close"
        if result["acknowledged_at"] > deadline:
            return self._confirm("acknowledgement_timeout")
        if result.get("ack") is not True:
            return self._confirm(result.get("cause", "unknown_acknowledgement"))
        # The administrator's eventual document dialog has no final-answer deadline
        while not answered.wait(0.05):
            if self.closed.is_set():
                return "close"
        answer = result["answer"]
        return answer if answer in ("close", "cancel") else self._confirm("unknown_callback")

    def _notify(self, reason: str) -> None:
        """Pass the joining reason to the document; the answer of its shared settle ends both waits.

        The join form only changes a running settle in the document and never starts one (QA-5.4-F7).
        """
        if self.closed.is_set() or not self.window.events.loaded.is_set():
            return

        def evaluate() -> None:
            """Call the script bridge off-thread; a failure keeps the editor form of the dialog."""
            try:
                self.window.evaluate_js(f"window.ServerManMapEditor.settleForClose('{reason}', true)")
            except Exception:
                self._logger.emit("map_editor.settle_failed", level="WARNING",
                                  fields={"profile_id": self.profile_id, "cause": "join_error"})

        threading.Thread(target=evaluate, daemon=True, name="dayz-serverman-editor-join").start()

    def _confirm(self, cause: str) -> str:
        """Report the failed settle and ask on the editor's own native window."""
        self._logger.emit("map_editor.settle_failed", level="WARNING",
                          fields={"profile_id": self.profile_id, "cause": cause})
        text = (f"The map editor for {self.display_name} does not respond. "
                "Changes that are not saved may be lost. Close it?")
        if self.closed.is_set():
            return "close"
        try:
            return "close" if self.window.create_confirmation_dialog("DayZ-ServerMan", text) else "cancel"
        except Exception:
            # A failed native dialog has no administrator approval to discard edits
            return "cancel"

    def release_close(self) -> None:
        """Release the document after an application close is cancelled."""
        with self._lock:
            self._attempt = None
        if not self.closed.is_set() and self.window.events.loaded.is_set():
            try:
                self.window.evaluate_js("window.ServerManMapEditor.releaseClose()")
            except Exception:
                self._logger.emit("map_editor.settle_failed", level="WARNING",
                                  fields={"profile_id": self.profile_id, "cause": "release_error"})
        self._state_changed("OPEN")

    def destroy_approved(self) -> None:
        """Allow the destruction event without running another settle."""
        with self._lock:
            self._approved = True
        self._state_changed("CLOSING")
        self.window.destroy()
