"""Validate durable migration payload identity before target reconciliation."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .migration_payload_identity import (
    MigrationPayloadIdentityError, validate_role_payload,
)
from .migrations import MigrationStorageError


def validate_payload_evidence(
    manager_root: Path, document: dict[str, object], stage: Path,
) -> None:
    """Verify staged or terminal payload evidence for every destination."""
    # Capture the journal state that decides which evidence must exist
    state = str(document["state"])
    migration_id = str(document["migration_id"])
    terminal = state in {"COMMITTED", "ROLLED_BACK"}
    # Check each destination against the evidence its state requires
    for destination in document["destinations"]:  # type: ignore[union-attr]
        staged = stage / str(destination["staged_relative"])
        if staged.is_file():
            _validate_file(staged, destination, migration_id, prior=False)
        elif not terminal:
            raise MigrationStorageError("Migration staged payload evidence is missing.")
        # Terminal journals prove their live targets instead
        if terminal:
            _validate_terminal(manager_root, destination, migration_id, state)
        elif destination["prior_exists"]:
            recovery = stage / str(destination["recovery_relative"])
            _validate_file(recovery, destination, migration_id, prior=True)


def _validate_terminal(
    manager_root: Path, destination: dict[str, object], migration_id: str, state: str,
) -> None:
    """Verify the live target bytes for a terminal migration state."""
    target = _target(manager_root, destination)
    # A rolled-back new target must not exist any more
    if state == "ROLLED_BACK" and destination["prior_exists"] is False:
        if target.exists():
            raise MigrationStorageError("Rolled-back migration target should be absent.")
        return
    # Otherwise the target must hold the recorded prior or staged bytes
    if not target.is_file():
        raise MigrationStorageError("Terminal migration payload evidence is missing.")
    _validate_bytes(
        target.read_bytes(), destination, migration_id, prior=state == "ROLLED_BACK",
    )


def _validate_file(
    path: Path, destination: dict[str, object], migration_id: str, *, prior: bool,
) -> None:
    """Verify that one evidence file exists and matches its digest."""
    if not path.is_file():
        raise MigrationStorageError("Migration payload evidence is missing.")
    _validate_bytes(path.read_bytes(), destination, migration_id, prior=prior)


def _validate_bytes(
    payload: bytes, destination: dict[str, object], migration_id: str, *, prior: bool,
) -> None:
    """Compare payload bytes against the recorded digest and identity."""
    # Select the prior or staged evidence columns from the prior flag
    digest_key = "prior_sha256" if prior else "staged_sha256"
    identity_key = "prior_payload_identity" if prior else "payload_identity"
    if hashlib.sha256(payload).hexdigest() != destination[digest_key]:
        raise MigrationStorageError("Migration payload digest is invalid.")
    # Re-derive the role identity so a swapped payload cannot pass
    try:
        identity = validate_role_payload(
            str(destination["role"]), str(destination["label"]), migration_id, payload,
        )
    except MigrationPayloadIdentityError as error:
        raise MigrationStorageError("Migration payload schema is invalid.") from error
    if identity != destination[identity_key]:
        raise MigrationStorageError("Migration payload identity is invalid.")


def _target(manager_root: Path, destination: dict[str, object]) -> Path:
    """Resolve a target path and refuse escapes from the manager root."""
    resolved = (manager_root / Path(str(destination["target_relative"]))).resolve(strict=False)
    # The resolved target must stay inside the manager root
    try:
        resolved.relative_to(manager_root)
    except ValueError as error:
        raise MigrationStorageError("Migration target escaped the manager root.") from error
    return resolved
