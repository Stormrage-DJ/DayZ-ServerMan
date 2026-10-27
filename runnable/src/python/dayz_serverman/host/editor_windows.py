"""Own one editor per profile and serialize opening, deletion and application close."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..adapters.windows.window_work_area import editor_geometry, window_work_area
from ..application.profile_deletion import ProfileDeletionError
from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from .editor_assets import compose_editor_html
from .editor_close import EditorCloseController
from .map_editor_window_api import MissionMapEditorApi


# Each approved destroy can wait this many seconds for the closed event
DESTROY_TIMEOUT = 5.0


@dataclass
class EditorWindowEntry:
    """Retain the immutable profile identity and its current native lifecycle."""

    window: Any
    api: MissionMapEditorApi
    close: EditorCloseController
    state: str = "OPEN"
    # Native size state from the window events; a minimized editor remembers whether it was maximized
    minimized: bool = False
    maximized: bool = False


class EditorWindowRegistry:
    """Provide both native window ownership and the profile deletion guard."""

    def __init__(self, webview: Any, main_window: Any, profiles: Any, bridge: Any,
                 frontend_root: Path, logger: Any, *, hidden: bool = False,
                 work_area: Callable[[Any], tuple[int, int, int, int]] = window_work_area,
                 selections: Any = None, dialogs: tuple[Any, Any] | None = None,
                 protected: Callable[[], Any] | None = None) -> None:
        """Bind the composition services without creating an editor before start.

        `selections` is the image selection service that each editor's image dialog registers with. `dialogs`
        and `protected` are the open and save dialog constants and the protected roots of Export plan (9.1).
        """
        self._webview = webview
        self._main_window = main_window
        self._profiles = profiles
        self._bridge = bridge
        self._frontend_root = frontend_root
        self._logger = logger
        self._hidden = hidden
        self._work_area = work_area
        self._selections = selections
        self._dialogs, self._protected = dialogs, protected
        self._lock = threading.RLock()
        self._entries: dict[str, EditorWindowEntry] = {}
        self._deleting: set[str] = set()
        self._closing = False
        self._started = False

    def mark_started(self) -> None:
        """Allow editor creation from the callback that runs after webview.start."""
        with self._lock:
            self._started = True

    def open(self, profile_id: object) -> dict[str, bool]:
        """Read the profile first, then atomically create or focus its editor."""
        with self._lock:
            profile = self._profiles.read(profile_id)
            identifier = profile.values.profile_id
            # The same lock reserves deletion and excludes new windows during application close
            if self._closing:
                raise ApplicationCallError(ErrorCode.MUTATION_CONFLICT, "The application is closing.",
                                           details={"reason": "application_closing", "cause": "SHUTTING_DOWN"})
            if identifier in self._deleting:
                raise ApplicationCallError(ErrorCode.MUTATION_CONFLICT, "This profile is being deleted.",
                                           retryable=True, details={"reason": "profile_deleting"})
            if identifier in self._entries:
                window = self._entries[identifier].window
                window.restore()
                window.show()
                return {"opened": False, "focused": True}
            if not self._started:
                raise RuntimeError("webview has not started")
            # Size the new document on the main window's monitor and bind only its profile
            api = MissionMapEditorApi(self._bridge, identifier, self._selections, dialogs=self._dialogs,
                                      protected=self._protected)
            window = self._webview.create_window(
                f"Map editor - {profile.values.display_name}",
                html=compose_editor_html(self._frontend_root), js_api=api,
                **editor_geometry(self._work_area(self._main_window)),
                min_size=(960, 600), resizable=True, hidden=self._hidden,
                confirm_close=False, text_select=True, zoomable=False)
            if window is None:
                raise RuntimeError("pywebview did not create an editor")
            api._bind_window(window)
            controller = EditorCloseController(
                window, identifier, profile.values.display_name, self._logger,
                lambda state: self._set_state(identifier, state, entry),
                lambda: self._close_editor(identifier, entry))
            entry = EditorWindowEntry(window, api, controller)
            self._entries[identifier] = entry
            window.events.closing += controller.on_closing
            window.events.minimized += lambda: self._size_state(entry, True, entry.maximized)
            window.events.maximized += lambda: self._size_state(entry, False, True)
            window.events.restored += lambda: self._size_state(entry, False, False)
            window.events.closed += lambda: self._remove(identifier, entry)
            # A very fast native close may precede event registration
            if window.events.closed.is_set():
                self._remove(identifier, entry)
            return {"opened": True, "focused": False}

    def _set_state(self, profile_id: str, state: str, entry: EditorWindowEntry | None = None) -> None:
        """Publish controller state only while its profile entry still exists."""
        with self._lock:
            if profile_id in self._entries and (entry is None or self._entries[profile_id] is entry):
                self._entries[profile_id].state = state

    @staticmethod
    def _size_state(entry: EditorWindowEntry, minimized: bool, maximized: bool) -> None:
        """Keep the size state that the native window reports."""
        entry.minimized, entry.maximized = minimized, maximized

    @staticmethod
    def _present(entry: EditorWindowEntry) -> None:
        """Show an editor for its close dialog; a minimized one returns to its earlier size (QA-5.4-F2)."""
        if entry.minimized:
            # restore() sets the normal size, so an editor that was maximized is maximized again instead
            if entry.maximized:
                entry.window.maximize()
            else:
                entry.window.restore()
        entry.window.show()

    def _remove(self, profile_id: str, entry: EditorWindowEntry) -> None:
        """Remove only the closed generation, drop its pending import file and unblock its settle waiters."""
        entry.api._forget_import()
        entry.close.on_closed()
        with self._lock:
            if self._entries.get(profile_id) is entry:
                del self._entries[profile_id]

    def _close_editor(self, profile_id: str, expected: EditorWindowEntry) -> None:
        """Destroy a native-close editor unless an application attempt owns settlement."""
        with self._lock:
            entry = self._entries.get(profile_id)
            if entry is expected and entry.state == "CLOSING" and not self._closing:
                entry.close.destroy_approved()

    @contextmanager
    def hold_for_deletion(self, profile_id: str) -> Iterator[None]:
        """Reserve deletion before lane work and release even after failed preflight."""
        with self._lock:
            if profile_id in self._entries:
                raise ProfileDeletionError("Close the map editor of this profile first.")
            if profile_id in self._deleting:
                raise ProfileDeletionError("This profile is being deleted.")
            self._deleting.add(profile_id)
        try:
            yield
        finally:
            with self._lock:
                self._deleting.remove(profile_id)

    def begin_close(self) -> tuple[EditorWindowEntry, ...]:
        """Refuse opening and capture editors in their original opening order."""
        with self._lock:
            self._closing = True
            return tuple(self._entries.values())

    def end_close(self) -> None:
        """Release all documents before allowing another open or native close."""
        with self._lock:
            for entry in tuple(self._entries.values()):
                entry.close.release_close()
            self._closing = False

    def settle_for_application(self) -> bool:
        """Show and settle every editor, releasing all of them on cancellation."""
        for entry in self.begin_close():
            if entry.close.closed.is_set():
                continue
            self._present(entry)
            if entry.close.settle("application") == "cancel":
                self.end_close()
                return False
        return True

    def destroy_all(self) -> None:
        """Approve owned editor destruction and bound each native closed wait."""
        for entry in self.begin_close():
            if entry.close.closed.is_set():
                continue
            entry.close.destroy_approved()
            if not entry.close.closed.wait(DESTROY_TIMEOUT):
                self._logger.emit("map_editor.destroy_timeout", level="WARNING",
                                  fields={"profile_id": entry.close.profile_id})
