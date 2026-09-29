"""Named browser bridge contracts for reversible medical features."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.models import RecordUnavailable, RevisionConflict
from ..repositories.atomic_file import ContentChangedError
from ..repositories.configuration_common import ConfigurationFileError
from ..repositories.profiles import ProfileNotFound, ProfileStorageError
from .medical_features import FEATURE_PATHS, MedicalFeatureService
from .mission_configuration import MissionPathError
from .operations.manager import OperationManager
from .operations.models import OperationFailure, QueueUnavailable
from .settings import SettingsValidationError

# Fields accepted by both the preview and apply handlers
EDIT_FIELDS = {"profile_id", "feature", "enabled", "expected_profile_revision",
               "expected_settings_revision", "expected_digest"}


class MedicalFeatureCoordinator:
    """Serve medical feature queries and queue feature toggles."""

    def __init__(self, service: MedicalFeatureService, operations: OperationManager) -> None:
        """Bind the medical feature service and the operation manager."""
        self._service, self._operations = service, operations

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the bridge handler table for medical feature use cases."""
        return {"load_medical_features": self.load, "preview_medical_feature": self.preview,
                "apply_medical_feature": self.apply}

    def load(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return the current state of every reversible medical feature."""
        # Require exactly the profile selector
        _exact(parameters, {"profile_id"})
        # Translate service failures into bridge errors
        try:
            return self._service.load(parameters["profile_id"])
        except Exception as error:
            raise _error(error) from error

    def preview(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Validate a toggle and return the projected change without writing."""
        _exact(parameters, EDIT_FIELDS)
        # Surface validation and digest failures as bridge errors
        try:
            return self._service.preview(**_args(parameters))
        except Exception as error:
            raise _error(error) from error

    def apply(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue a feature toggle and return its operation handle."""
        # Validate the request and derive the service arguments
        _exact(parameters, EDIT_FIELDS); arguments = _args(parameters)
        def work(context: Any) -> dict[str, Any]:
            """Apply the toggle inside the mutation lane."""
            try:
                return self._service.apply(**arguments, checkpoint=context.checkpoint)
            except Exception as error:
                call = _error(error)
                raise OperationFailure(call.code.value, call.safe_message,
                                       recovery_required=call.code == ErrorCode.RECOVERY_REQUIRED) from error
        # Submit the toggle with its replay-safe checkpoint phases
        try:
            record = self._operations.submit("APPLY_MEDICAL_FEATURE", work,
                safe_points=frozenset(("loaded", "validated")),
                log_fields={"profile_id": str(parameters["profile_id"]),
                            "feature": str(parameters["feature"])})
        except QueueUnavailable as error:
            raise ApplicationCallError(ErrorCode.MUTATION_CONFLICT, str(error), retryable=True) from error
        return {"operation_id": record.operation_id, "state": record.state.value}


def _exact(parameters: Mapping[str, Any], expected: set[str]) -> None:
    """Reject parameter sets that do not match the expected fields exactly."""
    if set(parameters) != expected:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "medical feature parameters are invalid")


def _args(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Map bridge parameters onto service arguments, rejecting unknown features."""
    feature = parameters["feature"]
    # Only features with a known file mapping can be toggled
    if feature not in FEATURE_PATHS:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "medical feature is not supported")
    return {"profile_id": parameters["profile_id"], "feature": str(feature),
            "enabled": parameters["enabled"],
            "expected_profile_revision": parameters["expected_profile_revision"],
            "expected_settings_revision": parameters["expected_settings_revision"],
            "expected_digest": parameters["expected_digest"]}


def _error(error: Exception) -> ApplicationCallError:
    """Map medical feature failures to stable bridge error codes."""
    if isinstance(error, ValueError):
        return ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error))
    if isinstance(error, (ProfileNotFound, MissionPathError)):
        return ApplicationCallError(ErrorCode.NOT_FOUND, str(error))
    if isinstance(error, (RevisionConflict, ContentChangedError)):
        return ApplicationCallError(ErrorCode.REVISION_CONFLICT, str(error), retryable=True)
    if isinstance(error, ConfigurationFileError):
        return ApplicationCallError(ErrorCode.RECOVERY_REQUIRED, str(error))
    if isinstance(error, (RecordUnavailable, ProfileStorageError, SettingsValidationError)):
        return ApplicationCallError(ErrorCode.RECOVERY_REQUIRED, "Medical feature context is unavailable.")
    # Everything unrecognized fails closed as a storage failure
    return ApplicationCallError(ErrorCode.STORAGE_FAILURE, "Medical feature could not be processed.")
