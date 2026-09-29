"""Fail-closed process reconciliation."""

from __future__ import annotations

from collections.abc import Callable

from ..domain.lifecycle import (
    LaunchEvidence,
    LifecycleSnapshot,
    ServerState,
    canonical_process_path,
)
from .lifecycle_ports import ProcessInventoryPort


def reconcile_server(
    expected_executable: str,
    inventory: ProcessInventoryPort,
    evidence: LaunchEvidence | None,
    handle_is_held: Callable[[str, int], bool],
) -> LifecycleSnapshot:
    """Derive the verified lifecycle state from the live process inventory."""
    # Canonicalize the expected executable path before any comparison
    expected = canonical_process_path(expected_executable)
    # Treat inventory failures as unknown instead of assuming the server is stopped
    try:
        snapshot = inventory.candidates(expected)
    except (OSError, PermissionError):
        return LifecycleSnapshot(ServerState.UNKNOWN, diagnostic_code="INVENTORY_UNAVAILABLE")
    # Refuse to conclude anything from an incomplete inventory
    if not snapshot.complete:
        return LifecycleSnapshot(ServerState.UNKNOWN, diagnostic_code="INVENTORY_INCOMPLETE")
    # Keep only processes whose executable matches the expected path
    matching = tuple(
        process
        for process in snapshot.processes
        if canonical_process_path(process.executable_path) == expected
    )
    # Missing means stopped; several candidates are an ownership ambiguity
    if not matching:
        return LifecycleSnapshot(ServerState.STOPPED)
    # Multiple candidates must never be managed automatically
    if len(matching) > 1:
        return LifecycleSnapshot(ServerState.AMBIGUOUS, diagnostic_code="PROCESS_AMBIGUOUS")
    process = matching[0]
    # Accept management only when launch evidence proves this exact process
    if evidence is not None and evidence.proves(process, handle_is_held):
        return LifecycleSnapshot(ServerState.RUNNING_MANAGED, process.pid)
    # Unproven ownership is reported as an external process
    return LifecycleSnapshot(
        ServerState.RUNNING_EXTERNAL,
        process.pid,
        "PROCESS_OWNERSHIP_UNPROVEN",
    )

