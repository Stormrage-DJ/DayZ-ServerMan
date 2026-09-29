"""Prepublication staging with durable no-publication evidence."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .migration_journal import MigrationJournalRepository
from .migration_payload_identity import (
    MigrationPayloadIdentityError, validate_role_payload,
)
from .migration_destination_plan import (
    MigrationDestinationPlanError, validate_destination_plan,
)
from .migrations import MigrationStorage, MigrationStorageError


@dataclass(frozen=True)
class PublicationTarget:
    """A destination payload waiting to be staged for publication."""
    role: str
    label: str
    path: Path
    payload: bytes


def prepare_publication(
    manager_root: Path, storage: MigrationStorage, journals: MigrationJournalRepository,
    migration_id: str, source_digest: str, fingerprint: str,
    targets: tuple[PublicationTarget, ...], stage: Path,
    hook: Callable[[str], None],
) -> dict[str, object]:
    """Stage publication outputs and recovery evidence and create the journal."""
    if not targets:
        raise MigrationStorageError("Migration has no publication targets.")
    # Collect destination records, prior payloads, and seen identities
    destinations: list[dict[str, object]] = []
    prior_payloads: list[bytes | None] = []
    identities: set[str] = set()
    # Stage each payload and capture the evidence its target needs
    for index, target in enumerate(targets):
        relative = _relative_target(manager_root, target.path)
        # De-duplicate targets under case-insensitive comparison
        identity = relative.casefold()
        if identity in identities:
            raise MigrationStorageError("Migration repeats a publication target.")
        identities.add(identity)
        # Name staged outputs by plan position so order stays verifiable
        staged_relative = f"output/{index:04d}.bin"
        # Validate the new payload against its role schema before writing
        try:
            payload_identity = validate_role_payload(
                target.role, target.label, migration_id, target.payload,
            )
        except MigrationPayloadIdentityError as error:
            raise MigrationStorageError("Migration payload identity is invalid.") from error
        # Write the staged output before reading the prior target bytes
        _write_stage(stage / staged_relative, target.payload)
        prior = target.path.read_bytes() if target.path.exists() else None
        try:
            prior_payload_identity = (
                validate_role_payload(target.role, target.label, migration_id, prior)
                if prior is not None else None
            )
        except MigrationPayloadIdentityError as error:
            raise MigrationStorageError("Migration prior payload identity is invalid.") from error
        # Allocate a recovery slot only when a prior target existed
        recovery_relative = f"recovery/{index:04d}.bin" if prior is not None else None
        prior_payloads.append(prior)
        destinations.append({
            "role": target.role, "label": target.label,
            "payload_identity": payload_identity,
            "prior_payload_identity": prior_payload_identity,
            "target_relative": relative, "staged_relative": staged_relative,
            "recovery_relative": recovery_relative, "prior_exists": prior is not None,
            "prior_sha256": _digest(prior) if prior is not None else None,
            "staged_sha256": _digest(target.payload), "state": "PLANNED",
        })
    # Validate the full ordered plan before persisting any journal
    try:
        validate_destination_plan(migration_id, destinations)
    except MigrationDestinationPlanError as error:
        raise MigrationStorageError("Migration destination plan is invalid.") from error
    # Record the no-publication marker before recovery copies are staged
    storage.write_plan(stage, {
        "schema_version": 1, "kind": "PUBLICATION_OUTPUT_NOT_PUBLISHED",
        "migration_id": migration_id, "publication_started": False,
        "destinations": [{
            key: item[key] for key in (
                "role", "label", "payload_identity", "prior_payload_identity",
                "target_relative", "staged_relative", "prior_exists",
                "prior_sha256", "staged_sha256",
            )
        } for item in destinations],
    })
    hook("PREPUBLICATION_MARKER")
    # Re-verify live targets before staging their recovery copies
    for target, destination, prior in zip(targets, destinations, prior_payloads):
        current = target.path.read_bytes() if target.path.exists() else None
        # Abort when a live target changed after staging began
        if current != prior:
            raise MigrationStorageError("Migration target changed during staging.")
        if prior is not None:
            _write_stage(stage / str(destination["recovery_relative"]), prior)
    hook("RECOVERY_EVIDENCE_STAGED")
    # Create the PREPARED journal as the final durable record
    return journals.create(migration_id, {
        "source_digest": source_digest, "preview_fingerprint": fingerprint,
        "state": "PREPARED", "publication_started": False,
        "committed": False, "resolved": False, "result": None,
        "destinations": destinations,
    })


def _relative_target(manager_root: Path, target: Path) -> str:
    """Return the manager-relative slash path of a target, refusing escapes."""
    resolved = target.resolve(strict=False)
    # The resolved target must stay inside the manager root
    try:
        return resolved.relative_to(manager_root).as_posix()
    except ValueError as error:
        raise MigrationStorageError("Migration target escaped the manager root.") from error


def _write_stage(path: Path, payload: bytes) -> None:
    """Write a staged payload with an exclusive create and fsync."""
    # Ensure the stage directory exists before the exclusive write
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _digest(payload: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of the payload."""
    return hashlib.sha256(payload).hexdigest()
