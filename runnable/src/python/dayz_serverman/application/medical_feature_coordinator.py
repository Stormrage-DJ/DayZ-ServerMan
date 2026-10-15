"""Named browser bridge contracts for reversible medical features."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.lifecycle import LifecycleFailure
from ..domain.models import RecordUnavailable, RevisionConflict
from ..repositories.atomic_file import ContentChangedError
from ..repositories.configuration_common import ConfigurationFileError
from ..repositories.mission_map_journal import MissionMapJournalError
from ..repositories.mission_map_publication import MissionMapPublicationError
from ..repositories.profiles import ProfileNotFound, ProfileStorageError
from ..repositories.restore_paths import RestorePathError
from .medical_features import FEATURE_PATHS, MedicalFeatureService
from .mission_configuration import MissionPathError
from .mission_map_apply_coordinator import apply_error
from .mission_map_recovery import EDITOR_KIND, RECOVERY_UNRESOLVED
from .operations.manager import OperationManager
from .operations.models import OperationFailure, QueueUnavailable
from .settings import SettingsValidationError

# Fields accepted by both the preview and apply handlers
EDIT_FIELDS = {"profile_id", "feature", "enabled", "expected_profile_revision",
               "expected_settings_revision", "expected_digest"}
# The reviewed zone fingerprint that a zone Apply on an editor-managed mission adds (8.13, customer decision 4)
FINGERPRINT = "preview_fingerprint"
# Failures of the D4 commit step of the coordinated zone toggle; they keep the codes of the map editor Apply
EDITOR_ERRORS = (LifecycleFailure, MissionMapPublicationError, MissionMapJournalError, RestorePathError)


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
            raise _with_zone_baseline(error, _error(error)) from error

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
        # Validate the request and derive the service arguments; only a zone Apply may add its fingerprint
        fingerprint = parameters.get(FINGERPRINT)
        _exact(parameters, EDIT_FIELDS | ({FINGERPRINT} if FINGERPRINT in parameters else set()))
        if FINGERPRINT in parameters and (parameters["feature"] != "medical_loot_zones" or not isinstance(fingerprint, str)):
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "medical feature parameters are invalid")
        arguments = _args(parameters)
        def work(context: Any) -> dict[str, Any]:
            """Apply the toggle inside the mutation lane; a D4 failure that needs recovery blocks the editor kind."""
            try:
                return self._service.apply(**arguments, checkpoint=context.checkpoint, preview_fingerprint=fingerprint,
                                           operation_id=context.operation_id)
            except Exception as error:
                call = _error(error)
                recovery = call.code == ErrorCode.RECOVERY_REQUIRED
                # Only the 8.5 recovery resolves an editor journal, so its blocks belong to the editor kind. They
                # carry the reason of a map editor Apply. An ordinary medical failure keeps the medical kind.
                owner = EDITOR_KIND if recovery and isinstance(error, EDITOR_ERRORS) else None
                if owner is not None:
                    context.block_for_recovery(RECOVERY_UNRESOLVED, owner=owner)
                raise OperationFailure(call.code.value, call.safe_message, recovery_required=recovery,
                                       recovery_owner=owner) from error
        # Submit the toggle with its replay-safe checkpoint phases
        try:
            record = self._operations.submit("APPLY_MEDICAL_FEATURE", work,
                safe_points=frozenset(("loaded", "validated")),
                log_fields={"profile_id": str(parameters["profile_id"]),
                            "feature": str(parameters["feature"])},
                target_profile_id=str(parameters["profile_id"]))
        except QueueUnavailable as error:
            raise ApplicationCallError(
                ErrorCode.MUTATION_CONFLICT, str(error), retryable=True,
                details=error.details,
            ) from error
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


def _with_zone_baseline(error: Exception, call: ApplicationCallError) -> ApplicationCallError:
    """Add the loot-zone classification that the zone refusal carries; code, message and retry flag stay (6.5)."""
    baseline = getattr(error, "zone_baseline", None)
    if baseline is None:
        return call
    return ApplicationCallError(call.code, call.safe_message, retryable=call.retryable,
                                details={"zone_baseline": baseline})


def _error(error: Exception) -> ApplicationCallError:
    """Map medical feature failures to stable bridge error codes."""
    # The coordinated zone toggle keeps the Apply codes, also a running server's refusal (8.13)
    if isinstance(error, EDITOR_ERRORS):
        return apply_error(error)
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
