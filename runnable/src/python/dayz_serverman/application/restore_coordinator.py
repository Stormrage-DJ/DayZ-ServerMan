"""Strict bridge contracts for restore preview, apply, and recovery inspection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.backups import SHA256
from ..domain.lifecycle import LifecycleFailure
from ..domain.models import RecordUnavailable, RevisionConflict
from ..domain.profiles import ProfileValidationError
from ..repositories.backups import BackupStorageError
from ..repositories.profiles import ProfileNotFound, ProfileStorageError
from ..repositories.restore_journal import RestoreJournalError
from ..repositories.restore_paths import RestorePathError
from ..repositories.restore_storage import RestoreStorageError
from .operations.manager import OperationManager
from .operations.models import OperationCancelled, OperationFailure, QueueUnavailable
from .restores import RestoreService
from .settings import SettingsValidationError


# Exact request fields accepted by the apply_restore handler
APPLY_FIELDS = {
    "profile_id", "backup_id", "expected_profile_revision", "expected_settings_revision",
    "expected_manifest_digest", "preview_fingerprint",
}


class RestoreCoordinator:
    """Bridge-facing coordinator for restore preview and apply calls."""

    def __init__(self, service: RestoreService, operations: OperationManager) -> None:
        """Store the restore service and operation manager."""
        self._service = service
        self._operations = operations

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the bridge handler table for restore calls."""
        return {
            "preview_restore": self.preview_restore,
            "apply_restore": self.apply_restore,
            "inspect_restore_recovery": self.inspect_restore_recovery,
        }

    def preview_restore(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return a signed restore preview for a verified backup."""
        # Reject unknown or missing request fields
        _exact(parameters, {"profile_id", "backup_id"})
        try:
            return self._service.preview(parameters["profile_id"], parameters["backup_id"])
        except Exception as error:
            raise _translate(error) from error

    def inspect_restore_recovery(self, parameters: Mapping[str, Any]) -> dict[str, object]:
        """Inspect restore recovery state and clear the block when safe."""
        _exact(parameters, set())
        result = self._service.inspect_recovery()
        # Clear the mutation block only when recovery is not required
        if not result["blocked"]:
            self._operations.clear_recovery_block()
        return result

    def apply_restore(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Submit a restore apply as a durable operation with recovery handling."""
        # Reject unknown fields and validate digests before scheduling any work
        _exact(parameters, APPLY_FIELDS)
        _digest(parameters["expected_manifest_digest"], "manifest digest")
        _digest(parameters["preview_fingerprint"], "preview fingerprint")

        def work(context: Any) -> dict[str, object]:
            """Apply the restore and translate failures into operation errors."""
            try:
                return self._service.apply(
                    parameters["profile_id"], parameters["backup_id"],
                    parameters["expected_profile_revision"],
                    parameters["expected_settings_revision"],
                    parameters["expected_manifest_digest"], parameters["preview_fingerprint"],
                    context.operation_id, context.checkpoint,
                )
            except OperationCancelled:
                # Cooperative cancellation must reach the operation lane unchanged
                raise
            except Exception as error:
                call = _translate(error)
                recovery = call.code == ErrorCode.RECOVERY_REQUIRED
                # Block further mutations until recovery has been inspected
                if recovery:
                    self._operations.block_for_recovery(
                        "Mutations are blocked until restore recovery is inspected.",
                    )
                raise OperationFailure(
                    call.code.value, call.safe_message,
                    retryable=call.retryable, recovery_required=recovery,
                ) from error

        try:
            operation = self._operations.submit(
                "RESTORE_BACKUP",
                work,
                safe_points=frozenset(
                    ("VERIFY_SOURCE", "STAGE_TARGETS", "PREPARE_RECOVERY", "WRITE_JOURNAL")
                ),
                log_fields={
                    "profile_id": str(parameters.get("profile_id")),
                    "backup_id": str(parameters.get("backup_id")),
                    "target_role": "restore",
                },
                target_profile_id=str(parameters.get("profile_id")),
            )
        except QueueUnavailable as error:
            raise ApplicationCallError(
                ErrorCode.MUTATION_CONFLICT, str(error), retryable=True,
                details=error.details,
            ) from error
        return {"operation_id": operation.operation_id, "state": operation.state.value}


def _exact(parameters: Mapping[str, Any], expected: set[str]) -> None:
    """Reject requests whose fields do not exactly match the contract."""
    if set(parameters) != expected:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "restore parameters are invalid")


def _digest(value: object, label: str) -> str:
    """Validate a SHA-256 digest field and return it unchanged."""
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, f"Restore {label} is invalid.")
    return value


def _translate(error: Exception) -> ApplicationCallError:
    """Map a domain failure to the closest bridge error with a safe message."""
    # Prefer the lifecycle's own declared code when the bridge knows it
    if isinstance(error, LifecycleFailure):
        code = ErrorCode.__members__.get(error.code, ErrorCode.INTERNAL_FAILURE)
        return ApplicationCallError(code, error.safe_message, retryable=error.retryable)
    if isinstance(error, RevisionConflict):
        return ApplicationCallError(ErrorCode.REVISION_CONFLICT, str(error), retryable=True)
    if isinstance(error, (ProfileValidationError, ValueError)):
        return ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error))
    if isinstance(error, ProfileNotFound):
        return ApplicationCallError(ErrorCode.NOT_FOUND, "Profile was not found.")
    if isinstance(error, BackupStorageError):
        if error.code == "PENDING_RUNTIME_PROFILE_SUPPORT":
            return ApplicationCallError(ErrorCode.PENDING_RUNTIME_PROFILE_SUPPORT, str(error))
        if error.code == "PROFILE_CONTEXT_MISMATCH":
            return ApplicationCallError(ErrorCode.PROFILE_CONTEXT_MISMATCH, str(error))
        if error.code == "UNSUPPORTED_SNAPSHOT_CONTENT":
            return ApplicationCallError(ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT, str(error))
        if error.code == "LEGACY_PROFILE_SCHEMA":
            return ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error))
        if error.code == "PROFILE_MISMATCH":
            return ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error))
        if error.code in {"INVALID_REQUEST"}:
            return ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error))
        if error.code in {"BACKUP_SOURCE_INVALID"}:
            return ApplicationCallError(ErrorCode.NOT_FOUND, str(error))
        return ApplicationCallError(ErrorCode.STORAGE_FAILURE, str(error), retryable=True)
    if isinstance(error, RestoreStorageError):
        if error.recovery_required:
            return ApplicationCallError(ErrorCode.RECOVERY_REQUIRED, str(error))
        if error.code == "INVALID_REQUEST":
            return ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error))
        if error.code == "PATH_INVALID":
            return ApplicationCallError(ErrorCode.PATH_INVALID, str(error))
        return ApplicationCallError(ErrorCode.STORAGE_FAILURE, str(error), retryable=True)
    if isinstance(error, RestorePathError):
        code = ErrorCode.PATH_INVALID if error.code.startswith("PATH_") else ErrorCode.STORAGE_FAILURE
        return ApplicationCallError(code, str(error), retryable=code == ErrorCode.STORAGE_FAILURE)
    if isinstance(error, (RecordUnavailable, ProfileStorageError, SettingsValidationError, RestoreJournalError)):
        return ApplicationCallError(ErrorCode.RECOVERY_REQUIRED, "Restore context requires recovery.")
    if isinstance(error, OSError):
        return ApplicationCallError(ErrorCode.STORAGE_FAILURE, "Restore storage failed.", retryable=True)
    return ApplicationCallError(ErrorCode.INTERNAL_FAILURE, "Restore processing failed.")
