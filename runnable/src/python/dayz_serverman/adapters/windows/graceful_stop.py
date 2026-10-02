"""Verified non-forced Windows close request for a managed DayZ process."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable

from ...application.lifecycle_ports import ProcessInventoryPort
from ...domain.lifecycle import LaunchEvidence, LifecycleFailure, canonical_process_path


class WindowsGracefulStop:
    """Mirror the legacy manager's `taskkill /PID` save-and-close behavior."""

    def __init__(
        self,
        inventory: ProcessInventoryPort,
        *,
        timeout_seconds: float = 120.0,
        poll_interval: float = 0.25,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """Store the inventory port and the injectable timing and runner hooks."""
        self._inventory = inventory
        self._timeout_seconds = timeout_seconds
        self._poll_interval = poll_interval
        self._runner = runner
        self._clock = clock
        self._sleeper = sleeper

    def request_stop(self, evidence: LaunchEvidence) -> None:
        """Send the save-and-close request and wait for the process to exit."""
        # Prove the recorded process identity before anything is signalled
        self._require_exact_process(evidence)
        try:
            result = self._runner(
                ["taskkill.exe", "/PID", str(evidence.pid)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                shell=False,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise LifecycleFailure(
                "STOP_REQUEST_FAILED",
                "The DayZ save-and-close request could not be sent.",
                retryable=True,
            ) from error
        if result.returncode != 0:
            # A rejected request is still a success when the process already vanished
            if self._process_is_gone(evidence):
                return
            raise LifecycleFailure(
                "STOP_REQUEST_FAILED",
                "Windows rejected the DayZ save-and-close request.",
                retryable=True,
            )

        # Wait for the process to disappear within the save-and-close window
        deadline = self._clock() + self._timeout_seconds
        last_unknown: LifecycleFailure | None = None
        while self._clock() < deadline:
            try:
                if self._process_is_gone(evidence):
                    return
                last_unknown = None
            except LifecycleFailure as error:
                # Exit can make identity temporarily unreadable; never treat uncertainty as absence.
                if error.code != "PROCESS_STATE_UNKNOWN" or not error.retryable:
                    raise
                last_unknown = error
            self._sleeper(self._poll_interval)
        # Preserve an unresolved inventory failure rather than claiming a successful stop.
        if last_unknown is not None:
            raise last_unknown
        raise LifecycleFailure(
            "STOP_TIMEOUT",
            "DayZ did not exit before the save-and-close timeout.",
            retryable=True,
        )

    def _require_exact_process(self, evidence: LaunchEvidence) -> None:
        """Refuse to stop unless the recorded process identity is still exact."""
        # Without a creation timestamp the process cannot be proven as ours
        if evidence.creation_time_ns is None:
            raise LifecycleFailure(
                "PROCESS_OWNERSHIP_UNPROVEN",
                "The managed DayZ process identity cannot be proven for stopping.",
                recovery_required=True,
            )
        process = self._find_process(evidence)
        # The process disappeared between inventory checks
        if process is None:
            raise LifecycleFailure(
                "PROCESS_STATE_UNKNOWN",
                "The managed DayZ process disappeared before the stop request.",
                retryable=True,
            )
        # Reject a reused process id whose executable or creation time differs
        if (
            canonical_process_path(process.executable_path) != evidence.executable_path
            or process.creation_time_ns != evidence.creation_time_ns
        ):
            raise LifecycleFailure(
                "PROCESS_OWNERSHIP_UNPROVEN",
                "The DayZ process identity changed before the stop request.",
                recovery_required=True,
            )

    def _process_is_gone(self, evidence: LaunchEvidence) -> bool:
        """Report process absence while rejecting identity changes during the wait."""
        process = self._find_process(evidence)
        if process is None:
            return True
        # An identity change mid-wait means the process was replaced; fail hard
        if (
            canonical_process_path(process.executable_path) != evidence.executable_path
            or process.creation_time_ns != evidence.creation_time_ns
        ):
            raise LifecycleFailure(
                "PROCESS_OWNERSHIP_UNPROVEN",
                "Process identity changed while waiting for DayZ to stop.",
                recovery_required=True,
            )
        return False

    def _find_process(self, evidence: LaunchEvidence):
        """Look up the recorded process id in a complete process inventory."""
        # An unavailable inventory makes the process state unknowable
        try:
            snapshot = self._inventory.candidates(evidence.executable_path)
        except (OSError, PermissionError) as error:
            raise LifecycleFailure(
                "PROCESS_STATE_UNKNOWN",
                "The DayZ process inventory is unavailable.",
                retryable=True,
            ) from error
        # An incomplete scan cannot prove ownership; report the state as unknown
        if not snapshot.complete:
            raise LifecycleFailure(
                "PROCESS_STATE_UNKNOWN",
                "The DayZ process inventory is incomplete.",
                retryable=True,
            )
        return next((item for item in snapshot.processes if item.pid == evidence.pid), None)
