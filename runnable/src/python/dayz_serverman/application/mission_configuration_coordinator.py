"""Named bridge contracts for mission configuration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.mission_configuration import MissionValidationError
from ..domain.models import RecordUnavailable, RevisionConflict
from ..repositories.atomic_file import ContentChangedError
from ..repositories.configuration_common import ConfigurationFileError
from ..repositories.profiles import ProfileNotFound, ProfileStorageError
from .mission_configuration import MissionConfigurationService, MissionPathError
from .operations.manager import OperationManager
from .operations.models import OperationFailure, QueueUnavailable
from .settings import SettingsValidationError

# Bridge fields accepted by preview and apply calls
FIELDS = {"profile_id", "target", "expected_profile_revision", "expected_settings_revision", "expected_digest", "updates"}
# Bridge fields accepted by the starter-loadout conversion call
CONVERSION_FIELDS = {"profile_id", "expected_profile_revision", "expected_settings_revision", "expected_digest"}


class MissionConfigurationCoordinator:
    """Serve mission configuration calls over the shared operation lane."""

    def __init__(self, service: MissionConfigurationService, operations: OperationManager) -> None:
        """Store the mission service and the shared operation manager."""
        self._service = service
        self._operations = operations

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the bridge handler table for mission configuration calls."""
        return {"load_mission_configuration": self.load,
                "preview_mission_configuration": self.preview,
                "apply_mission_configuration": self.apply,
                "convert_starter_loadout": self.convert_starter}

    def load(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return one mission file with its persisted values."""
        _exact(parameters, {"profile_id", "target"})
        # Translate resolution failures into bridge error codes
        try:
            return self._service.load(parameters["profile_id"], _target(parameters["target"]))
        except Exception as error:
            raise _error(error) from error

    def preview(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return the proposed change preview for one mission target."""
        _exact(parameters, FIELDS)
        # Translate validation failures into bridge error codes
        try:
            return self._service.preview(**_args(parameters))
        except Exception as error:
            raise _error(error) from error

    def apply(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue a validated mission update on the exclusive lane."""
        _exact(parameters, FIELDS)
        arguments = _args(parameters)
        def work(context: Any) -> dict[str, Any]:
            """Apply the reviewed change and map failures for the queue."""
            # Convert every failure into a typed operation failure
            try:
                return self._service.apply(**arguments, checkpoint=context.checkpoint)
            except Exception as error:
                call = _error(error)
                raise OperationFailure(call.code.value, call.safe_message,
                                       recovery_required=call.code == ErrorCode.RECOVERY_REQUIRED) from error
        # Queue the change and surface a busy lane immediately
        try:
            record = self._operations.submit("APPLY_MISSION_CONFIGURATION", work,
                safe_points=frozenset(("loaded", "validated")),
                log_fields={"profile_id": str(parameters["profile_id"]), "target_role": str(parameters["target"])},
                target_profile_id=str(parameters["profile_id"]))
        except QueueUnavailable as error:
            raise ApplicationCallError(
                ErrorCode.MUTATION_CONFLICT, str(error), retryable=True,
                details=error.details,
            ) from error
        return {"operation_id": record.operation_id, "state": record.state.value}

    def convert_starter(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue a legacy starter-loadout conversion."""
        _exact(parameters, CONVERSION_FIELDS)
        # Bind only the four conversion proofs for the queued work
        arguments = {name: parameters[name] for name in CONVERSION_FIELDS}

        def work(context: Any) -> dict[str, Any]:
            """Convert the starter loadout and map failures for the queue."""
            # Convert every failure into a typed operation failure
            try:
                return self._service.convert_starter(**arguments, checkpoint=context.checkpoint)
            except Exception as error:
                call = _error(error)
                raise OperationFailure(call.code.value, call.safe_message,
                                       recovery_required=call.code == ErrorCode.RECOVERY_REQUIRED) from error
        # Queue the conversion and surface a busy lane immediately
        try:
            record = self._operations.submit("CONVERT_STARTER_LOADOUT", work,
                safe_points=frozenset(("loaded", "validated")),
                log_fields={"profile_id": str(parameters["profile_id"])},
                target_profile_id=str(parameters["profile_id"]))
        except QueueUnavailable as error:
            raise ApplicationCallError(
                ErrorCode.MUTATION_CONFLICT, str(error), retryable=True,
                details=error.details,
            ) from error
        return {"operation_id": record.operation_id, "state": record.state.value}


def _exact(parameters: Mapping[str, Any], expected: set[str]) -> None:
    """Reject calls whose parameter set differs from the expected fields."""
    if set(parameters) != expected:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "mission configuration parameters are invalid")


def _target(value: object) -> str:
    """Validate a mission target role before it reaches the service."""
    # Reject targets that the mission workflow does not support
    if value not in ("economy", "weather", "spawnable_damage", "starter_loadout", "events"):
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "mission target is not supported")
    return str(value)


def _args(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Unpack a preview or apply payload into service keyword arguments."""
    return {"profile_id": parameters["profile_id"], "target": _target(parameters["target"]),
            "expected_profile_revision": parameters["expected_profile_revision"],
            "expected_settings_revision": parameters["expected_settings_revision"],
            "expected_digest": parameters["expected_digest"], "raw_updates": parameters["updates"]}


def _error(error: Exception) -> ApplicationCallError:
    """Map a service exception onto the matching bridge error call."""
    # Route each failure family to its stable bridge error code
    if isinstance(error, MissionValidationError):
        return ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error))
    if isinstance(error, (ProfileNotFound, MissionPathError)):
        return ApplicationCallError(ErrorCode.NOT_FOUND, str(error))
    if isinstance(error, (RevisionConflict, ContentChangedError)):
        return ApplicationCallError(ErrorCode.REVISION_CONFLICT, str(error), retryable=True)
    if isinstance(error, ConfigurationFileError):
        return ApplicationCallError(ErrorCode.RECOVERY_REQUIRED, str(error))
    if isinstance(error, (RecordUnavailable, ProfileStorageError, SettingsValidationError)):
        return ApplicationCallError(ErrorCode.RECOVERY_REQUIRED, "Mission configuration context is unavailable.")
    return ApplicationCallError(ErrorCode.STORAGE_FAILURE, "Mission configuration could not be processed.")
