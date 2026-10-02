"""Named bridge contracts for manual backups and verified history."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.backups import BackupManifestError
from ..domain.models import RecordUnavailable, RevisionConflict
from ..domain.profiles import ProfileValidationError
from ..repositories.backups import BackupStorageError
from ..repositories.backup_verification import BackupVerificationError
from ..repositories.profiles import ProfileNotFound, ProfileStorageError
from .backups import BackupService
from .operations.manager import OperationManager
from .operations.models import OperationCancelled, OperationFailure, QueueUnavailable
from .settings import SettingsValidationError


class BackupCoordinator:
    """Translate backup bridge calls into service work with operation tracking."""

    def __init__(self, service: BackupService, operations: OperationManager) -> None:
        """Bind the backup service and the shared operation manager."""
        self._service = service
        self._operations = operations

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the bridge handler table for backup use cases."""
        return {
            "list_backups": self.list_backups,
            "list_backup_catalog": self.list_backup_catalog,
            "create_backup": self.create_backup,
        }

    def list_backup_catalog(self, parameters):
        """Query archives even when every profile has been deleted."""
        _exact(parameters, set())
        try:
            return self._service.catalog()
        except Exception as error:
            raise _query_error(error) from error

    def list_backups(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return the verified backup history for one profile."""
        # Require exactly the profile identifier
        _exact(parameters, {"profile_id"})
        # Surface verification and storage failures as bridge errors
        try:
            return self._service.history(parameters["profile_id"])
        except Exception as error:
            raise _query_error(error) from error

    def create_backup(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue a verified backup creation and return its operation handle."""
        # Require exactly the backup request fields
        _exact(
            parameters,
            {"profile_id", "expected_profile_revision", "expected_settings_revision"},
        )
        # Reject invalid expected revisions before queueing any work
        for field in ("expected_profile_revision", "expected_settings_revision"):
            value = parameters[field]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ApplicationCallError(ErrorCode.INVALID_REQUEST, f"{field} is invalid")

        def work(context: Any) -> dict[str, Any]:
            """Create the backup inside the operation lane with cancellation support."""
            # Keep cancellation propagating and map other failures to operation errors
            try:
                return self._service.create(
                    parameters["profile_id"],
                    parameters["expected_profile_revision"],
                    parameters["expected_settings_revision"],
                    context.checkpoint,
                )
            except OperationCancelled:
                raise
            except Exception as error:
                call = _query_error(error)
                raise OperationFailure(
                    call.code.value,
                    call.safe_message,
                    retryable=call.retryable,
                    recovery_required=call.code == ErrorCode.RECOVERY_REQUIRED,
                ) from error

        # Submit the backup to the mutation lane with its replay-safe checkpoint phases
        try:
            record = self._operations.submit(
                "CREATE_BACKUP",
                work,
                safe_points=frozenset(
                    ("DISCOVER", "STAGE", "HASH", "WRITE_MANIFEST", "VERIFY")
                ),
                log_fields={"profile_id": str(parameters["profile_id"]), "target_role": "backup"},
            )
        except QueueUnavailable as error:
            raise ApplicationCallError(ErrorCode.MUTATION_CONFLICT, str(error), retryable=True) from error
        return {"operation_id": record.operation_id, "state": record.state.value}


def _exact(parameters: Mapping[str, Any], expected: set[str]) -> None:
    """Reject parameter sets that do not match the expected fields exactly."""
    if set(parameters) != expected:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "backup parameters are invalid")


def _query_error(error: Exception) -> ApplicationCallError:
    """Map backup service failures to stable bridge error codes."""
    # Malformed or unverified archives are retryable storage failures
    if isinstance(error, (BackupManifestError, BackupVerificationError, json.JSONDecodeError)):
        return ApplicationCallError(
            ErrorCode.STORAGE_FAILURE,
            "Backup integrity verification failed.",
            retryable=True,
        )
    if isinstance(error, (ProfileValidationError, ValueError)):
        return ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error))
    if isinstance(error, ProfileNotFound):
        return ApplicationCallError(ErrorCode.NOT_FOUND, "Profile was not found.")
    if isinstance(error, RevisionConflict):
        return ApplicationCallError(ErrorCode.REVISION_CONFLICT, str(error), retryable=True)
    if isinstance(error, BackupStorageError):
        # Map the fine-grained storage codes onto the bridge vocabulary
        if error.code == "RUNTIME_PROFILE_UNRESOLVED":
            return ApplicationCallError(ErrorCode.RUNTIME_PROFILE_UNRESOLVED, str(error))
        if error.code == "RECOVERY_REQUIRED":
            return ApplicationCallError(ErrorCode.RECOVERY_REQUIRED, str(error))
        if error.code in {"PATH_INVALID", "PATH_OUTSIDE_ALLOWED_ROOT", "UNSUPPORTED_NETWORK"}:
            return ApplicationCallError(ErrorCode.PATH_INVALID, str(error))
        if error.code in {"BACKUP_SOURCE_INVALID", "BACKUP_CONTEXT_INVALID"}:
            return ApplicationCallError(ErrorCode.NOT_FOUND, str(error))
        if error.code == "INVALID_REQUEST":
            return ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error))
        if error.code == "BACKUP_INTEGRITY_FAILED":
            return ApplicationCallError(ErrorCode.STORAGE_FAILURE, str(error), retryable=True)
        return ApplicationCallError(ErrorCode.STORAGE_FAILURE, str(error), retryable=True)
    if isinstance(error, (RecordUnavailable, ProfileStorageError, SettingsValidationError)):
        return ApplicationCallError(ErrorCode.RECOVERY_REQUIRED, "Backup context is unavailable.")
    if isinstance(error, OSError):
        return ApplicationCallError(ErrorCode.STORAGE_FAILURE, "Backup storage failed.", retryable=True)
    # Everything unrecognized fails closed as an internal error
    return ApplicationCallError(ErrorCode.INTERNAL_FAILURE, "Backup processing failed.")
