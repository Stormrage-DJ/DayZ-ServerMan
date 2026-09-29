"""Bridge-facing profile queries and exclusive-lane mutations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.models import RecordUnavailable, RevisionConflict
from ..domain.profiles import ProfileInput, ProfileValidationError, validate_profile_id
from ..repositories.profiles import ProfileNotFound, ProfileStorageError
from .operations.manager import OperationManager
from .operations.models import OperationFailure, QueueUnavailable
from .profiles import ProfileService
from .settings import SettingsValidationError


def _exact_fields(parameters: Mapping[str, Any], allowed: set[str]) -> None:
    """Reject calls with unknown or missing profile parameters."""
    unknown = set(parameters).difference(allowed)
    missing = allowed.difference(parameters)
    if unknown or missing:
        raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "profile parameters are invalid")


class ProfileCoordinator:
    """Serve profile queries and queue profile mutations."""

    def __init__(self, profiles: ProfileService, operations: OperationManager) -> None:
        """Store the profile service and the shared operation manager."""
        self._profiles = profiles
        self._operations = operations

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the bridge handler table for profile calls."""
        return {
            "list_profiles": self.list_profiles,
            "read_profile": self.read_profile,
            "preview_profile_command": self.preview_profile_command,
            "save_profile": self.save_profile,
            "delete_profile": self.delete_profile,
        }

    def list_profiles(self, parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Return every stored profile as bridge documents."""
        _exact_fields(parameters, set())
        # Serialize stored records for the operator interface
        try:
            return [record.to_dict() for record in self._profiles.list()]
        except ProfileStorageError as error:
            raise _query_storage_error(error) from error

    def read_profile(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Return one stored profile as a bridge document."""
        _exact_fields(parameters, {"profile_id"})
        # Translate unknown or unreadable profiles into bridge errors
        try:
            return self._profiles.read(parameters["profile_id"]).to_dict()
        except (ProfileValidationError, ProfileNotFound, ProfileStorageError) as error:
            raise _query_error(error) from error

    def preview_profile_command(self, parameters: Mapping[str, Any]) -> dict[str, object]:
        """Return the launch command preview for one profile."""
        _exact_fields(parameters, {"profile_id"})
        try:
            return self._profiles.preview_launch(parameters["profile_id"]).to_dict()
        except (ProfileValidationError, ProfileNotFound, ProfileStorageError) as error:
            raise _query_error(error) from error
        # Settings or record failures require recovery messaging
        except (RecordUnavailable, SettingsValidationError) as error:
            raise ApplicationCallError(
                ErrorCode.RECOVERY_REQUIRED,
                "Manager settings require recovery.",
            ) from error

    def save_profile(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue a create-or-replace profile save."""
        _exact_fields(parameters, {"profile", "expected_revision"})
        # Validate the revision proof and parse the profile payload
        expected = _expected_revision(parameters["expected_revision"], allow_none=True)
        try:
            profile = ProfileInput.parse(parameters["profile"])
        except ProfileValidationError as error:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error)) from error
        return self._submit(
            "SAVE_PROFILE",
            lambda: self._profiles.save(profile, expected).to_dict(),
        )

    def delete_profile(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Queue a delete for one profile under an exact revision."""
        _exact_fields(parameters, {"profile_id", "expected_revision"})
        # Require a concrete revision so deletes cannot race
        expected = _expected_revision(parameters["expected_revision"], allow_none=False)
        try:
            profile_id = validate_profile_id(parameters["profile_id"])
        except ProfileValidationError as error:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error)) from error
        return self._submit(
            "DELETE_PROFILE",
            lambda: {"profile_id": self._profiles.delete(profile_id, expected)},
        )

    def _submit(self, kind: str, action: Callable[[], dict[str, Any]]) -> dict[str, str]:
        """Queue one profile mutation and describe the queued operation."""
        def work(_context: object) -> dict[str, Any]:
            """Run the mutation and map failures for the operation lane."""
            # Translate each failure family into a typed operation failure
            try:
                return action()
            except RevisionConflict as error:
                raise OperationFailure("REVISION_CONFLICT", str(error)) from error
            except ProfileNotFound as error:
                raise OperationFailure("NOT_FOUND", str(error)) from error
            except ProfileValidationError as error:
                raise OperationFailure("INVALID_REQUEST", str(error)) from error
            except ProfileStorageError as error:
                code = "RECOVERY_REQUIRED" if error.recovery_required else "STORAGE_FAILURE"
                raise OperationFailure(
                    code,
                    str(error),
                    recovery_required=error.recovery_required,
                ) from error
            except OSError as error:
                raise OperationFailure("STORAGE_FAILURE", "Profile storage failed.") from error

        # Queue the mutation and surface a busy lane immediately
        try:
            record = self._operations.submit(kind, work)
        except QueueUnavailable as error:
            raise ApplicationCallError(
                ErrorCode.MUTATION_CONFLICT,
                str(error),
                retryable=True,
            ) from error
        return {"operation_id": record.operation_id, "state": record.state.value}


def _expected_revision(value: object, *, allow_none: bool) -> int | None:
    """Validate an optional expected revision proof."""
    # A missing revision is accepted only for create-style saves
    if value is None and allow_none:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ApplicationCallError(
            ErrorCode.INVALID_REQUEST,
            "expected_revision is invalid",
        )
    return value


def _query_error(error: Exception) -> ApplicationCallError:
    """Translate a profile query failure into its bridge call."""
    if isinstance(error, ProfileNotFound):
        return ApplicationCallError(ErrorCode.NOT_FOUND, str(error))
    if isinstance(error, ProfileStorageError):
        return _query_storage_error(error)
    return ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error))


def _query_storage_error(error: ProfileStorageError) -> ApplicationCallError:
    """Map profile storage failures onto recovery or storage codes."""
    code = ErrorCode.RECOVERY_REQUIRED if error.recovery_required else ErrorCode.STORAGE_FAILURE
    return ApplicationCallError(code, str(error))
