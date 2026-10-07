"""Acquire-once scope of the server-folder writer side (A13) that one owner step can hand to the next."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

from ..adapters.windows.server_folder_lock import NO_FOLDER_LOCK
from .lifecycle_ports import ServerFolderWriterPort


# Owner wait for the reads in progress before a swap step (OD7: about 5 s, then refuse)
OWNER_WRITER_WAIT_SECONDS = 5.0
# Wait of a startup recovery before it takes today's block path
STARTUP_RECOVERY_WAIT_SECONDS = 30.0


def writer_or_default(writer: ServerFolderWriterPort | None) -> ServerFolderWriterPort:
    """Return the wired writer, or the lock-free default when none is wired."""
    return NO_FOLDER_LOCK if writer is None else writer


class WriterScope:
    """One exclusive hold that is taken at most once and released idempotently."""

    def __init__(self, writer: ServerFolderWriterPort | None, bound_seconds: float | None = None) -> None:
        """Keep the writer; a bound of None means the owner wait, read when the scope is taken."""
        self._writer = writer_or_default(writer)
        self._bound = bound_seconds
        self._context: AbstractContextManager[None] | None = None
        self.acquisitions = 0

    @property
    def held(self) -> bool:
        """Report whether the scope holds the writer side now."""
        return self._context is not None

    def acquire(self) -> None:
        """Take the writer side once; a second call while held does nothing; refusal raises ServerFolderBusy."""
        if self._context is not None:
            return
        bound = OWNER_WRITER_WAIT_SECONDS if self._bound is None else self._bound
        context = self._writer.exclusive(bound)
        context.__enter__()
        self._context = context
        self.acquisitions += 1

    def release(self) -> None:
        """Release the writer side when held; any later call does nothing."""
        context, self._context = self._context, None
        if context is not None:
            context.__exit__(None, None, None)

    @contextmanager
    def held_for(self) -> Iterator[None]:
        """Hold the writer side for the block and release it on every path."""
        self.acquire()
        try:
            yield
        finally:
            self.release()

    def relay_after_acquire(
        self, phase: str, checkpoint: Callable[[str, int], None],
    ) -> Callable[[str, int], None]:
        """Wrap a checkpoint: at `phase`, take the writer side before the phase is relayed."""
        def relayed(reported: str, percent: int) -> None:
            """Acquire first, so a refusal leaves the operation at its last staging phase."""
            if reported == phase:
                self.acquire()
            checkpoint(reported, percent)
        return relayed
