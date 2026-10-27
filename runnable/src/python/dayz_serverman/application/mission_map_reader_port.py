"""Reader port of Inspect: the reader side for one mission-read slice (A181, QF-2).

An observer session binds its folder reader, so each slice holds the reader side.
An owner session has no reader, so its port runs the slice at once.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Any, ContextManager, Protocol

from ..adapters.windows.server_folder_lock import FolderLockUnsupported


class ReaderPort(Protocol):
    """Hold the reader side for one slice, or raise ServerFolderBusy when it stays closed for the bound."""

    def hold(self, bound_seconds: float) -> ContextManager[None]:
        """Return a context that holds the reader side while its block runs."""
        ...


class ReaderUnsupported(RuntimeError):
    """The folder cannot hold the reader side; the observer session refuses the call as unsupported."""


class NoReaderPort:
    """Port of an owner session and of tests: there is no reader side to take."""

    @contextmanager
    def hold(self, bound_seconds: float) -> Iterator[None]:
        """Run the block at once."""
        yield


class DelegatingReaderPort:
    """Port that delegates to the reader of an observer session after `bind`; a no-op before it."""

    def __init__(self) -> None:
        """Start without a reader; `unsupported` keeps the last refusal for the session wrapper."""
        self._reader: Any = None
        self.unsupported: BaseException | None = None

    def bind(self, reader: Any) -> None:
        """Bind the folder reader of the observer session."""
        self._reader = reader

    @contextmanager
    def hold(self, bound_seconds: float) -> Iterator[None]:
        """Hold the bound reader side for the block; a busy writer raises ServerFolderBusy."""
        if self._reader is None:
            yield
            return
        with ExitStack() as stack:
            try:
                stack.enter_context(self._reader.shared(bound_seconds))
            except FolderLockUnsupported as error:
                # The session wrapper turns this into its existing unsupported-lock refusal
                self.unsupported = error
                raise ReaderUnsupported("the server folder cannot hold the reader side") from error
            yield
