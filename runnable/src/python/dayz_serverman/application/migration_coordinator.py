"""Strict bridge handlers for copy-only legacy import."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.models import RecordUnavailable, RevisionConflict
from ..repositories.legacy_source import LegacySourceError
from ..repositories.migrations import MigrationStorageError
from ..repositories.profiles import ProfileStorageError
from .migration_preview import MigrationConflictError, MigrationValidationError, SourceChangedError
from .migrations import MigrationService
from .operations.manager import OperationManager
from .operations.models import OperationFailure, QueueUnavailable
from .settings import SettingsValidationError


# Full lowercase SHA-256 hex digest required for migration fingerprints
SHA256 = re.compile(r"[0-9a-f]{64}")


def _exact(parameters: Mapping[str, Any], fields: set[str]) -> None:
    """Reject calls whose parameter set differs from the expected fields."""
    if set(parameters) != fields:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "Migration parameters are invalid.")


class MigrationCoordinator:
    """Expose legacy migration selection, preview, and apply as bridge handlers."""

    def __init__(self, service: MigrationService, operations: OperationManager) -> None:
        """Store the migration service and the shared operation manager."""
        self._service = service
        self._operations = operations

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the bridge handler table for legacy import calls."""
        return {
            "select_legacy_root": self.select_legacy_root,
            "preview_legacy_import": self.preview,
            "apply_legacy_import": self.apply,
        }

    def select_legacy_root(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Inspect the selected legacy root and return a selection identifier."""
        _exact(parameters, {"root"})
        # Translate an unusable legacy root into a path error
        try:
            return self._service.select_root(parameters["root"])
        except LegacySourceError as error:
            raise ApplicationCallError(ErrorCode.PATH_INVALID, str(error)) from error

    def preview(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Validate the preview request and return the migration findings."""
        _exact(parameters, {"selection_id"})
        # Translate inspection failures into bridge error codes
        try:
            return self._service.preview(parameters["selection_id"])
        except MigrationValidationError as error:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error)) from error
        except MigrationConflictError as error:
            raise ApplicationCallError(ErrorCode.MIGRATION_CONFLICT, str(error)) from error
        except (RecordUnavailable, ProfileStorageError, SettingsValidationError) as error:
            raise ApplicationCallError(ErrorCode.RECOVERY_REQUIRED, "Current data requires recovery.") from error

    def apply(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue the reviewed migration as an exclusive import operation."""
        _exact(parameters, {"preview_id", "preview_fingerprint", "selected_items"})
        preview_id = _identifier(parameters["preview_id"])
        fingerprint = parameters["preview_fingerprint"]
        # The fingerprint binds this apply request to the reviewed preview
        if not isinstance(fingerprint, str) or SHA256.fullmatch(fingerprint) is None:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "Migration fingerprint is invalid.")
        selected = parameters["selected_items"]
        # Reject selections that are not plain lists of identifiers
        if not isinstance(selected, list):
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "Migration selection is invalid.")

        def work(context: object) -> Mapping[str, Any]:
            """Apply the reviewed import and translate failures for the queue."""
            # Convert domain failures into typed operation failures
            try:
                return self._service.apply(preview_id, fingerprint, selected, context)  # type: ignore[arg-type]
            except MigrationValidationError as error:
                raise OperationFailure("INVALID_REQUEST", str(error)) from error
            except SourceChangedError as error:
                raise OperationFailure("SOURCE_CHANGED", str(error)) from error
            except (MigrationConflictError, RevisionConflict) as error:
                raise OperationFailure("MIGRATION_CONFLICT", str(error)) from error
            except LegacySourceError as error:
                raise OperationFailure("SOURCE_CHANGED", "Legacy source changed after preview.") from error
            except MigrationStorageError as error:
                raise OperationFailure(
                    "RECOVERY_REQUIRED" if error.recovery_required else "STORAGE_FAILURE",
                    str(error), recovery_required=error.recovery_required,
                ) from error
            except (ProfileStorageError, RecordUnavailable, OSError) as error:
                raise OperationFailure("STORAGE_FAILURE", "Legacy import storage failed.") from error

        # Queue the import on the exclusive mutation lane
        try:
            record = self._operations.submit(
                "IMPORT_LEGACY",
                work,
                safe_points=frozenset(("DISCOVER", "COPY_SOURCE", "CONVERT", "VERIFY")),
            )
        except QueueUnavailable as error:
            raise ApplicationCallError(ErrorCode.MUTATION_CONFLICT, str(error), retryable=True) from error
        return {"operation_id": record.operation_id, "state": record.state.value}


def _identifier(value: object) -> str:
    """Return a validated 32-character migration preview identifier."""
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "Migration preview identifier is invalid.")
    return value
