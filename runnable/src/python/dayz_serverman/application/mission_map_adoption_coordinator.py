"""Name the adoption owner methods of the map editor: the preview (10.13) and the adopt step (10.35, D16, A11).

The preview is a synchronous read of the owner session, as the Apply and restore previews (AR-1). The adopt step
runs as lane kind `ADOPT_MISSION_MAP`. A refusal code raised at or after `COMMITTING` becomes a fixed failure code
(AR-4). A failure that needs recovery blocks the lane under the editor recovery kind, which the 8.5 recovery lifts.
Construction registers nothing; the handler table takes `handlers()`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError, BridgeHandler
from ..domain.mission_map_adoption import ADOPT_LANE_KIND
from .mission_map_adoption import MissionMapAdoption
from .mission_map_apply_coordinator import apply_error, late_failure
from .mission_map_coordinator_errors import exact_parameters, refusal
from .mission_map_recovery import EDITOR_KIND, RECOVERY_UNRESOLVED
from .operations.manager import OperationManager
from .operations.models import OperationCancelled, OperationFailure, QueueUnavailable

# Exact request fields of the adoption preview and of the adopt step
PREVIEW_FIELDS = {"profile_id"}
ADOPT_FIELDS = frozenset(("profile_id", "preview_fingerprint"))


class MissionMapAdoptionCoordinator:
    """The adoption methods of the editor; each one is owner-only (A11)."""

    def __init__(self, adoption: MissionMapAdoption, operations: OperationManager | None = None) -> None:
        """Keep the adoption service and the authoritative operation lane."""
        self.adoption, self._operations = adoption, operations

    def handlers(self) -> Mapping[str, BridgeHandler]:
        """Return the bridge methods in handler-table order."""
        return {"preview_mission_map_adoption": self.preview_mission_map_adoption,
                "adopt_mission_map": self.adopt_mission_map}

    def adopt_mission_map(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue one adoption and return its operation handle; a malformed request is refused before the lane."""
        if set(parameters) != ADOPT_FIELDS or not isinstance(parameters["preview_fingerprint"], str) or \
                self._operations is None:
            raise refusal(ErrorCode.INVALID_REQUEST, "invalid_document", "The map editor parameters are invalid.")

        def work(context: Any) -> dict[str, Any]:
            """Adopt inside the lane; the editor recovery owns any recovery block."""
            try:
                return self.adoption.adopt(parameters["profile_id"], parameters["preview_fingerprint"],
                                           operation_id=context.operation_id)
            except OperationCancelled:
                raise
            except Exception as error:
                # A refusal code at or after the commit point never claims that nothing changed (AR-4)
                call = late_failure(apply_error(error), self.adoption.commit_point(context.operation_id))
                recovery = call.code == ErrorCode.RECOVERY_REQUIRED
                # The 8.5 recover handler lifts only blocks of the editor kind, so the block belongs to it. The
                # lane sets the one block with the catalogue reason (8.11 F1)
                owner = EDITOR_KIND if recovery else None
                reason = RECOVERY_UNRESOLVED if recovery else None
                raise OperationFailure(call.code.value, call.safe_message, retryable=call.retryable,
                                       recovery_required=recovery, recovery_owner=owner,
                                       recovery_reason=reason) from error

        try:
            record = self._operations.submit(ADOPT_LANE_KIND, work,
                                             log_fields={"profile_id": str(parameters["profile_id"])},
                                             target_profile_id=str(parameters["profile_id"]))
        except QueueUnavailable as error:
            raise ApplicationCallError(ErrorCode.MUTATION_CONFLICT, str(error), retryable=True,
                                       details=error.details) from error
        return {"operation_id": record.operation_id, "state": record.state.value}

    def preview_mission_map_adoption(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Review the adoption of the profile's current mission; the answer says when adoption is refused."""
        exact_parameters(parameters, PREVIEW_FIELDS)
        try:
            return self.adoption.preview(parameters["profile_id"])
        except Exception as error:
            # The refusals and failures of a review keep the codes and texts that the Apply preview gives
            raise apply_error(error) from error
