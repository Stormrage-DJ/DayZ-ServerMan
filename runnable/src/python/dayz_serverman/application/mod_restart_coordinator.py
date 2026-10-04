"""Lane operation that applies reviewed mods to a running server: check, stop, backup, apply, start.

The coordinator owns the order only. The server state, the installation mutex,
every copy and the one start belong to the lifecycle, backup and publication
services; none of them knows this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.lifecycle import LifecycleFailure
from .backup_coordinator import _query_error as backup_query_error
from .backups import BackupService
from .lifecycle import ServerLifecycleService
from .lifecycle_coordinator import BACKUP_SAFE_POINTS
from .mod_publication import ModPublicationService, PublicationRequest
from .mod_publication_coordinator import (
    PUBLICATION_SAFE_POINTS,
    _request as publication_request,
    operation_failure,
)
from .operations.context import OperationContext
from .operations.manager import OperationManager
from .operations.models import OperationCancelled, OperationFailure, QueueUnavailable

# Phases of the pre-start check and the start keep the percent that the publication gives them
_START_PHASES = frozenset(("VERIFY_BEFORE_START", "START_SERVER"))


class ModRestartCoordinator:
    """Serve `apply_mods_and_restart`: one operation from the plan check to the server start."""

    def __init__(
        self, publication: ModPublicationService, lifecycle: ServerLifecycleService,
        backups: BackupService | None, operations: OperationManager,
    ) -> None:
        """Store the three services whose calls this coordinator orders, and the lane."""
        self._publication = publication
        self._lifecycle = lifecycle
        self._backups = backups
        self._operations = operations

    def handlers(self) -> dict[str, Any]:
        """Return the bridge handler table of the restart call."""
        return {"apply_mods_and_restart": self.apply}

    def apply(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue the reviewed apply with a stop before it and a start after it."""
        # The request is the publish request plus the backup flag, as an exact field set
        backup = parameters.get("backup_after_stop") if isinstance(parameters, Mapping) else None
        if not isinstance(backup, bool):
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "publication parameters are invalid")
        request = publication_request(
            {key: value for key, value in parameters.items() if key != "backup_after_stop"},
            include_fingerprint=True,
        )
        fingerprint = parameters["publication_fingerprint"]

        def work(context: OperationContext) -> Mapping[str, Any]:
            """Run the steps in order; each service checks its own preconditions."""
            try:
                return self._run(request, fingerprint, backup, context)
            except (OperationCancelled, OperationFailure):
                raise
            except LifecycleFailure as error:
                raise OperationFailure(
                    error.code, error.safe_message, retryable=error.retryable,
                    recovery_required=error.recovery_required,
                ) from error
            except Exception as error:
                raise operation_failure(error) from error

        # Cancellation stops at the plan check and at the backup and publication safe points
        try:
            record = self._operations.submit(
                "APPLY_MODS_AND_RESTART", work,
                safe_points=frozenset(("preflight",)) | BACKUP_SAFE_POINTS | PUBLICATION_SAFE_POINTS,
                log_fields={"profile_id": request.profile_id, "target_role": "dayz_server"},
                target_profile_id=request.profile_id,
            )
        except QueueUnavailable as error:
            raise ApplicationCallError(
                ErrorCode.MUTATION_CONFLICT, str(error), retryable=True, details=error.details,
            ) from error
        return {"operation_id": record.operation_id, "state": record.state.value}

    def _run(
        self, request: PublicationRequest, fingerprint: str, backup: bool,
        context: OperationContext,
    ) -> Mapping[str, Any]:
        """Confirm the plan, stop, back up when asked, then hand over to the publication."""
        context.checkpoint("preflight", 3)
        # Nothing stops before the reviewed plan is confirmed again
        if not self._publication.confirm_restart_plan(request, fingerprint):
            return _nothing_to_apply(request)
        # A cancellation that arrived during the plan check ends here: the server keeps running
        context.checkpoint("preflight", 3)
        context.checkpoint("STOP_SERVER", 8)
        self._lifecycle.stop(request.settings_revision)
        backup_result = self._create_backup(request, context) if backup else None
        # The publication takes the write guard, applies, checks and starts the server
        result = self._publication.publish(request, fingerprint, _PublicationProgress(context))
        return {**result, "backup": backup_result}

    def _create_backup(
        self, request: PublicationRequest, context: OperationContext,
    ) -> dict[str, Any]:
        """Run the backup service with the error mapping of the lifecycle operations."""
        if self._backups is None:
            raise OperationFailure("BACKUP_UNAVAILABLE", "Backup service is unavailable.")
        # Name the backup before its service runs, so a failure there is told as a backup failure
        context.checkpoint("BACKUP_DISCOVER", 10)
        try:
            # Scale the backup progress into the 10-35 percent band
            return self._backups.create(
                request.profile_id, request.profile_revision, request.settings_revision,
                lambda phase, percent: context.checkpoint(f"BACKUP_{phase}", 10 + percent // 4),
            )
        except OperationCancelled:
            raise
        except Exception as error:
            call = backup_query_error(error)
            raise OperationFailure(
                call.code.value, call.safe_message, retryable=call.retryable,
                recovery_required=call.code == ErrorCode.RECOVERY_REQUIRED,
            ) from error


class _PublicationProgress:
    """Operation context of the publication step: the same operation, progress in its own band."""

    def __init__(self, context: OperationContext) -> None:
        """Store the context of the restart operation."""
        self._context = context

    @property
    def operation_id(self) -> str:
        """Return the identifier of the restart operation."""
        return self._context.operation_id

    @property
    def cancellation_requested(self) -> bool:
        """Report whether cancellation was requested for the restart operation."""
        return self._context.cancellation_requested

    def checkpoint(self, phase: str, percent: int) -> None:
        """Forward the phase; scale the apply progress into the 35-88 percent band."""
        scaled = percent if phase in _START_PHASES else min(88, 35 + percent * 53 // 55)
        self._context.checkpoint(phase, scaled)

    def record_evidence(self, evidence: Mapping[str, object]) -> None:
        """Forward the publication evidence to the restart operation."""
        self._context.record_evidence(evidence)


def _nothing_to_apply(request: PublicationRequest) -> dict[str, object]:
    """Return the result of a restart request whose plan writes nothing: no stop, no start."""
    return {
        "profile_id": request.profile_id,
        "update_operation_id": request.update_operation_id,
        "publication_state": "UNCHANGED",
        "start_requested": True,
        "start_authorized": False,
        "start_state": "NOT_NEEDED",
        "start_error": None,
        "prestart_check": None,
        "backup": None,
    }
