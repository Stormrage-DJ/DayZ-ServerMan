"""Hold profile deletion against the desktop editor without a GUI dependency."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol


class EditorGuardPort(Protocol):
    """Reserve a profile for deletion, refusing an editor that still owns it."""

    def hold_for_deletion(self, profile_id: str) -> AbstractContextManager[None]:
        """Keep new editors out until the deletion attempt ends."""
        ...


class NoEditorGuard:
    """Keep command and observer sessions independent of desktop windows."""

    @contextmanager
    def hold_for_deletion(self, profile_id: str) -> Iterator[None]:
        """Allow deletion when no desktop registry is bound."""
        yield


class DelegatingEditorGuard:
    """Bind the desktop registry to the guard already owned by composition."""

    def __init__(self) -> None:
        """Start with a no-op port for command and observer sessions."""
        self._port: EditorGuardPort = NoEditorGuard()
        self._lock = threading.Lock()

    def bind(self, port: EditorGuardPort) -> None:
        """Publish the registry before desktop callbacks can delete a profile."""
        with self._lock:
            self._port = port

    @contextmanager
    def hold_for_deletion(self, profile_id: str) -> Iterator[None]:
        """Retain the selected port through the complete deletion attempt."""
        with self._lock:
            port = self._port
        with port.hold_for_deletion(profile_id):
            yield
