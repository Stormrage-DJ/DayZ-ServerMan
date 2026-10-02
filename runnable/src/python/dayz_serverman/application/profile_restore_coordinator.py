"""Strict named bridge methods for independent profile reconstruction."""

from collections.abc import Mapping, Callable
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from .operations.models import OperationFailure, OperationCancelled, QueueUnavailable
from .profile_restores import PREVIEW_FIELDS, ProfileRestoreService
from .operations.manager import OperationManager
from .restore_coordinator import _translate


class ProfileRestoreCoordinator:
    """Submit direct restore mutations through the existing operation lane."""

    def __init__(self, service: ProfileRestoreService, operations: OperationManager) -> None:
        """Bind the service and the shared mutation manager."""
        self.service, self.operations = service, operations

    def handlers(self) -> dict[str, Callable]:
        """Expose exact-field archive selection and reconstruction methods."""
        return {"preview_profile_restore": self.preview, "restore_profile_from_backup": self.apply, "inspect_backup_archive": self.inspect_archive}

    def inspect_archive(self, parameters):
        """Inspect a single selected ZIP without creating a profile or importing files."""
        if set(parameters) != {"path"}:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "Archive selection fields are invalid.")
        try:
            return self.service.selected.inspect(parameters["path"])
        except RuntimeError as error:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "The selected archive is corrupt, unsafe or unsupported. Select a valid full backup ZIP.") from error
        except Exception as error:
            raise _translate(error) from error

    def preview(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Translate a read-only preview failure into a safe bridge diagnostic."""
        try:
            return self.service.preview(parameters)
        except Exception as error:
            raise _translate(error) from error

    def apply(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Queue a fingerprint-bound publication without permitting late cancellation."""
        expected = PREVIEW_FIELDS | {"expected_manifest_digest", "preview_fingerprint", "overwrite_confirmation"}
        if set(parameters) != expected:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "Direct restore fields are invalid.")
        try:
            self.service.validate_request({key: parameters[key] for key in PREVIEW_FIELDS})
        except Exception as error:
            raise _translate(error) from error
        captured = dict(parameters)
        def work(context):
            """Run inside the mutation lane and block it if compensation cannot finish."""
            try:
                return self.service.apply(captured, context.operation_id, context.checkpoint)
            except OperationCancelled:
                raise
            except Exception as error:
                recovery = any(record["phase"] == "RECOVERY_REQUIRED" for record in self.service.storage.journals.records())
                if recovery:
                    context.block_for_recovery("Direct profile restore requires recovery.")
                    raise OperationFailure("RECOVERY_REQUIRED", "Direct profile restore requires recovery.", recovery_required=True) from error
                translated = _translate(error)
                raise OperationFailure(translated.code.value, translated.safe_message, retryable=translated.retryable) from error
        try:
            operation = self.operations.submit("RESTORE_PROFILE_FROM_BACKUP", work,
                safe_points=frozenset({"VERIFYING_BACKUP", "PREPARING", "PREPARED"}),
                log_fields={"backup_id": str(captured["backup_id"]), "target_role": "profile_restore"})
        except QueueUnavailable as error:
            raise ApplicationCallError(ErrorCode.MUTATION_CONFLICT, str(error), retryable=True) from error
        return {"operation_id": operation.operation_id, "state": operation.state.value}
