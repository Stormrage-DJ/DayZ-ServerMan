"""Set editor recovery blocks at startup and serve the on-demand recovery handler (8.5, registered in 8.9)."""

from __future__ import annotations

from collections.abc import Mapping

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..observability.structured_log import StructuredLogger
from .folder_writer_scope import STARTUP_RECOVERY_WAIT_SECONDS
from .mission_map_recovery import EDITOR_KIND, RECOVERY_NO_ROOT, RECOVERY_UNRESOLVED, MissionMapRecovery
from .operations.manager import OperationManager

# What started a recovery: the owner startup, or Check again and `map recover` (8.12 Architect decision 1)
TRIGGER_STARTUP = "STARTUP"
TRIGGER_ON_DEMAND = "ON_DEMAND"


class MissionMapRecoveryCoordinator:
    """Own only editor recovery blocks; the publication coordinator registers the handler (8.9)."""

    def __init__(
        self, service: MissionMapRecovery, operations: OperationManager, logger: StructuredLogger | None = None,
    ) -> None:
        """Keep the recovery service, the authoritative operation lane and the optional manager log."""
        self.service, self.operations, self.logger = service, operations, logger

    def startup(self) -> None:
        """Recover before owner handlers are exposed, using the startup writer wait."""
        self._apply_block(self.service.inspect(wait_seconds=STARTUP_RECOVERY_WAIT_SECONDS), TRIGGER_STARTUP)

    def inspect_mission_map_recovery(self, parameters: Mapping[str, object]) -> dict[str, object]:
        """Defer while the lane is active; otherwise recover and clear only editor-owned blocks."""
        if parameters:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "Mission map recovery takes no parameters.")
        if not self.operations.is_drained():
            return {"blocked": True, "deferred": True, "diagnostics": []}
        result = self.service.inspect()
        self._apply_block(result, TRIGGER_ON_DEMAND)
        return result

    def _apply_block(self, result: dict[str, object], trigger: str) -> None:
        """Log each journal, then preserve unrelated blocks and distinguish the repairable missing-root cause."""
        # The internal journal items never reach the bridge answer, the window or the CLI
        journals = result.pop("journals", [])
        reason = str(result.get("reason", RECOVERY_UNRESOLVED))
        self._log_journals(journals, reason, trigger)
        if result.get("cleanup_deferred"):
            return
        if not result["blocked"]:
            self.operations.clear_recovery_block(EDITOR_KIND)
        elif result.get("reason") == RECOVERY_NO_ROOT:
            self.operations.block_for_missing_dayz_root(RECOVERY_NO_ROOT, owner=EDITOR_KIND)
        else:
            self.operations.block_for_recovery(reason, owner=EDITOR_KIND)

    def _log_journals(self, journals: object, reason: str, trigger: str) -> None:
        """Write one WARNING line per resolved or kept journal; the lines hold no path, root, profile or mission."""
        for item in journals if isinstance(journals, list) else ():
            if "unreadable" in item:
                # Unreadable journals have no trusted id; one line names their count
                self._write("mission_map.recovery_blocked", None,
                            {"reason": reason, "unreadable": item["unreadable"], "trigger": trigger})
            elif item["result"] is None:
                self._write("mission_map.recovery_blocked", item["operation_id"],
                            {"operation_kind": item["operation_kind"], "phase": item["phase"],
                             "reason": reason, "trigger": trigger})
            else:
                self._write("mission_map.recovery_resolved", item["operation_id"],
                            {"operation_kind": item["operation_kind"], "phase": item["phase"],
                             "result": item["result"], "trigger": trigger})

    def _write(self, event: str, operation_id: object, fields: dict[str, object]) -> None:
        """Emit one line; a missing logger or a failed write never changes the recovery or the block."""
        if self.logger is None:
            return
        try:
            self.logger.emit(event, level="WARNING", operation_id=operation_id if isinstance(operation_id, str) else None,
                             fields=fields)
        except (OSError, TypeError, ValueError):
            return
