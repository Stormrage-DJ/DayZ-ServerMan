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
# Codes that keep their meaning at or after the commit point; any other code there is a refusal code (AR-4)
FAILURE_CODES = frozenset((ErrorCode.RECOVERY_REQUIRED, ErrorCode.PUBLICATION_FAILED, ErrorCode.INTERNAL_FAILURE,
                           ErrorCode.STORAGE_FAILURE))
# The host message of an Apply that needs recovery
RECOVERY_MESSAGE = "The mission map apply requires recovery."
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
                call = late_failure(apply_error(error), self._service.commit_point(context.operation_id))
                recovery = call.code == ErrorCode.RECOVERY_REQUIRED
                # The lane sets the one editor block with the catalogue reason; the record keeps the
                # commit-step message for diagnostics (8.11 F1). The Apply kind is the editor kind
                owner = EDITOR_KIND if recovery else None
                reason = RECOVERY_UNRESOLVED if recovery else None
                raise OperationFailure(call.code.value, call.safe_message, retryable=call.retryable,
                                       recovery_required=recovery, recovery_owner=owner,
                                       recovery_reason=reason) from error

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
        return refusal(ErrorCode.RECOVERY_REQUIRED, "recovery_required", RECOVERY_MESSAGE)
    if isinstance(error, RevisionConflict):
        # A stale preview: review the preview again (retryable); the message is the Apply or the restore stale text
        return refusal(ErrorCode.REVISION_CONFLICT, "preview_stale", str(error), retryable=True)
    return translate(error)


def late_failure(call: ApplicationCallError, point: str | None) -> ApplicationCallError:
    """Return the failure of a map lane operation by its commit point (AR-4, D13).

    A refusal code raised at or after COMMITTING becomes a fixed failure code. So the record never claims that
    nothing changed. The code is RECOVERY_REQUIRED while the journal is active, and PUBLICATION_FAILED after the
    journal was retired as committed. Before the commit point, and for a failure code, the failure stays as it is.
    """
    if point is None or call.code in FAILURE_CODES:
        return call
    if point == "active":
        return refusal(ErrorCode.RECOVERY_REQUIRED, "recovery_required", RECOVERY_MESSAGE)
    return ApplicationCallError(ErrorCode.PUBLICATION_FAILED, call.safe_message)


def _publication_error(error: MissionMapPublicationError) -> ApplicationCallError:
    """Map a refusal or failure of the commit step; a failure after the commit point needs recovery."""
    details: dict[str, Any] = dict(getattr(error, "details", None) or {})
    reason = str(details.pop("reason", error.code.lower()))
    if error.recovery_required or error.code == "RECOVERY_REQUIRED":
        return refusal(ErrorCode.RECOVERY_REQUIRED, reason, str(error))
    code = PUBLICATION_CODES.get(error.code, ErrorCode.INVALID_REQUEST)
    retryable = bool(getattr(error, "retryable", code == ErrorCode.REVISION_CONFLICT))
    return refusal(code, reason, str(error), retryable=retryable, **details)
