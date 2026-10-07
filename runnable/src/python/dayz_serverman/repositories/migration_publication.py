"""Journaled publication and recovery for copy-only legacy imports."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Callable, Iterable

from ..adapters.windows.shared_files import read_bytes_shared, write_bytes_atomically
from .migration_destination_plan import (
    MigrationDestinationPlanError, validate_destination_plan,
)
from .migration_journal import MigrationJournalError, MigrationJournalRepository
from .migration_payload_evidence import validate_payload_evidence
from .migration_recovery import classify_destinations, orphan_stage_is_proven_prepublication
from .migration_staging import PublicationTarget, prepare_publication
from .migrations import MigrationStorage, MigrationStorageError
# Observer invoked around publication phases
PublicationHook = Callable[[str], None]
# Observer invoked when unresolved recovery must block mutations
RecoveryBlock = Callable[[str], None]
# Migration identifiers are 32-character lowercase hex values
MIGRATION_ID = re.compile(r"[0-9a-f]{32}")


class MigrationPublication:
    """Publish staged migration output with journaled rollback and recovery."""

    def __init__(
        self, manager_root: Path, storage: MigrationStorage,
        journals: MigrationJournalRepository, hook: PublicationHook | None = None,
        block_recovery: RecoveryBlock | None = None,
    ) -> None:
        """Store the manager root, storage, journals, and optional observers."""
        self._manager_root = manager_root.resolve(strict=False)
        self._storage = storage
        self._journals = journals
        self._hook = hook
        self._block_recovery = block_recovery

    def publish(
        self, migration_id: str, source_digest: str, fingerprint: str,
        targets: Iterable[PublicationTarget],
    ) -> None:
        """Publish every staged destination and commit or roll back the journal."""
        # Stage all outputs and prior evidence before touching live targets
        stage = self._storage.create_stage(migration_id)
        try:
            document = prepare_publication(
                self._manager_root, self._storage, self._journals,
                migration_id, source_digest, fingerprint, tuple(targets), stage,
                self._call,
            )
        except Exception:
            self._storage.cleanup(stage)
            raise
        try:
            self._call("JOURNAL_PREPARED")
            # Mark the journal publishing before the first live mutation
            document = self._journals.update(
                document, state="PUBLISHING", publication_started=True,
            )
            # Replace each destination from its verified staged payload
            for index, destination in enumerate(document["destinations"]):
                self._call(f"BEFORE_PUBLISH_{destination['role']}")
                self._replace_from_stage(stage, destination)
                self._call(f"AFTER_REPLACE_{destination['role']}")
                # Persist each destination state right after its replacement
                destinations = [dict(item) for item in document["destinations"]]
                destinations[index]["state"] = "PUBLISHED"
                document = self._journals.update(document, destinations=destinations)
                self._call(f"PUBLISH_{destination['role']}")
                if destination["role"] == "REPORT":
                    self._call("REPORT")
            # Prove every published target before committing the journal
            self._verify_published(document)
            document = self._journals.update(
                document, state="COMMITTED", committed=True, resolved=True,
                result="COMMITTED",
            )
            self._call("COMMITTED")
            # Retire so recovery never replays a completed publication
            self._retire(document, stage)
        except Exception as error:
            # Roll back from the persisted journal, not the in-memory document
            try:
                current = self._journals.read(self._journals.path(migration_id))
                self._compensate(current, stage)
            except Exception as recovery_error:
                # Block mutations when rollback cannot be proven
                self._mark_recovery_required(migration_id)
                raise MigrationStorageError(
                    "Migration publication requires recovery.", recovery_required=True,
                ) from recovery_error
            raise

    def inspect_recovery(self) -> dict[str, object]:
        """Recover interrupted publications and orphan stages at startup."""
        # Collect recoverable identifiers separately from unresolved blockers
        blocked: list[str] = []
        recovered: list[str] = []
        # Walk active and retired journals so retirement stays idempotent
        journal_paths = (*self._journals.active_paths(), *self._journals.retired_paths())
        journal_ids = {path.stem for path in journal_paths}
        # Reconcile each journal according to its recorded state
        for path in journal_paths:
            document: dict[str, object] | None = None
            try:
                document = self._journals.read(path)
                migration_id = document["migration_id"]
                self._validate_plan(document)
                stage = self._stage(migration_id)
                validate_payload_evidence(self._manager_root, document, stage)
                # Retired journals only need their evidence verified
                if path.suffix == ".retired":
                    self._finish_retired(document, stage)
                elif document["state"] == "COMMITTED":
                    self._verify_published(document)
                    self._retire(document, stage)
                elif document["state"] == "ROLLED_BACK":
                    self._verify_prior(document)
                    self._retire(document, stage)
                else:
                    self._compensate(document, stage)
                recovered.append(migration_id)
            except Exception:
                if document is not None and path.suffix == ".json":
                    self._mark_recovery_required(str(document["migration_id"]))
                blocked.append(path.name)
        # Clean orphan stages only when they prove no publication started
        for stage in self._storage.root.glob(".*.stage") if self._storage.root.exists() else ():
            migration_id = stage.name[1:-6]
            if migration_id in journal_ids:
                continue
            try:
                if MIGRATION_ID.fullmatch(migration_id) is None:
                    raise MigrationStorageError("Migration staging identity is invalid.")
                if not orphan_stage_is_proven_prepublication(
                    self._manager_root, stage, migration_id,
                ):
                    raise MigrationStorageError("Migration staging is not proven prepublication.")
                self._storage.cleanup(stage)
                recovered.append(migration_id)
            except Exception:
                blocked.append(stage.name)
        # Block further mutations while any recovery is unresolved
        if blocked:
            self._block("Mutations are blocked by unresolved migration recovery.")
        return {
            "state": "RECOVERY_REQUIRED" if blocked else "READY",
            "blocked": blocked,
            "recovered": recovered,
        }

    def _compensate(self, document: dict[str, object], stage: Path) -> None:
        """Restore prior payloads and mark the journal rolled back."""
        self._validate_plan(document)
        validate_payload_evidence(self._manager_root, document, stage)
        # Re-classify live targets before choosing the rollback path
        destinations = [dict(item) for item in document["destinations"]]  # type: ignore[arg-type]
        states = classify_destinations(self._manager_root, destinations)
        if "THIRD_STATE" in states:
            raise MigrationStorageError("Migration targets contain an ambiguous third state.")
        # Record compensation before restoring so a crash keeps the intent
        if document["publication_started"]:
            document = self._journals.update(
                document, state="COMPENSATING", committed=False,
                resolved=False, result=None,
            )
        # Restore destinations in reverse publication order
        for destination, state in reversed(tuple(zip(destinations, states))):
            self._call(f"BEFORE_COMPENSATE_{destination['role']}")
            if state == "STAGED_OUTPUT":
                self._restore_prior(stage, destination)
            destination["state"] = "RESTORED"
        document = self._journals.update(
            document, destinations=destinations, state="ROLLED_BACK",
            committed=False, resolved=True, result="ROLLED_BACK",
        )
        # Verify restored bytes before retiring the journal
        self._verify_prior(document)
        self._retire(document, stage)

    def _replace_from_stage(self, stage: Path, destination: dict[str, object]) -> None:
        """Replace one target with its staged payload after a digest check."""
        source = stage / str(destination["staged_relative"])
        payload = read_bytes_shared(source)
        # Refuse to publish bytes that changed after staging
        if _digest(payload) != destination["staged_sha256"]:
            raise MigrationStorageError("Migration staged output changed.")
        self._atomic_bytes(self._target(destination), payload)

    def _restore_prior(self, stage: Path, destination: dict[str, object]) -> None:
        """Restore the original target bytes from recovery evidence."""
        target = self._target(destination)
        current = read_bytes_shared(target) if target.exists() else None
        if destination["prior_exists"]:
            # The original bytes may already sit in place untouched
            if current is not None and _digest(current) == destination["prior_sha256"]:
                return
            # Otherwise restore from the staged recovery copy after a digest check
            recovery = stage / str(destination["recovery_relative"])
            payload = read_bytes_shared(recovery)
            if _digest(payload) != destination["prior_sha256"]:
                raise MigrationStorageError("Migration recovery evidence changed.")
            self._atomic_bytes(target, payload)
        elif current is None:
            return
        # A new target is removed only when it matches the staged digest
        elif _digest(current) == destination["staged_sha256"]:
            target.unlink()
        # Anything else is ambiguous and must keep recovery unresolved
        else:
            raise MigrationStorageError("Migration target state is ambiguous.")

    def _verify_published(self, document: dict[str, object]) -> None:
        """Fail unless every published target matches its staged digest."""
        # Each target must exist and hold exactly the staged bytes
        for destination in document["destinations"]:  # type: ignore[union-attr]
            target = self._target(destination)
            if not target.is_file() or _digest(read_bytes_shared(target)) != destination["staged_sha256"]:
                raise MigrationStorageError("Published migration output could not be verified.")

    def _verify_prior(self, document: dict[str, object]) -> None:
        """Fail unless every target matches the recorded prior state."""
        # Existing priors must match their digest and new targets must be gone
        for destination in document["destinations"]:  # type: ignore[union-attr]
            target = self._target(destination)
            if destination["prior_exists"]:
                if not target.is_file() or _digest(read_bytes_shared(target)) != destination["prior_sha256"]:
                    raise MigrationStorageError("Migration rollback could not be verified.")
            elif target.exists():
                raise MigrationStorageError("Migration rollback left a new target.")

    def _finish_retired(self, document: dict[str, object], stage: Path) -> None:
        """Finish a retired journal whose state is terminal."""
        # Terminal journals must still verify their targets before cleanup
        if document["state"] == "COMMITTED":
            self._verify_published(document)
        elif document["state"] == "ROLLED_BACK":
            self._verify_prior(document)
        else:
            raise MigrationStorageError("Retired migration journal is unresolved.")
        self._storage.cleanup(stage) if stage.exists() else None
        self._journals.finish_retired(str(document["migration_id"]))

    def _retire(self, document: dict[str, object], stage: Path) -> None:
        """Retire the journal and best-effort clean its stage."""
        self._journals.retire(document)
        self._call("JOURNAL_RETIRED")
        # Cleanup failures must not undo a retired publication
        try:
            self._storage.cleanup(stage)
            self._journals.finish_retired(str(document["migration_id"]))
        except MigrationStorageError:
            pass

    def _mark_recovery_required(self, migration_id: str) -> None:
        """Flag the journal as requiring recovery and block mutations."""
        # Best-effort update; the block below always runs
        try:
            document = self._journals.read(self._journals.path(migration_id))
            if document["state"] != "RECOVERY_REQUIRED":
                self._journals.update(
                    document, state="RECOVERY_REQUIRED", publication_started=True,
                    committed=False, resolved=False, result="RECOVERY_REQUIRED",
                )
        except (MigrationJournalError, OSError):
            pass
        self._block("Mutations are blocked by unresolved migration recovery.")

    def _target(self, destination: dict[str, object]) -> Path:
        """Resolve a destination target and refuse root escapes."""
        target = self._manager_root / Path(str(destination["target_relative"]))
        resolved = target.resolve(strict=False)
        # The resolved target must stay inside the manager root
        try:
            resolved.relative_to(self._manager_root)
        except ValueError as error:
            raise MigrationStorageError("Migration target escaped the manager root.") from error
        return resolved

    def _stage(self, migration_id: str) -> Path:
        """Return the hidden staging directory for a migration identifier."""
        return self._storage.root / f".{migration_id}.stage"

    @staticmethod
    def _validate_plan(document: dict[str, object]) -> None:
        """Re-validate the persisted plan before recovery acts on it."""
        # Surface plan violations as storage errors
        try:
            validate_destination_plan(
                str(document["migration_id"]), document["destinations"],  # type: ignore[arg-type]
            )
        except MigrationDestinationPlanError as error:
            raise MigrationStorageError("Migration destination plan is invalid.") from error

    @staticmethod
    def _atomic_bytes(path: Path, payload: bytes) -> None:
        """Write bytes through a temporary file and a final atomic swap."""
        # The shared helper creates the folder, fsyncs before the swap and removes its staging file
        write_bytes_atomically(path, payload, ".migration.tmp")

    def _call(self, phase: str) -> None:
        """Forward a publication phase to the optional observer."""
        if self._hook is not None:
            self._hook(phase)

    def _block(self, message: str) -> None:
        """Forward a blocking reason to the optional observer."""
        if self._block_recovery is not None:
            self._block_recovery(message)


def _digest(payload: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of the payload."""
    return hashlib.sha256(payload).hexdigest()
