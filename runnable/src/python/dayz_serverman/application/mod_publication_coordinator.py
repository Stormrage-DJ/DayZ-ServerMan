"""Strict bridge contracts for reviewed mod and key publication."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.profiles import validate_profile_id
from .mod_publication import ModPublicationError, ModPublicationService, PublicationRequest
from .operations.manager import OperationManager
from .operations.models import OperationCancelled, OperationFailure, QueueUnavailable


# Lowercase SHA-256 hex digest expected for review proofs
DIGEST = re.compile(r"[0-9a-f]{64}")


class ModPublicationCoordinator:
    """Serve mod and key publication calls over the shared operation lane."""

    def __init__(self, service: ModPublicationService, operations: OperationManager) -> None:
        """Store the publication service and the shared operation manager."""
        self._service = service
        self._operations = operations

    def handlers(self) -> dict[str, Any]:
        """Return the bridge handler table for publication calls."""
        return {
            "preview_mod_publication": self.preview,
            "publish_mods_and_keys": self.publish,
        }

    def preview(self, parameters: Mapping[str, Any]) -> dict[str, object]:
        """Return the reviewed publication plan for one update gate."""
        request = _request(parameters, include_fingerprint=False)
        # Translate review failures into bridge error calls
        try:
            return self._service.preview(request)
        except Exception as error:
            raise _call_error(error) from error

    def publish(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue a reviewed publication and optional server start."""
        request = _request(parameters, include_fingerprint=True)
        fingerprint = parameters["publication_fingerprint"]

        def work(context):
            """Publish the reviewed targets and map failures for the queue."""
            # Let queue cancellation pass through untouched
            try:
                return self._service.publish(request, fingerprint, context)
            except OperationCancelled:
                raise
            except ModPublicationError as error:
                call = _call_error(error)
                raise OperationFailure(
                    call.code.value, call.safe_message, retryable=call.retryable,
                    recovery_required=error.recovery_required,
                ) from error
            except Exception as error:
                call = _call_error(error)
                raise OperationFailure(
                    call.code.value, call.safe_message, retryable=call.retryable,
                    recovery_required=call.code == ErrorCode.RECOVERY_REQUIRED,
                ) from error

        # Queue the publication and surface a busy lane immediately
        try:
            record = self._operations.submit(
                "PUBLISH_MODS_AND_KEYS", work,
                safe_points=frozenset((
                    "PUBLICATION_PREFLIGHT", "DISCOVER_ITEM", "CACHE_PROOF_RECHECK",
                    "STAGE_TARGET", "CHECK_TARGET", "COPY_FILE", "COPY_KEY",
                    "BEFORE_PUBLICATION",
                )),
                log_fields={"profile_id": request.profile_id},
            )
        except QueueUnavailable as error:
            raise ApplicationCallError(ErrorCode.MUTATION_CONFLICT, str(error), retryable=True) from error
        return {"operation_id": record.operation_id, "state": record.state.value}


def _request(parameters: Mapping[str, Any], *, include_fingerprint: bool) -> PublicationRequest:
    """Validate a bridge payload and build the reviewed publication request."""
    # Accept the fingerprint only on the publish command
    expected = {
        "profile_id", "expected_profile_revision", "expected_semantic_profile_digest",
        "expected_settings_revision", "update_operation_id",
    }
    if include_fingerprint:
        expected.add("publication_fingerprint")
    if set(parameters) != expected:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "publication parameters are invalid")
    # Validate the profile identifier before any revision parsing
    try:
        profile_id = validate_profile_id(parameters["profile_id"])
    except ValueError as error:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error)) from error
    # Require non-negative integer revision proofs
    revisions = []
    for field in ("expected_profile_revision", "expected_settings_revision"):
        value = parameters[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, f"{field} is invalid")
        revisions.append(value)
    digest = parameters["expected_semantic_profile_digest"]
    operation_id = parameters["update_operation_id"]
    # Require a digest-shaped profile proof
    if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "profile digest is invalid")
    # Bound the update operation identifier used for gate matching
    if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 128:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "update operation identifier is invalid")
    if include_fingerprint:
        value = parameters["publication_fingerprint"]
        if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "publication fingerprint is invalid")
    return PublicationRequest(profile_id, revisions[0], digest, revisions[1], operation_id)


def _call_error(error: Exception) -> ApplicationCallError:
    """Map a publication exception onto the matching bridge error call."""
    if isinstance(error, ModPublicationError):
        # Translate publication codes into bridge codes and retry policy
        code = {
            "REVISION_CONFLICT": ErrorCode.REVISION_CONFLICT,
            "PUBLICATION_PREVIEW_STALE": ErrorCode.PUBLICATION_PREVIEW_STALE,
            "CACHE_VERIFICATION_FAILED": ErrorCode.CACHE_VERIFICATION_FAILED,
            "KEY_COLLISION": ErrorCode.PUBLICATION_FAILED,
            "PATH_INVALID": ErrorCode.PATH_INVALID,
            "RECOVERY_REQUIRED": ErrorCode.RECOVERY_REQUIRED,
        }.get(error.code, ErrorCode.PUBLICATION_FAILED)
        return ApplicationCallError(code, str(error), retryable=code == ErrorCode.REVISION_CONFLICT)
    return ApplicationCallError(ErrorCode.INTERNAL_FAILURE, "Publication processing failed.")
