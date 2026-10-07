"""Launch evidence and transition phase of one lifecycle service, and the durable ownership record (A6)."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from ..domain.lifecycle import LaunchEvidence, ServerState, canonical_process_path
from ..domain.models import RecordUnavailable, RevisionConflict
from ..repositories.server_ownership import (
    OwnedServer,
    OwnershipRecord,
    OwnershipUnavailable,
    ServerOwnershipRepository,
)
from .lifecycle_ports import ProcessInventoryPort


class LaunchOwnership:
    """Hold the launch evidence and the in-flight transition phase under one lock.

    With a repository, it also writes the ownership record at a launch and clears it
    after a proven stop (owner sessions only), and adopts a server that another
    manager session of the same root started (9.3). Without one, it keeps today's
    in-memory behaviour only.
    """

    def __init__(
        self, repository: ServerOwnershipRepository | None = None, manager_root: Path | None = None, *,
        logger: object | None = None, writable: bool = True, session_id: str = "",
    ) -> None:
        """Start without evidence; bind the record, the canonical manager root, the logger and the write rule."""
        # Protect the evidence and phase fields shared across bridge threads
        self._evidence: LaunchEvidence | None = None
        self._phase: ServerState | None = None
        self._lock = threading.RLock()
        self._repository = repository
        self._root = canonical_manager_root(manager_root) if manager_root is not None else None
        self._logger = logger
        # Observers never write the record (writable false); they adopt in memory only
        self._writable = writable
        self._session_id = session_id
        # Record fields of an adopted server; None for this session's own launch
        self._adopted: OwnedServer | None = None
        # Readiness hint of the next launch: (profile id, query port, RPT folder)
        self._staged: tuple[str, int | None, str | None] | None = None
        self._unreadable_logged = False

    def current(self) -> LaunchEvidence | None:
        """Return the current launch evidence under the lock."""
        with self._lock:
            return self._evidence

    def set(self, evidence: LaunchEvidence | None) -> None:
        """Replace the stored launch evidence under the lock; own evidence is never adopted."""
        with self._lock:
            self._evidence = evidence
            self._adopted = None

    def phase(self) -> ServerState | None:
        """Return the in-flight transition phase under the lock."""
        with self._lock:
            return self._phase

    def set_phase(self, phase: ServerState | None) -> None:
        """Set or clear the in-flight transition phase under the lock."""
        with self._lock:
            self._phase = phase

    def snapshot(self) -> tuple[ServerState | None, LaunchEvidence | None]:
        """Return the transition phase and the launch evidence read together under one lock."""
        with self._lock:
            return self._phase, self._evidence

    def adopted_launch(self) -> OwnedServer | None:
        """Return the record fields of an adopted server, or None for an own launch or no evidence."""
        with self._lock:
            return self._adopted

    def adopt(self, expected_executable: str, inventory: ProcessInventoryPort) -> LaunchEvidence | None:
        """Adopt the server of the record when exactly one live process matches it (9.3); else None.

        Every refusal leaves the state to today's reconciliation: an unreadable record
        never means "stopped".
        """
        if self._repository is None or self._root is None:
            return None
        try:
            record = self._repository.read()
        except (OwnershipUnavailable, OSError) as error:
            self._log_once_unreadable(error)
            return None
        if record is None or record.server is None or record.manager_root != self._root:
            return None
        server = record.server
        expected = canonical_process_path(expected_executable)
        if server.executable_path != expected:
            return None
        # The inventory must be complete and show exactly one candidate with the recorded identity
        try:
            snapshot = inventory.candidates(expected)
        except OSError:
            return None
        if not snapshot.complete:
            return None
        matching = [item for item in snapshot.processes if canonical_process_path(item.executable_path) == expected]
        if len(matching) != 1:
            return None
        process = matching[0]
        if process.pid != server.pid or process.creation_time_ns is None:
            return None
        if process.creation_time_ns != server.creation_time_ns:
            return None
        # Adopted evidence carries no handle token: this session never held a handle
        evidence = LaunchEvidence(server.pid, expected, server.creation_time_ns, server.launch_token, None)
        with self._lock:
            # A launch or a stop of this session that ran meanwhile wins
            if self._evidence is not None or self._phase is not None:
                return self._evidence
            self._evidence = evidence
            self._adopted = server
            return evidence

    def stage_readiness(self, profile_id: str, query_port: int | None, rpt_directory: Path | None) -> None:
        """Keep the readiness hint of the next launch; the record of that launch carries it (9.2)."""
        if not self._writable:
            return
        with self._lock:
            self._staged = (profile_id, query_port, str(rpt_directory) if rpt_directory is not None else None)

    def record_launch(self, evidence: LaunchEvidence, profile_id: str, started_after_ns: int) -> None:
        """Write the record of this session's launch; a failure is logged and never fails the start."""
        with self._lock:
            staged, self._staged = self._staged, None
        if not self._writable or self._repository is None or self._root is None:
            return
        if evidence.creation_time_ns is None:
            # Adoption needs the creation time, so a record without one would never be used
            self._warn("server_ownership.write_failed", "creation_time_missing")
            return
        query_port, rpt_directory = (staged[1], staged[2]) if staged and staged[0] == profile_id else (None, None)
        self._save(OwnershipRecord(self._root, OwnedServer(
            evidence.pid, evidence.executable_path, evidence.creation_time_ns, evidence.launch_token,
            profile_id, started_after_ns, query_port, rpt_directory, self._session_id or "unknown",
        )))

    def clear(self) -> None:
        """Write the record without a server after a proven stop; a failure is logged and ignored."""
        if not self._writable or self._repository is None or self._root is None:
            return
        self._save(OwnershipRecord(self._root, None))

    def _save(self, record: OwnershipRecord) -> None:
        """Save the record over a missing or valid one; log every refusal and failure (9.2)."""
        assert self._repository is not None
        try:
            self._repository.write(record)
        except OwnershipUnavailable as error:
            self._warn("server_ownership.unwritable", str(error))
        except (RevisionConflict, RecordUnavailable, OSError, ValueError) as error:
            self._warn("server_ownership.write_failed", type(error).__name__)

    def _log_once_unreadable(self, error: Exception) -> None:
        """Log an unreadable record once per session."""
        with self._lock:
            if self._unreadable_logged:
                return
            self._unreadable_logged = True
        self._warn("server_ownership.unreadable", str(error) or type(error).__name__)

    def _warn(self, event: str, reason: str) -> None:
        """Write one WARNING line; a logging failure never reaches the lifecycle."""
        emit = getattr(self._logger, "emit", None)
        if emit is None:
            return
        try:
            emit(event, level="WARNING", fields={"reason": reason})
        except (OSError, ValueError):
            pass


def canonical_manager_root(root: Path) -> str:
    """Return the canonical comparison form of a manager root (9.1)."""
    return os.path.normcase(os.path.normpath(str(Path(root).resolve(strict=False))))
