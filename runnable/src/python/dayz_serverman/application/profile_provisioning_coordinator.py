"""Bridge-facing mission queries and profile provisioning mutation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.models import RecordUnavailable
from ..domain.profiles import ProfileValidationError
from ..repositories.profiles import ProfileStorageError
from .mission_catalog import MissionCatalogError
from .operations.manager import OperationManager
from .operations.models import OperationFailure, QueueUnavailable
from .profile_provisioning import ProfileProvisioningError, ProfileProvisioningService
from .settings import SettingsValidationError


class ProfileProvisioningCoordinator:
    """Expose mission discovery and queue recoverable profile creation."""

    def __init__(self, service: ProfileProvisioningService, operations: OperationManager) -> None:
        """Store provisioning and operation services."""
        self._service = service
        self._operations = operations

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the provisioning bridge methods."""
        return {
            "list_profile_missions": self.list_profile_missions,
            "provision_profile": self.provision_profile,
        }

    def list_profile_missions(self, parameters: Mapping[str, Any]) -> dict[str, object]:
        """Return mission choices for the configured DayZ installation."""
        if parameters:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "mission parameters are invalid")
        try:
            return self._service.list_missions()
        except (ProfileValidationError, MissionCatalogError) as error:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error)) from error
        except (RecordUnavailable, SettingsValidationError) as error:
            raise ApplicationCallError(
                ErrorCode.RECOVERY_REQUIRED, "Manager settings require recovery.",
            ) from error

    def provision_profile(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue one profile provisioning request."""
        if set(parameters) != {"profile", "expected_settings_revision"}:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "provision parameters are invalid")
        profile = parameters["profile"]
        revision = parameters["expected_settings_revision"]
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "settings revision is invalid")

        def work(context) -> dict[str, object]:
            """Run provisioning and map safe operation failures."""
            try:
                return self._service.provision(
                    profile, revision, context.operation_id, context.checkpoint,
                )
            except (ProfileValidationError, MissionCatalogError) as error:
                raise OperationFailure("INVALID_REQUEST", str(error)) from error
            except ProfileProvisioningError as error:
                raise OperationFailure(
                    "RECOVERY_REQUIRED" if error.recovery_required else "PROVISION_FAILED",
                    str(error), recovery_required=error.recovery_required,
                ) from error
            except ProfileStorageError as error:
                raise OperationFailure(
                    "RECOVERY_REQUIRED" if error.recovery_required else "STORAGE_FAILURE",
                    str(error), recovery_required=error.recovery_required,
                ) from error
            except (OSError, RecordUnavailable, SettingsValidationError) as error:
                raise OperationFailure("STORAGE_FAILURE", "Profile provisioning failed.") from error

        try:
            record = self._operations.submit(
                "PROVISION_PROFILE", work,
                safe_points=frozenset({"stage_profile"}),
            )
        except QueueUnavailable as error:
            raise ApplicationCallError(
                ErrorCode.MUTATION_CONFLICT, str(error), retryable=True, details=error.details,
            ) from error
        return {"operation_id": record.operation_id, "state": record.state.value}
