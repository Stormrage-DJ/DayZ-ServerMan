"""Strict bridge handlers for external legacy backup references."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.models import RecordUnavailable, RevisionConflict
from ..repositories.legacy_backup_index import LegacyBackupIndexError
from .legacy_backups import LegacyBackupService
from .operations.manager import OperationManager
from .operations.models import OperationFailure, QueueUnavailable


class LegacyBackupCoordinator:
    """Serve legacy backup listing and revalidation through the bridge."""

    def __init__(self, service: LegacyBackupService, operations: OperationManager) -> None:
        """Bind the legacy backup service and the operation manager."""
        self._service = service
        self._operations = operations

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the bridge handler table for legacy backup use cases."""
        return {
            "list_legacy_backup_references": self.list,
            "revalidate_legacy_backup_references": self.revalidate,
        }

    def list(self, parameters: Mapping[str, Any]) -> dict[str, object]:
        """Return the recorded legacy backup references."""
        # This query takes no parameters
        if parameters:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "Legacy backup query is invalid.")
        # Report index failures as a recovery-required error
        try:
            return self._service.list()
        except (RecordUnavailable, LegacyBackupIndexError) as error:
            raise ApplicationCallError(
                ErrorCode.RECOVERY_REQUIRED, "Legacy backup index requires recovery.",
            ) from error

    def revalidate(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue a revalidation of the stored legacy backup references."""
        # Require exactly the expected index revision
        if set(parameters) != {"expected_revision"}:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "Revalidation request is invalid.")
        expected = parameters["expected_revision"]

        def work(_context: object) -> Mapping[str, Any]:
            """Revalidate the entries inside the operation lane."""
            try:
                return self._service.revalidate(expected)
            except ValueError as error:
                raise OperationFailure("INVALID_REQUEST", str(error)) from error
            except RevisionConflict as error:
                raise OperationFailure("REVISION_CONFLICT", str(error)) from error
            except (OSError, RecordUnavailable, LegacyBackupIndexError) as error:
                raise OperationFailure("STORAGE_FAILURE", "Legacy backup revalidation failed.") from error

        # Submit the revalidation as a mutation operation
        try:
            record = self._operations.submit("REVALIDATE_LEGACY_BACKUPS", work)
        except QueueUnavailable as error:
            raise ApplicationCallError(ErrorCode.MUTATION_CONFLICT, str(error), retryable=True) from error
        return {"operation_id": record.operation_id, "state": record.state.value}
