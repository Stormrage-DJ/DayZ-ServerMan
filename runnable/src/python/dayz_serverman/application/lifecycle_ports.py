"""Platform ports used by server lifecycle orchestration."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from ..domain.lifecycle import InventorySnapshot, LaunchEvidence


@dataclass(frozen=True)
class LaunchRequest:
    """Everything the launcher needs for one direct server process."""
    argv: tuple[str, ...]
    working_directory: Path
    log_path: Path
    launch_token: str


@dataclass(frozen=True)
class LaunchReceipt:
    """Process identity returned by the launcher, with optional ownership proof."""
    pid: int
    creation_time_ns: int | None
    handle_token: str | None


class ProcessInventoryPort(Protocol):
    """List candidate processes for the expected executable."""

    # List live candidate processes for the expected executable
    def candidates(self, expected_executable: str) -> InventorySnapshot: ...


class ProcessLauncherPort(Protocol):
    """Launch the server and manage the retained process handle."""

    # Start the server process and return its receipt
    def launch(self, request: LaunchRequest) -> LaunchReceipt: ...

    # Report whether the retained handle still matches the process
    def retains_handle(self, handle_token: str, pid: int) -> bool: ...

    # Close a retained handle when ownership is no longer needed
    def release_handle(self, handle_token: str) -> None: ...


class GracefulStopPort(Protocol):
    """Request a graceful stop for a proven server process."""

    # Ask the proven process to shut down gracefully
    def request_stop(self, evidence: LaunchEvidence) -> None: ...


class InstallationMutexPort(Protocol):
    """Serialize lifecycle operations for one DayZ root."""

    # Hold the install lock for the duration of the context
    def guard(self, dayz_root: str) -> AbstractContextManager[None]: ...


# Type alias for the environment mapping handed to process launchers
Environment = Mapping[str, str]
