"""Queue the two restore lane operations of the map editor and word their failures (leaf 8.8).

The handler table registers `restore_mission_map` through the publication
coordinator (8.9). The lane kind follows the mode: `RESTORE_MISSION_MAP_ORIGINAL` or
`RESTORE_MISSION_MAP_PREVIOUS`; their activity wording exists since 8.5.

Failures use the Apply mapping (8.7): the 6.11 refusal of Restore original
keeps `UNSUPPORTED_SNAPSHOT_CONTENT` with the gate text, a stale review is the
retryable `REVISION_CONFLICT` and a refused review is `INVALID_REQUEST`.

A refusal code raised at or after `COMMITTING` becomes a fixed failure code,
as for Apply (AR-4, leaf 10.32). Every editor journal kind shares one recovery
owner, so a failure that needs recovery blocks the lane under the editor kind.
The 8.5 recovery then lifts that block.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.mission_map_restore_texts import INVALID_MODE, INVALID_MODE_TEXT, LANE_KINDS
from .mission_map_apply_coordinator import apply_error, late_failure
from .mission_map_coordinator_errors import refusal
from .mission_map_recovery import EDITOR_KIND, RECOVERY_UNRESOLVED
from .mission_map_restore import MissionMapRestore
from .operations.manager import OperationManager
from .operations.models import OperationCancelled, OperationFailure, QueueUnavailable

# Exact request fields of the lane operation
RESTORE_FIELDS = frozenset(("profile_id", "mode", "preview_fingerprint"))


class MissionMapRestoreCoordinator:
    """Submit a restore to the operation lane; construction registers nothing."""

    def __init__(self, service: MissionMapRestore, operations: OperationManager) -> None:
        """Keep the restore service and the authoritative operation lane."""
        self._service, self._operations = service, operations

    def restore_mission_map(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue one restore and return its operation handle; a malformed request is refused before the lane."""
        if set(parameters) != RESTORE_FIELDS or not isinstance(parameters["preview_fingerprint"], str):
            raise refusal(ErrorCode.INVALID_REQUEST, "invalid_document", "The map editor parameters are invalid.")
        mode = parameters["mode"]
        # An unknown mode gets the 8.8 text unchanged (Product Owner 2026-10-15 07:10:16)
        if not isinstance(mode, str) or mode not in LANE_KINDS:
            raise refusal(ErrorCode.INVALID_REQUEST, INVALID_MODE, INVALID_MODE_TEXT)

        def work(context: Any) -> dict[str, Any]:
            """Restore inside the lane; the editor recovery owns any recovery block."""
            try:
                return self._service.restore(parameters["profile_id"], mode, parameters["preview_fingerprint"],
                                             operation_id=context.operation_id)
            except OperationCancelled:
                raise
            except Exception as error:
                # A refusal code at or after the commit point never claims that nothing changed (AR-4)
                call = late_failure(apply_error(error), self._service.commit_point(context.operation_id))
                recovery = call.code == ErrorCode.RECOVERY_REQUIRED
                # Not the restore kind: the 8.5 recover handler lifts only blocks of the editor kind (8.13 F1).
                # The lane sets the one block with the catalogue reason; the record keeps the commit-step
                # message for diagnostics (8.11 F1)
                owner = EDITOR_KIND if recovery else None
                reason = RECOVERY_UNRESOLVED if recovery else None
                raise OperationFailure(call.code.value, call.safe_message, retryable=call.retryable,
                                       recovery_required=recovery, recovery_owner=owner,
                                       recovery_reason=reason) from error

        try:
            record = self._operations.submit(LANE_KINDS[mode], work,
                                             log_fields={"profile_id": str(parameters["profile_id"]), "mode": mode},
                                             target_profile_id=str(parameters["profile_id"]))
        except QueueUnavailable as error:
            raise ApplicationCallError(ErrorCode.MUTATION_CONFLICT, str(error), retryable=True,
                                       details=error.details) from error
        return {"operation_id": record.operation_id, "state": record.state.value}
