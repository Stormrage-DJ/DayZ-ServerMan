"""Set editor recovery blocks at startup and serve the on-demand recovery handler (8.5, registered in 8.9)."""

from __future__ import annotations

from collections.abc import Mapping

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from .folder_writer_scope import STARTUP_RECOVERY_WAIT_SECONDS
from .mission_map_recovery import EDITOR_KIND, RECOVERY_NO_ROOT, RECOVERY_UNRESOLVED, MissionMapRecovery
from .operations.manager import OperationManager


class MissionMapRecoveryCoordinator:
    """Own only editor recovery blocks; the publication coordinator registers the handler (8.9)."""

    def __init__(self, service: MissionMapRecovery, operations: OperationManager) -> None:
        """Keep the recovery service and the authoritative operation lane."""
        self.service, self.operations = service, operations

    def startup(self) -> None:
        """Recover before owner handlers are exposed, using the startup writer wait."""
        self._apply_block(self.service.inspect(wait_seconds=STARTUP_RECOVERY_WAIT_SECONDS))

    def inspect_mission_map_recovery(self, parameters: Mapping[str, object]) -> dict[str, object]:
        """Defer while the lane is active; otherwise recover and clear only editor-owned blocks."""
        if parameters:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "Mission map recovery takes no parameters.")
        if not self.operations.is_drained():
            return {"blocked": True, "deferred": True, "diagnostics": []}
        result = self.service.inspect()
        self._apply_block(result)
        return result

    def _apply_block(self, result: dict[str, object]) -> None:
        """Preserve unrelated blocks and distinguish the repairable missing-root cause."""
        if result.get("cleanup_deferred"):
            return
        if not result["blocked"]:
            self.operations.clear_recovery_block(EDITOR_KIND)
        elif result.get("reason") == RECOVERY_NO_ROOT:
            self.operations.block_for_missing_dayz_root(RECOVERY_NO_ROOT, owner=EDITOR_KIND)
        else:
            self.operations.block_for_recovery(str(result.get("reason", RECOVERY_UNRESOLVED)), owner=EDITOR_KIND)
