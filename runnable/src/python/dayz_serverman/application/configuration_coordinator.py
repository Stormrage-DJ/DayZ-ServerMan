"""Bridge queries and mutation-lane commands for shared configuration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.configuration import ConfigurationValidationError
from ..domain.models import RecordUnavailable, RevisionConflict
from ..domain.profiles import ProfileValidationError
from ..repositories.atomic_file import ContentChangedError
from ..repositories.configuration_files import ConfigurationFileError
from ..repositories.profiles import ProfileNotFound, ProfileStorageError
from .configuration import (
    ConfigurationPathError,
    ConfigurationService,
    GameplayPrerequisiteError,
)
from .operations.manager import OperationManager
from .operations.models import OperationFailure, QueueUnavailable
from .settings import SettingsValidationError


class ConfigurationCoordinator:
    """Serve configuration queries and queue configuration mutations."""

    def __init__(self, service: ConfigurationService, operations: OperationManager) -> None:
        """Bind the configuration service and the operation manager."""
        self._service = service
        self._operations = operations

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the bridge handler table for configuration use cases."""
        return {
            "load_configuration": self.load,
            "preview_configuration": self.preview,
            "apply_configuration": self.apply,
        }

    def load(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return the current configuration view through the bridge."""
        # Require exactly the profile and target selectors
        _exact(parameters, {"profile_id", "target"})
        target = _target(parameters["target"])
        # Translate service failures into bridge errors
        try:
            return self._service.load(parameters["profile_id"], target)
        except Exception as error:
            raise _query_error(error) from error

    def preview(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return the projected change for validated updates."""
        _exact(parameters, _EDIT_FIELDS)
        arguments = _edit_arguments(parameters)
        # Surface validation and digest failures as bridge errors
        try:
            return self._service.preview(**arguments)
        except Exception as error:
            raise _query_error(error) from error

    def apply(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue a configuration apply and return its operation handle."""
        # Require exactly the edit request fields
        _exact(parameters, _EDIT_FIELDS)
        arguments = _edit_arguments(parameters)

        def work(context: Any) -> dict[str, Any]:
            """Apply the configuration inside the mutation lane."""
            try:
                return self._service.apply(**arguments, checkpoint=context.checkpoint)
            except Exception as error:
                raise _operation_error(error) from error

        # Queue the apply as a cancellable operation
        try:
            record = self._operations.submit(
                "APPLY_CONFIGURATION",
                work,
                safe_points=frozenset(("loaded", "validated")),
                log_fields={
                    "profile_id": str(parameters["profile_id"]),
                    "target_role": f"{parameters['target']}_configuration",
                },
                target_profile_id=str(parameters["profile_id"]),
            )
        except QueueUnavailable as error:
            raise ApplicationCallError(
                ErrorCode.MUTATION_CONFLICT, str(error), retryable=True,
                details=error.details,
            ) from error
        return {"operation_id": record.operation_id, "state": record.state.value}


# Fields accepted by both the preview and apply handlers
_EDIT_FIELDS = {
    "profile_id",
    "target",
    "expected_profile_revision",
    "expected_settings_revision",
    "expected_digest",
    "expected_server_digest",
    "updates",
}


def _exact(parameters: Mapping[str, Any], allowed: set[str]) -> None:
    """Reject parameter sets that do not match the allowed fields exactly."""
    if set(parameters) != allowed:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "configuration parameters are invalid")


def _target(value: object) -> str:
    """Return the validated target name for a configuration request."""
    # Only the server and gameplay files can be edited
    if value not in ("server", "gameplay"):
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "target must be server or gameplay")
    return str(value)


def _edit_arguments(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Map bridge parameters onto configuration service keyword arguments."""
    return {
        "profile_id": parameters["profile_id"],
        "target": _target(parameters["target"]),
        "expected_profile_revision": parameters["expected_profile_revision"],
        "expected_settings_revision": parameters["expected_settings_revision"],
        "expected_digest": parameters["expected_digest"],
        "expected_server_digest": parameters["expected_server_digest"],
        "raw_updates": parameters["updates"],
    }


def _query_error(error: Exception) -> ApplicationCallError:
    """Map configuration failures to stable bridge error codes."""
    if isinstance(error, (ConfigurationValidationError, ProfileValidationError)):
        return ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error))
    if isinstance(error, (ProfileNotFound, ConfigurationPathError)):
        return ApplicationCallError(ErrorCode.NOT_FOUND, str(error))
    if isinstance(error, (RevisionConflict, ContentChangedError)):
        return ApplicationCallError(ErrorCode.REVISION_CONFLICT, str(error), retryable=True)
    if isinstance(error, GameplayPrerequisiteError):
        return ApplicationCallError(ErrorCode.GAMEPLAY_NOT_ENABLED, str(error))
    if isinstance(error, ConfigurationFileError):
        return ApplicationCallError(ErrorCode.RECOVERY_REQUIRED, str(error))
    if isinstance(error, (RecordUnavailable, ProfileStorageError, SettingsValidationError)):
        return ApplicationCallError(ErrorCode.RECOVERY_REQUIRED, "Configuration context is unavailable.")
    # Everything unrecognized fails closed as a storage failure
    return ApplicationCallError(ErrorCode.STORAGE_FAILURE, "Configuration could not be read.")


def _operation_error(error: Exception) -> OperationFailure:
    """Convert a bridge error into an operation failure for the mutation lane."""
    call_error = _query_error(error)
    return OperationFailure(
        call_error.code.value,
        call_error.safe_message,
        recovery_required=call_error.code == ErrorCode.RECOVERY_REQUIRED,
    )
