"""Precondition checks of the server lifecycle that map failures onto lifecycle codes."""

from __future__ import annotations

from ..domain.lifecycle import LifecycleFailure, LifecycleSnapshot, ServerState
from ..domain.models import ManagerSettings, RecordUnavailable
from ..domain.profiles import ProfileRecord, ProfileValidationError
from ..repositories.profiles import ProfileNotFound, ProfileStorageError
from .profiles import ProfileService
from .settings import SettingsService, SettingsValidationError


def require_settings(settings: SettingsService, expected_revision: int) -> ManagerSettings:
    """Load settings, enforce the revision guard, and require an executable."""
    # Map unreadable settings to a recovery-required failure
    try:
        loaded = settings.load()
    except (RecordUnavailable, SettingsValidationError) as error:
        raise LifecycleFailure(
            "RECOVERY_REQUIRED",
            "Manager settings require recovery.",
            recovery_required=True,
        ) from error
    # Refuse a stale revision and confirm the executable is configured
    if loaded.revision != expected_revision:
        raise LifecycleFailure("REVISION_CONFLICT", "The settings revision changed.")
    configured_executable(loaded)
    return loaded


def require_profile(profiles: ProfileService, profile_id: str, expected_revision: int) -> ProfileRecord:
    """Load the profile, enforce its revision, and map storage failures."""
    # Map missing, invalid, and stored profile failures onto lifecycle codes
    try:
        profile = profiles.read(profile_id)
    except ProfileNotFound as error:
        raise LifecycleFailure("NOT_FOUND", "The selected profile was not found.") from error
    except ProfileValidationError as error:
        raise LifecycleFailure("INVALID_REQUEST", str(error)) from error
    except ProfileStorageError as error:
        raise LifecycleFailure(
            "RECOVERY_REQUIRED" if error.recovery_required else "STORAGE_FAILURE",
            "The selected profile is unavailable.",
            recovery_required=error.recovery_required,
        ) from error
    # Refuse a stale revision so the launch uses the reviewed profile
    if profile.revision != expected_revision:
        raise LifecycleFailure("REVISION_CONFLICT", "The profile revision changed.")
    return profile


def configured_executable(settings: ManagerSettings) -> str:
    """Return the configured executable or fail with a path error."""
    # Both the root and the executable must be configured before launch
    if settings.dayz_root is None or settings.dayz_executable is None:
        raise LifecycleFailure("PATH_INVALID", "DayZ paths are not configured.")
    return settings.dayz_executable


def require_state(
    actual: LifecycleSnapshot,
    required: ServerState,
    action: str,
) -> None:
    """Raise a coded failure unless the snapshot already matches the required state."""
    if actual.state == required:
        return
    # Distinguish external, unknown, and control conflicts for the caller
    if actual.state == ServerState.RUNNING_EXTERNAL:
        code = "EXTERNAL_PROCESS"
    elif actual.state in {ServerState.UNKNOWN, ServerState.AMBIGUOUS}:
        code = "PROCESS_STATE_UNKNOWN"
    else:
        code = "CONTROL_CONFLICT"
    raise LifecycleFailure(code, f"Cannot {action} while server state is {actual.state.value}.")
