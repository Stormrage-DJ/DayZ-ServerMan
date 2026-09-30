"""Server-process observations and ownership evidence."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable


class ServerState(str, Enum):
    """Lifecycle state derived from server process observations."""

    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING_MANAGED = "RUNNING_MANAGED"
    RUNNING_EXTERNAL = "RUNNING_EXTERNAL"
    STOPPING = "STOPPING"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS = "AMBIGUOUS"


class ServerReadiness(str, Enum):
    """Application readiness of a verified manager-owned DayZ process."""

    STARTING = "STARTING"
    READY = "READY"
    UNRESPONSIVE = "UNRESPONSIVE"


def canonical_process_path(path: str | os.PathLike[str]) -> str:
    """Return the canonical comparison form of a process path."""
    # Resolve and case-fold so Windows path spellings compare equal
    resolved = Path(path).resolve(strict=False)
    return os.path.normcase(os.path.normpath(str(resolved)))


@dataclass(frozen=True)
class ProcessObservation:
    """One observed server-candidate process with identity evidence."""

    pid: int
    executable_path: str
    creation_time_ns: int | None = None
    launch_token: str | None = None


@dataclass(frozen=True)
class InventorySnapshot:
    """Set of observed processes with a completeness flag."""

    processes: tuple[ProcessObservation, ...]
    complete: bool = True


@dataclass(frozen=True)
class LaunchEvidence:
    """Evidence recorded when the manager launched a server process."""

    pid: int
    executable_path: str
    creation_time_ns: int | None
    launch_token: str
    handle_token: str | None

    def proves(
        self,
        process: ProcessObservation,
        handle_is_held: Callable[[str, int], bool],
    ) -> bool:
        """Return whether the process matches any recorded ownership evidence."""
        # PID and executable path must match before deeper proof
        if self.pid != process.pid:
            return False
        if self.executable_path != canonical_process_path(process.executable_path):
            return False
        # Creation time, launch token, or a held handle can each prove ownership
        creation_matches = (
            self.creation_time_ns is not None
            and process.creation_time_ns is not None
            and self.creation_time_ns == process.creation_time_ns
        )
        token_matches = (
            process.launch_token is not None
            and process.launch_token == self.launch_token
        )
        handle_matches = (
            self.handle_token is not None
            and handle_is_held(self.handle_token, process.pid)
        )
        return creation_matches or token_matches or handle_matches

    def to_dict(self) -> dict[str, object]:
        """Return the evidence as its persisted object."""
        return {
            "pid": self.pid,
            "executable_path": self.executable_path,
            "creation_time_ns": self.creation_time_ns,
            "launch_token": self.launch_token,
            "handle_token": self.handle_token,
        }


@dataclass(frozen=True)
class LifecycleSnapshot:
    """Snapshot of the lifecycle state with diagnostics."""

    state: ServerState
    process_id: int | None = None
    diagnostic_code: str | None = None
    readiness: ServerReadiness | None = None
    query_port: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the snapshot as its persisted object."""
        return {
            "state": self.state.value,
            "process_id": self.process_id,
            "diagnostic_code": self.diagnostic_code,
            "readiness": self.readiness.value if self.readiness is not None else None,
            "query_port": self.query_port,
        }


class LifecycleFailure(RuntimeError):
    """Lifecycle error carrying a safe message and recovery flags."""

    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        recovery_required: bool = False,
        retryable: bool = False,
    ) -> None:
        """Store the failure code, safe message, and recovery flags."""
        self.code = code
        self.safe_message = safe_message
        self.recovery_required = recovery_required
        self.retryable = retryable
        super().__init__(safe_message)
