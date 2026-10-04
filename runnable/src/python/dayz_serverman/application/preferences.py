"""Portable operator-interface preferences."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.models import RecordState, RecordUnavailable, RevisionConflict
from ..domain.profiles import ProfileValidationError, validate_profile_id
from ..repositories.json_store import VersionedJsonRepository
from ..repositories.profiles import ProfileNotFound, ProfileStorageError
from .profiles import ProfileService


class PreferenceCoordinator:
    """Load and atomically store non-operational UI choices."""

    def __init__(self, repository: VersionedJsonRepository, profiles: ProfileService) -> None:
        """Store the preference repository and the profile validator."""
        self._repository = repository
        self._profiles = profiles
        # Callback that tells the update-check scheduler about a changed switch
        self._automatic_checks_listener: Callable[[], None] | None = None

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return the bridge handler table for UI preference calls."""
        return {
            "get_ui_preferences": self.get_ui_preferences,
            "save_selected_profile": self.save_selected_profile,
            "save_backup_after_stop": self.save_backup_after_stop,
            "save_automatic_update_checks": self.save_automatic_update_checks,
        }

    def get_ui_preferences(self, parameters: Mapping[str, Any]) -> dict[str, object]:
        """Return sanitized UI preferences and their storage status."""
        self._exact_fields(parameters, set())
        inspection = self._repository.inspect()
        # Report the stored state without fields when the document is not valid
        automatic = self._automatic_checks(inspection)
        if inspection.state != RecordState.VALID or inspection.document is None:
            return self._view(None, (), inspection.state.value, automatic)
        fields = inspection.document.fields
        value = fields.get("selected_profile_id")
        raw_backups = fields.get("backup_after_stop_profiles", [])
        # Reject stored field shapes that do not match the expected types
        if value is not None and not isinstance(value, str):
            return self._view(None, (), "INVALID", automatic)
        if (not isinstance(raw_backups, list)
                or any(not isinstance(item, str) for item in raw_backups)
                or len(raw_backups) != len(set(raw_backups))):
            return self._view(None, (), "INVALID", automatic)
        selected = None
        backups: list[str] = []
        # Re-validate every referenced profile so stale identifiers surface
        try:
            if value is not None:
                selected = self._require_profile(value)
            backups = [self._require_profile(item) for item in raw_backups]
        except (ProfileValidationError, ProfileNotFound, ProfileStorageError):
            return self._view(selected, backups, "STALE", automatic)
        return self._view(selected, backups, "VALID", automatic)

    def save_selected_profile(self, parameters: Mapping[str, Any]) -> dict[str, str]:
        """Store the selected profile after proving it exists."""
        self._exact_fields(parameters, {"profile_id"})
        profile_id = self._validated_profile(parameters.get("profile_id"))
        # Write through the shared path so revision conflicts surface
        self._save({"selected_profile_id": profile_id})
        return {"selected_profile_id": profile_id}

    def save_backup_after_stop(self, parameters: Mapping[str, Any]) -> dict[str, object]:
        """Enable or disable automatic backups for one profile."""
        self._exact_fields(parameters, {"profile_id", "enabled"})
        enabled = parameters.get("enabled")
        # Require an explicit boolean flag for the preference change
        if not isinstance(enabled, bool):
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "enabled is invalid")
        profile_id = self._validated_profile(parameters.get("profile_id"))
        # Read the writable document before editing its stored list
        inspection = self._writable_inspection()
        fields = dict(inspection.document.fields) if inspection.document is not None else {}
        raw = fields.get("backup_after_stop_profiles", [])
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise ApplicationCallError(
                ErrorCode.RECOVERY_REQUIRED, "UI preference storage requires recovery.",
            )
        # Add or remove the profile so the stored set stays unique
        profiles = set(raw)
        if enabled:
            profiles.add(profile_id)
        else:
            profiles.discard(profile_id)
        # Save the updated list under the inspected revision
        self._save({"backup_after_stop_profiles": sorted(profiles)}, inspection=inspection)
        return {"profile_id": profile_id, "backup_after_stop": enabled}

    def save_automatic_update_checks(self, parameters: Mapping[str, Any]) -> dict[str, bool]:
        """Switch the automatic Steam update checks on or off."""
        self._exact_fields(parameters, {"enabled"})
        enabled = parameters.get("enabled")
        # Require an explicit boolean flag for the preference change
        if not isinstance(enabled, bool):
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "enabled is invalid")
        self._save({"automatic_update_checks": enabled})
        # Let the scheduler evaluate the new switch without waiting for its timer
        if self._automatic_checks_listener is not None:
            self._automatic_checks_listener()
        return {"automatic_update_checks": enabled}

    def automatic_update_checks(self) -> bool:
        """Return the effective switch of the automatic Steam update checks."""
        return self._automatic_checks(self._repository.inspect())

    def on_automatic_update_checks_saved(self, listener: Callable[[], None]) -> None:
        """Register the callback that runs after the switch was stored."""
        self._automatic_checks_listener = listener

    def backup_after_stop(self, profile_id: str) -> bool:
        """Return a valid saved preference, failing closed when storage is stale."""
        view = self.get_ui_preferences({})
        profiles = view.get("backup_after_stop_profiles", [])
        # Fail closed when storage is stale or the profile is not enabled
        return view.get("storage_status") == "VALID" and profile_id in profiles

    def delete_profile(self, profile_id: str) -> None:
        """Remove every preference that refers to a deleted profile."""
        inspection = self._writable_inspection()
        if inspection.document is None:
            return
        fields = dict(inspection.document.fields)
        changed = False
        if fields.get("selected_profile_id") == profile_id:
            fields["selected_profile_id"] = None
            changed = True
        raw = fields.get("backup_after_stop_profiles", [])
        if isinstance(raw, list) and profile_id in raw:
            fields["backup_after_stop_profiles"] = [item for item in raw if item != profile_id]
            changed = True
        if changed:
            expected = inspection.document.revision
            self._repository.save(fields, expected)

    def _save(self, changes: dict[str, object], *, inspection=None) -> None:
        """Write preference changes under the inspected revision."""
        inspection = inspection or self._writable_inspection()
        # Merge the requested changes over the current stored fields
        fields = dict(inspection.document.fields) if inspection.document is not None else {}
        fields.update(changes)
        expected = inspection.document.revision if inspection.document is not None else None
        # Translate revision and storage failures into bridge errors
        try:
            self._repository.save(fields, expected)
        except RevisionConflict as error:
            raise ApplicationCallError(ErrorCode.REVISION_CONFLICT, str(error), retryable=True) from error
        except (OSError, RecordUnavailable, ValueError) as error:
            raise ApplicationCallError(ErrorCode.STORAGE_FAILURE, "UI preferences could not be stored.") from error

    def _writable_inspection(self):
        """Return the repository inspection when preferences may be written."""
        inspection = self._repository.inspect()
        # Missing or valid storage may be written; anything else needs recovery
        if inspection.state not in (RecordState.MISSING, RecordState.VALID):
            raise ApplicationCallError(
                ErrorCode.RECOVERY_REQUIRED,
                "UI preference storage requires recovery.",
            )
        return inspection

    def _validated_profile(self, value: object) -> str:
        """Validate a profile identifier or translate the failure."""
        try:
            return self._require_profile(value)
        except ProfileValidationError as error:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, str(error)) from error
        except ProfileNotFound as error:
            raise ApplicationCallError(ErrorCode.NOT_FOUND, str(error)) from error
        except ProfileStorageError as error:
            raise ApplicationCallError(ErrorCode.STORAGE_FAILURE, "Profile storage is unavailable.") from error

    def _require_profile(self, value: object) -> str:
        """Return the identifier after proving the profile can be read."""
        profile_id = validate_profile_id(value)
        # Reading the profile rejects unknown or unreadable identifiers
        self._profiles.read(profile_id)
        return profile_id

    @staticmethod
    def _automatic_checks(inspection) -> bool:
        """Derive the effective automatic-check switch from the stored record."""
        # No record yet: automatic checks are on by default
        if inspection.state == RecordState.MISSING:
            return True
        # An unreadable record switches automatic checks off
        if inspection.state != RecordState.VALID or inspection.document is None:
            return False
        # An absent key is on; a stored value counts only when it is a true boolean
        return inspection.document.fields.get("automatic_update_checks", True) is True

    @staticmethod
    def _view(
        selected: str | None, backups, status: str, automatic: bool,
    ) -> dict[str, object]:
        """Shape the preference view returned to the operator interface."""
        return {
            "selected_profile_id": selected,
            "backup_after_stop_profiles": sorted(backups),
            "storage_status": status,
            "automatic_update_checks": automatic,
        }

    @staticmethod
    def _exact_fields(parameters: Mapping[str, Any], allowed: set[str]) -> None:
        """Reject calls that do not match the allowed preference fields."""
        if set(parameters) != allowed:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "preference parameters are invalid")
