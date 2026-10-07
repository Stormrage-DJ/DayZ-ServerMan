"""The one write guard of a mod publication: the installation mutex, then a proven stopped server."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Protocol

from ..domain.lifecycle import LifecycleFailure, LifecycleSnapshot, ServerState
from .folder_writer_scope import writer_or_default
from .lifecycle_ports import ServerFolderWriterPort

# Policy of customer decision D10, the only place that holds it: must a writing
# publication WITHOUT a requested start (a plain apply) run inside the guard?
# The customer decided on 2026-10-04: yes. Every apply that writes into the
# server folder takes the installation mutex and needs a proven stopped server.
# False would attempt the apply in any server state, as before the guard. A writing
# publication with a requested start is always guarded, whatever this value is.
PLAIN_APPLY_REQUIRES_GUARD = True


class StatusPort(Protocol):
    """Port that reports the reconciled server state."""
    # Return the current lifecycle snapshot
    def status(self) -> LifecycleSnapshot: ...


class MutexPort(Protocol):
    """Port that serializes manager control of one DayZ installation."""
    # Hold the installation mutex for the duration of the block
    def guard(self, dayz_root: str) -> AbstractContextManager[None]: ...


class InstallationGuard:
    """Hold the installation mutex and require the state STOPPED, as the restores do."""

    def __init__(
        self, lifecycle: StatusPort, mutex: MutexPort, folder_writer: ServerFolderWriterPort | None = None,
    ) -> None:
        """Store the lifecycle status source, the installation mutex and the optional A13 writer side."""
        self._lifecycle = lifecycle
        self._mutex = mutex
        self._folder_writer = writer_or_default(folder_writer)

    def with_folder_writer(self, folder_writer: ServerFolderWriterPort | None) -> InstallationGuard:
        """Return the same guard with the A13 writer side that a recovery takes (3.3)."""
        return InstallationGuard(self._lifecycle, self._mutex, folder_writer)

    @contextmanager
    def stopped(self, dayz_root: Path, *, folder_wait: float | None = None) -> Iterator[None]:
        """Run the block under the mutex with a proven stopped server.

        A busy mutex and every state other than STOPPED raise LifecycleFailure
        before the block runs, so a refusal changes nothing. With `folder_wait`,
        the block also holds the A13 writer side; ServerFolderBusy, a
        LifecycleFailure, refuses after that wait.
        """
        with self._mutex.guard(str(dayz_root)):
            # The state is read under the mutex, so no other manager changes it meanwhile
            state = self._lifecycle.status().state
            if state != ServerState.STOPPED:
                raise LifecycleFailure(
                    _refusal_code(state),
                    f"Applying mods requires STOPPED; current state is {state.value}.",
                )
            if folder_wait is None:
                yield
                return
            # A recovery renames server folders, so no observer may be inside them meanwhile
            with self._folder_writer.exclusive(folder_wait):
                yield


def _refusal_code(state: ServerState) -> str:
    """Map the blocking server state to its failure code."""
    if state == ServerState.RUNNING_EXTERNAL:
        return "EXTERNAL_PROCESS"
    if state in {ServerState.UNKNOWN, ServerState.AMBIGUOUS}:
        return "PROCESS_STATE_UNKNOWN"
    # A managed server that runs, starts or stops is under this manager's control
    return "CONTROL_CONFLICT"
