"""Allowlisted bridge use cases for settings and operation control."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..bridge.contracts import ErrorCode, IDENTIFIER_PATTERN
from ..bridge.facade import ApplicationCallError
from ..domain.models import RevisionConflict, SettingsInput
from .operations.manager import OperationManager
from .operations.models import (
    EventCursorExpired,
    OperationFailure,
    OperationNotCancellable,
    OperationNotFound,
    QueueUnavailable,
)
from .settings import (
    LOCATION_ROOT_FIELDS,
    PATH_SELECTION_ROLES,
    SETTINGS_FIELDS,
    SettingsService,
    SettingsValidationError,
    expand_location_roots,
    resolved_paths_for_selection,
)
from .shutdown import ShutdownCoordinator


def _exact_fields(parameters: Mapping[str, Any], allowed: set[str]) -> None:
    """Reject parameter sets that contain unknown fields."""
    unknown = set(parameters).difference(allowed)
    if unknown:
        raise ApplicationCallError(
            ErrorCode.INVALID_REQUEST,
            f"parameters contain unknown fields: {sorted(unknown)}",
        )


class ApplicationCoordinator:
    """Serve settings and operation-control bridge use cases."""

    def __init__(
        self,
        settings: SettingsService,
        operations: OperationManager,
        shutdown: ShutdownCoordinator,
    ) -> None:
        """Bind settings, operations, and shutdown collaborators."""
        self._settings = settings
        self._operations = operations
        self._shutdown = shutdown

    def handlers(self) -> dict[str, Any]:
        """Return the bridge handler table for application use cases."""
        return {
            "get_application_snapshot": self.get_application_snapshot,
            "get_operation": self.get_operation,
            "read_operation_events": self.read_operation_events,
            "request_operation_cancellation": self.request_operation_cancellation,
            "request_shutdown": self.request_shutdown,
            "save_settings": self.save_settings,
            "validate_settings_path_selection": self.validate_settings_path_selection,
        }

    def get_application_snapshot(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return settings, diagnostics, operations, and shutdown state."""
        # This snapshot takes no parameters
        _exact_fields(parameters, set())
        # Collect settings and path diagnostics for the snapshot
        settings = self._settings.load()
        diagnostics = self._settings.diagnostics(settings)
        return {
            "settings": self._settings_value(settings),
            "portable_backup_root": str(self._settings.portable_backup_root()),
            "diagnostics": [
                {
                    "role": item.role.value,
                    "status": item.status.value,
                    "configured_path": item.configured_path,
                    "message": item.message,
                    "action": item.action,
                }
                for item in diagnostics
            ],
            "operations": [self._operation_value(record) for record in self._operations.list_recent()],
            "operation_session_id": self._operations.session_id,
            "shutdown_state": self._shutdown.snapshot().state.value,
            "mutation_block": self._operations.recovery_block,
        }

    def get_operation(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return one operation record by identifier."""
        _exact_fields(parameters, {"operation_id"})
        # Require a well-formed operation identifier
        operation_id = parameters.get("operation_id")
        if not isinstance(operation_id, str) or IDENTIFIER_PATTERN.fullmatch(operation_id) is None:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "operation_id is required")
        # Report a missing operation as not found
        try:
            return self._operation_value(self._operations.get(operation_id))
        except OperationNotFound as error:
            raise ApplicationCallError(ErrorCode.NOT_FOUND, str(error)) from error

    def read_operation_events(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return operation events after a cursor with a bounded page size."""
        _exact_fields(parameters, {"after_sequence", "maximum"})
        # Default the cursor and page size, then require real integers
        after = parameters.get("after_sequence", 0)
        maximum = parameters.get("maximum", 100)
        if (
            not isinstance(after, int)
            or isinstance(after, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
        ):
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "event cursor is invalid")
        # Map expired cursors and invalid ranges onto bridge codes
        try:
            events, next_cursor = self._operations.read_events(after, maximum)
        except EventCursorExpired as error:
            raise ApplicationCallError(ErrorCode.EVENT_CURSOR_EXPIRED, str(error)) from error
        except ValueError as error:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error)) from error
        return {
            "session_id": self._operations.session_id,
            "events": [event.to_dict() for event in events],
            "next_cursor": next_cursor,
        }

    def request_operation_cancellation(
        self,
        parameters: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Request cancellation of a running operation."""
        _exact_fields(parameters, {"operation_id"})
        # Require a well-formed operation identifier
        operation_id = parameters.get("operation_id")
        if not isinstance(operation_id, str) or IDENTIFIER_PATTERN.fullmatch(operation_id) is None:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "operation_id is required")
        # Translate missing or non-cancellable operations into bridge errors
        try:
            return self._operation_value(self._operations.request_cancellation(operation_id))
        except OperationNotFound as error:
            raise ApplicationCallError(ErrorCode.NOT_FOUND, str(error)) from error
        except OperationNotCancellable as error:
            raise ApplicationCallError(
                ErrorCode.OPERATION_NOT_CANCELLABLE,
                str(error),
            ) from error

    def request_shutdown(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Request application shutdown through the shutdown coordinator."""
        _exact_fields(parameters, set())
        return self._shutdown.request_shutdown().to_dict()

    def save_settings(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Queue a settings save with a revision guard and path expansion."""
        # Accept only the location roots and the expected revision
        allowed = {*LOCATION_ROOT_FIELDS, "expected_revision"}
        _exact_fields(parameters, allowed)
        # The revision guard must be a non-negative integer or null
        expected = parameters.get("expected_revision")
        if expected is not None and (
            not isinstance(expected, int) or isinstance(expected, bool) or expected < 0
        ):
            raise ApplicationCallError(
                ErrorCode.INVALID_REQUEST,
                "expected_revision must be a non-negative integer or null",
            )
        # Normalize each location root and reject non-string values
        values: dict[str, str | None] = {}
        for field in LOCATION_ROOT_FIELDS:
            value = parameters.get(field)
            if value is not None and not isinstance(value, str):
                raise ApplicationCallError(
                    ErrorCode.INVALID_REQUEST,
                    f"{field} must be a string or null",
                )
            values[field] = value
        # Expand the selected roots into the concrete paths to persist
        expanded = expand_location_roots(values)

        def work(_context: object) -> dict[str, Any]:
            """Persist the settings inside the operation lane."""
            try:
                # Carry the stored steam fields forward unchanged
                current = self._settings.load()
                expanded.update(
                    steam_account_name=current.steam_account_name,
                    steam_authentication_mode=current.steam_authentication_mode,
                )
                saved = self._settings.save(SettingsInput(**expanded), expected)
            except RevisionConflict as error:
                raise OperationFailure("REVISION_CONFLICT", str(error)) from error
            except SettingsValidationError as error:
                raise OperationFailure("PATH_INVALID", str(error)) from error
            except OSError as error:
                raise OperationFailure(
                    "STORAGE_FAILURE",
                    "Settings could not be stored.",
                    retryable=True,
                ) from error
            return self._settings_value(saved)

        # Queue the save in the mutation lane
        try:
            operation = self._operations.submit("SAVE_SETTINGS", work)
        except QueueUnavailable as error:
            raise ApplicationCallError(
                ErrorCode.MUTATION_CONFLICT,
                str(error),
                retryable=True, details=error.details,
            ) from error
        return {"operation_id": operation.operation_id, "state": operation.state.value}

    def validate_settings_path_selection(
        self, parameters: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate one path selection and return its diagnostics and resolved paths."""
        _exact_fields(parameters, {"role", "path"})
        # Require a known role and a non-empty selected path
        role = parameters.get("role")
        path = parameters.get("path")
        if not isinstance(role, str) or role not in PATH_SELECTION_ROLES:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "settings path role is invalid")
        if not isinstance(path, str) or not path:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "selected path is invalid")
        # Surface invalid selections as path errors
        try:
            diagnostic = self._settings.validate_selection(role, path)
        except SettingsValidationError as error:
            raise ApplicationCallError(ErrorCode.PATH_INVALID, str(error)) from error
        return {
            "role": role,
            "path": diagnostic.configured_path,
            "status": diagnostic.status.value,
            "message": diagnostic.message,
            "action": diagnostic.action,
            "resolved_paths": resolved_paths_for_selection(role, diagnostic.configured_path),
        }

    @staticmethod
    def _settings_value(settings: Any) -> dict[str, Any]:
        """Shape a settings record including its revision for the bridge."""
        return {
            "revision": settings.revision,
            **{field: getattr(settings, field) for field in SETTINGS_FIELDS},
        }

    def _operation_value(self, operation: Any) -> dict[str, Any]:
        """Shape an operation record and mark whether it can still be cancelled."""
        value = operation.to_dict()
        value["cancellable"] = self._operations.is_cancellable(operation.operation_id)
        return value
