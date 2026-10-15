"""Queue the `APPLY_MISSION_MAP` lane operation and word its failures (8.7).

The handler table registers `apply_mission_map` through the publication
coordinator (8.9). A failure becomes an operation failure with an existing
error code.

The family gate keeps the 6.11 refusal: `UNSUPPORTED_SNAPSHOT_CONTENT` with
the gate text, which the activity view shows unchanged through
`SHOWN_HOST_TEXTS`. A failure that needs recovery blocks the lane under the
editor kind until the 8.5 recovery lifts the block.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.lifecycle import LifecycleFailure
from ..domain.mission_map_families import FamilyNotVerified
from ..domain.models import RevisionConflict
from ..repositories.mission_map_journal import MissionMapJournalError
from ..repositories.mission_map_publication import MissionMapPublicationError
from ..repositories.restore_paths import RestorePathError
from .mission_map_apply import MissionMapApply
from .mission_map_apply_choices import ApplyRefused
from .mission_map_coordinator_errors import refusal, translate
from .mission_map_preview import NoSavedPlan
from .mission_map_recovery import EDITOR_KIND, RECOVERY_UNRESOLVED
from .operations.manager import OperationManager
from .operations.models import OperationCancelled, OperationFailure, QueueUnavailable

# Exact request fields of the lane operation
APPLY_FIELDS = frozenset(("profile_id", "preview_fingerprint", "choices"))
# Bridge codes of the commit-step refusals; any other refusal is a refused request
PUBLICATION_CODES = {"REVISION_CONFLICT": ErrorCode.REVISION_CONFLICT, "TARGET_CHANGED": ErrorCode.REVISION_CONFLICT,
                     "PATH_INVALID": ErrorCode.PATH_INVALID}


class MissionMapApplyCoordinator:
    """Submit Apply to the operation lane; construction registers nothing."""

    def __init__(self, service: MissionMapApply, operations: OperationManager) -> None:
        """Keep the Apply service and the authoritative operation lane."""
        self._service, self._operations = service, operations

    def apply_mission_map(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue one Apply and return its operation handle; a malformed request is refused before the lane."""
        if set(parameters) != APPLY_FIELDS or not isinstance(parameters["preview_fingerprint"], str):
            raise refusal(ErrorCode.INVALID_REQUEST, "invalid_document", "The map editor parameters are invalid.")

        def work(context: Any) -> dict[str, Any]:
            """Apply inside the lane; the kind of this operation owns any recovery block."""
            try:
                return self._service.apply(parameters["profile_id"], parameters["preview_fingerprint"],
                                           parameters["choices"], operation_id=context.operation_id)
            except OperationCancelled:
                raise
            except Exception as error:
                call = apply_error(error)
                recovery = call.code == ErrorCode.RECOVERY_REQUIRED
                if recovery:
                    context.block_for_recovery(RECOVERY_UNRESOLVED)
                raise OperationFailure(call.code.value, call.safe_message, retryable=call.retryable,
                                       recovery_required=recovery) from error

        try:
            record = self._operations.submit(EDITOR_KIND, work,
                                             log_fields={"profile_id": str(parameters["profile_id"])},
                                             target_profile_id=str(parameters["profile_id"]))
        except QueueUnavailable as error:
            raise ApplicationCallError(ErrorCode.MUTATION_CONFLICT, str(error), retryable=True,
                                       details=error.details) from error
        return {"operation_id": record.operation_id, "state": record.state.value}


def apply_error(error: Exception) -> ApplicationCallError:
    """Map one Apply failure to an existing code; the 6.11 refusal keeps its code, reason and text."""
    if isinstance(error, FamilyNotVerified):
        # The 6.11 envelope of the 3.6 translation, before the ValueError rule of a refused request
        return translate(error)
    if isinstance(error, LifecycleFailure):
        code = ErrorCode.__members__.get(error.code, ErrorCode.INTERNAL_FAILURE)
        return ApplicationCallError(code, error.safe_message, retryable=error.retryable)
    if isinstance(error, ApplyRefused):
        return refusal(ErrorCode.INVALID_REQUEST, error.reason, str(error), path=error.path)
    if isinstance(error, NoSavedPlan):
        # The Product Owner text (evidence 8.7), never the request text or a problem list of an invalid document
        return refusal(ErrorCode.INVALID_REQUEST, "no_saved_plan", str(error))
    if isinstance(error, MissionMapPublicationError):
        return _publication_error(error)
    if isinstance(error, (MissionMapJournalError, RestorePathError)):
        return refusal(ErrorCode.RECOVERY_REQUIRED, "recovery_required", "The mission map apply requires recovery.")
    if isinstance(error, RevisionConflict):
        # A stale preview: review the preview again (retryable); the message is the Apply or the restore stale text
        return refusal(ErrorCode.REVISION_CONFLICT, "preview_stale", str(error), retryable=True)
    return translate(error)


def _publication_error(error: MissionMapPublicationError) -> ApplicationCallError:
    """Map a refusal or failure of the commit step; a failure after the commit point needs recovery."""
    details: dict[str, Any] = dict(getattr(error, "details", None) or {})
    reason = str(details.pop("reason", error.code.lower()))
    if error.recovery_required or error.code == "RECOVERY_REQUIRED":
        return refusal(ErrorCode.RECOVERY_REQUIRED, reason, str(error))
    code = PUBLICATION_CODES.get(error.code, ErrorCode.INVALID_REQUEST)
    retryable = bool(getattr(error, "retryable", code == ErrorCode.REVISION_CONFLICT))
    return refusal(code, reason, str(error), retryable=retryable, **details)
