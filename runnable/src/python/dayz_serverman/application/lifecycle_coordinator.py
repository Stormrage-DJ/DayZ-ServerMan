"""Named bridge methods for reconciled server lifecycle operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.lifecycle import LifecycleFailure
from .lifecycle import ServerLifecycleService
from .backups import BackupService
from .backup_coordinator import _query_error as backup_query_error
from .operations.context import OperationContext
from .operations.manager import OperationManager
from .operations.models import OperationCancelled, OperationFailure, QueueUnavailable


# Backup checkpoint phases that may be safely replayed after a restart
BACKUP_SAFE_POINTS = frozenset(
    f"BACKUP_{phase}" for phase in ("DISCOVER", "STAGE", "HASH", "WRITE_MANIFEST", "VERIFY")
)


class LifecycleCoordinator:
    """Queue lifecycle operations and optionally chain a backup after a stop."""

    def __init__(
        self, lifecycle: ServerLifecycleService, operations: OperationManager,
        backups: BackupService | None = None,
    ) -> None:
        """Bind lifecycle, operations, and the optional backup service."""
        self._lifecycle = lifecycle
        self._operations = operations
        self._backups = backups

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the bridge handler table for lifecycle use cases."""
        return {
            "get_server_status": self.get_server_status,
            "start_server": self.start_server,
            "stop_server": self.stop_server,
            "restart_server": self.restart_server,
        }

    def get_server_status(self, parameters: Mapping[str, Any]) -> dict[str, object]:
        """Return the reconciled server status snapshot."""
        # This query takes no parameters
        _exact_fields(parameters, set())
        return self._lifecycle.status().to_dict()

    def start_server(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue a start request and return its operation handle."""
        # Parse and validate the start parameters
        profile_id, profile_revision, settings_revision = _start_parameters(parameters)
        return self._submit(
            "START_SERVER",
            lambda _context: self._lifecycle.start(
                profile_id, profile_revision, settings_revision,
            ).to_dict(),
            {"profile_id": profile_id, "target_role": "dayz_server"},
        )

    def stop_server(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue a stop and optionally chain a verified backup afterwards."""
        # Parse the stop parameters including the backup flag
        profile_id, profile_revision, settings_revision, backup = _control_parameters(parameters)

        def action(context: OperationContext) -> Mapping[str, Any]:
            """Stop the server and optionally create the post-stop backup."""
            context.checkpoint("STOP_SERVER", 20)
            result = self._lifecycle.stop(settings_revision).to_dict()
            # Chain a verified backup after the server is confirmed stopped
            if backup:
                result["backup"] = self._create_backup(
                    profile_id, profile_revision, settings_revision, context,
                )
            return result

        # Allow replay only of backup checkpoints when a backup is chained
        return self._submit(
            "STOP_SERVER",
            action,
            {"profile_id": profile_id, "target_role": "dayz_server"},
            safe_points=BACKUP_SAFE_POINTS if backup else frozenset(),
        )

    def restart_server(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue a restart, chaining stop, backup, and start when requested."""
        # Parse the restart parameters including the backup flag
        profile_id, profile_revision, settings_revision, backup = _control_parameters(parameters)
        if backup:
            def action(context: OperationContext) -> Mapping[str, Any]:
                """Stop, back up, then start the server again inside the lane."""
                context.checkpoint("STOP_SERVER", 15)
                self._lifecycle.stop(settings_revision)
                # Back up while the server is closed, then start it again
                backup_result = self._create_backup(
                    profile_id, profile_revision, settings_revision, context,
                )
                context.checkpoint("START_SERVER", 98)
                result = self._lifecycle.start(
                    profile_id, profile_revision, settings_revision,
                ).to_dict()
                result["backup"] = backup_result
                return result
        else:
            # Without a backup, use the service restart path directly
            action = lambda _context: self._lifecycle.restart(
                profile_id, profile_revision, settings_revision,
            ).to_dict()
        # Allow replay only of backup checkpoints when a backup is chained
        return self._submit(
            "RESTART_SERVER",
            action,
            {"profile_id": profile_id, "target_role": "dayz_server"},
            safe_points=BACKUP_SAFE_POINTS if backup else frozenset(),
        )

    def _create_backup(
        self, profile_id: str, profile_revision: int, settings_revision: int,
        context: OperationContext,
    ) -> dict[str, Any]:
        """Run the backup service inside the operation lane with shared error mapping."""
        # Fail when no backup service is wired into this deployment
        if self._backups is None:
            raise OperationFailure("BACKUP_UNAVAILABLE", "Backup service is unavailable.")
        try:
            # Run the backup with progress scaled into the 20-95 percent band
            return self._backups.create(
                profile_id, profile_revision, settings_revision,
                lambda phase, percent: context.checkpoint(
                    f"BACKUP_{phase}", 20 + (percent * 3 // 4),
                ),
            )
        # Keep cancellation propagating and map other failures to operation errors
        except OperationCancelled:
            raise
        except Exception as error:
            call = backup_query_error(error)
            raise OperationFailure(
                call.code.value, call.safe_message, retryable=call.retryable,
                recovery_required=call.code == ErrorCode.RECOVERY_REQUIRED,
            ) from error

    def _submit(
        self,
        kind: str,
        action: Callable[[OperationContext], Mapping[str, Any]],
        log_fields: Mapping[str, Any],
        *,
        safe_points: frozenset[str] = frozenset(),
    ) -> dict[str, str]:
        """Wrap the action with preflight progress and failure mapping, then queue it."""
        def work(context: OperationContext) -> Mapping[str, Any]:
            """Report preflight progress, then run the supplied action."""
            context.checkpoint("preflight", 10)
            # Convert lifecycle failures into operation failures with the same codes
            try:
                return action(context)
            except LifecycleFailure as error:
                raise OperationFailure(
                    error.code,
                    error.safe_message,
                    retryable=error.retryable,
                    recovery_required=error.recovery_required,
                ) from error

        # Surface queue contention as a retryable mutation conflict
        try:
            operation = self._operations.submit(
                kind, work, safe_points=safe_points, log_fields=log_fields,
            )
        except QueueUnavailable as error:
            raise ApplicationCallError(
                ErrorCode.MUTATION_CONFLICT,
                str(error),
                retryable=True,
            ) from error
        return {"operation_id": operation.operation_id, "state": operation.state.value}


def _start_parameters(parameters: Mapping[str, Any]) -> tuple[str, int, int]:
    """Parse and validate the fields shared by start and control requests."""
    # Require exactly the profile and both revision guards
    fields = {"profile_id", "expected_profile_revision", "expected_settings_revision"}
    _exact_fields(parameters, fields)
    profile_id = parameters["profile_id"]
    if not isinstance(profile_id, str):
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "profile_id is invalid")
    return (
        profile_id,
        _revision(parameters["expected_profile_revision"]),
        _revision(parameters["expected_settings_revision"]),
    )


def _control_parameters(parameters: Mapping[str, Any]) -> tuple[str, int, int, bool]:
    """Parse control fields and return the flag for a post-stop backup."""
    # Require the control fields including the backup flag
    fields = {
        "profile_id", "expected_profile_revision", "expected_settings_revision",
        "backup_after_stop",
    }
    _exact_fields(parameters, fields)
    # Reuse the shared start parsing for the three common fields
    profile_id, profile_revision, settings_revision = _start_parameters({
        key: parameters[key] for key in fields if key != "backup_after_stop"
    })
    backup = parameters["backup_after_stop"]
    # Require a real boolean backup flag
    if not isinstance(backup, bool):
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "backup_after_stop is invalid")
    return profile_id, profile_revision, settings_revision, backup


def _revision(value: object) -> int:
    """Return a valid revision guard or raise a bridge error."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "expected revision is invalid")
    return value


def _exact_fields(parameters: Mapping[str, Any], fields: set[str]) -> None:
    """Reject parameter sets that do not match the required fields."""
    if set(parameters) != fields:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "lifecycle parameters are invalid")
