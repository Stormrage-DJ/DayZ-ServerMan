"""Strict bridge handler for the "Verify files" operation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.profiles import validate_profile_id
from .operations.manager import OperationManager
from .operations.models import QueueUnavailable
from .workshop_verification import VerifyRequest, WorkshopVerificationService

# Exact field set of the request
_FIELDS = {"profile_id", "expected_profile_revision", "expected_settings_revision"}
# Phases at which a running verification stops when cancellation was requested
SAFE_POINTS = frozenset(("verify_source", "verify_target"))


class WorkshopVerificationCoordinator:
    """Bridge-facing coordinator that queues file verification on the operation lane."""

    def __init__(self, service: WorkshopVerificationService, operations: OperationManager) -> None:
        """Store the verification service and the operation manager."""
        self._service = service
        self._operations = operations

    def handlers(self) -> dict[str, Any]:
        """Return the bridge handler table for the verification call."""
        return {"verify_mod_files": self.verify_mod_files}

    def verify_mod_files(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Validate a verification request and submit it as an operation."""
        # Enforce the exact request envelope before reading fields
        if set(parameters) != _FIELDS:
            raise ApplicationCallError(
                ErrorCode.INVALID_REQUEST, "Verification request fields are missing or unknown.",
            )
        try:
            profile_id = validate_profile_id(parameters.get("profile_id"))
        except ValueError as error:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error)) from error
        request = VerifyRequest(
            profile_id,
            _revision(parameters.get("expected_profile_revision"), "expected_profile_revision"),
            _revision(parameters.get("expected_settings_revision"), "expected_settings_revision"),
        )
        # The lane serializes the proof writes with SteamCMD runs and publications
        try:
            record = self._operations.submit(
                "VERIFY_WORKSHOP_FILES",
                lambda context: self._service.verify(request, context),
                safe_points=SAFE_POINTS,
                log_fields={"profile_id": profile_id},
                target_profile_id=profile_id,
            )
        except QueueUnavailable as error:
            # A saturated or blocked lane surfaces as a retryable mutation conflict
            raise ApplicationCallError(
                ErrorCode.MUTATION_CONFLICT, str(error), retryable=True, details=error.details,
            ) from error
        return {"operation_id": record.operation_id, "state": record.state.value}


def _revision(value: object, field: str) -> int:
    """Validate a non-negative revision integer."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, f"{field} is invalid")
    return value
