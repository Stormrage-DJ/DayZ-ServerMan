"""Translate mission-map load, save and set-aside failures into the accepted bridge refusal contract."""

from __future__ import annotations

from typing import Any, Mapping

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.mission_map_families import FamilyNotVerified
from ..domain.mission_map_values import PlanValidationError
from ..domain.models import RecordUnavailable, RepositoryError, RevisionConflict
from ..domain.profiles import ProfileValidationError
from ..repositories.mission_map_plans import PlanNotInvalid, PlanRecordInvalid
from ..repositories.profiles import ProfileNotFound, ProfileStorageError
from .mission_configuration import MissionPathError
from .mission_map_plans import AdoptionRequired, AssociationMismatch, MissionPathChanged, ObserverWriteRefused
from .settings import SettingsValidationError


def refusal(code: ErrorCode, reason: str, message: str, *, retryable: bool = False,
            **details: Any) -> ApplicationCallError:
    """Build a safe refusal with its closed reason code and optional problem list."""
    # Every invalid document refusal gives the window a document-level or object-level problem
    if reason == "invalid_document" and "problems" not in details:
        details["problems"] = [{"object_id": None, "rule": "invalid_document", "message": message}]
    return ApplicationCallError(code, message, retryable=retryable, details={"reason": reason, **details})


def exact_parameters(parameters: Mapping[str, Any], fields: set[str]) -> None:
    """Refuse missing or unknown parameters before reading the profile or document."""
    if set(parameters) != fields:
        raise refusal(ErrorCode.INVALID_REQUEST, "invalid_document", "The map editor parameters are invalid.")


def translate(error: Exception) -> ApplicationCallError:
    """Map each expected service failure to an existing code, reason and retryability."""
    # Invalid documents report each affected object; invalid saved bytes cannot be replaced
    if isinstance(error, PlanValidationError):
        problems = [{"object_id": problem.object_id, "rule": "invalid_document", "message": problem.message}
                    for problem in error.problems]
        return refusal(ErrorCode.INVALID_REQUEST, "invalid_document", str(error), problems=problems)
    if isinstance(error, PlanRecordInvalid):
        return refusal(ErrorCode.REVISION_CONFLICT, "invalid_saved_data",
                       "The saved plan is invalid. Reload it, then set it aside explicitly before saving.")
    # Set aside found a valid or missing plan: the window shows a state that is no longer true
    if isinstance(error, PlanNotInvalid):
        return refusal(ErrorCode.REVISION_CONFLICT, "plan_not_invalid",
                       "The saved plan is not invalid now. Reload the plan to see its current state.")
    if isinstance(error, RevisionConflict):
        return refusal(ErrorCode.REVISION_CONFLICT, "stale_revision",
                       "The plan or its records changed. Reload the plan before saving.", retryable=True)
    # Association failures never let a stale editor address another mission
    if isinstance(error, MissionPathChanged):
        return refusal(ErrorCode.PROFILE_CONTEXT_MISMATCH, error.reason,
                       "The mission path changed. Reload the plan; adoption is required before saving.",
                       folders=error.folders)
    if isinstance(error, AssociationMismatch):
        return refusal(ErrorCode.PROFILE_CONTEXT_MISMATCH, "association_mismatch", str(error))
    # An invalidated last-applied record (AR-6): the ADOPT-6 Apply refusal, a state refusal that exits 3 (D13)
    if isinstance(error, AdoptionRequired):
        return refusal(ErrorCode.PROFILE_CONTEXT_MISMATCH, "adoption_required", str(error))
    if isinstance(error, ProfileNotFound):
        return refusal(ErrorCode.NOT_FOUND, "profile_not_found", "The selected profile no longer exists.")
    if isinstance(error, (MissionPathError, SettingsValidationError, RecordUnavailable, FileNotFoundError)):
        return refusal(ErrorCode.NOT_FOUND, "mission_unresolved", "The mission of this profile could not be resolved.")
    # Only the observer guard is a control refusal; a denied or shared file is a storage failure (QA-5.3-F3)
    if isinstance(error, ObserverWriteRefused):
        return refusal(ErrorCode.CONTROL_CONFLICT, "storage_failure", "An observer session cannot save a plan.")
    # Storage failures keep their underlying details out of the public envelope
    if isinstance(error, (OSError, RepositoryError, ProfileStorageError)):
        return refusal(ErrorCode.STORAGE_FAILURE, "storage_failure", "The map editor data could not be stored or read.",
                       retryable=True)
    # Apply or Restore original on an unverified family (6.11): the family is not supported; the plan stays usable
    if isinstance(error, FamilyNotVerified):
        return refusal(ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT, error.gate.reason, str(error),
                       operation=error.operation, terrain_id=error.gate.terrain_id)
    if isinstance(error, (ValueError, ProfileValidationError)):
        return refusal(ErrorCode.INVALID_REQUEST, "invalid_document", str(error))
    raise error
