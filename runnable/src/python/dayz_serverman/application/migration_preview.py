"""Legacy migration destination proofs and conflict classification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain.migrations import ConvertedProfile, MigrationConflict, migration_fingerprint
from ..domain.models import ManagerSettings
from ..repositories.legacy_source import LegacyInventory
from .settings import SETTINGS_FIELDS


class MigrationValidationError(ValueError):
    """Raised when a migration request or selection is not valid."""
    pass


class MigrationConflictError(RuntimeError):
    """Raised when the migration destination or preview state conflicts."""
    pass


class SourceChangedError(MigrationConflictError):
    """Raised when the legacy source changes after it was reviewed."""
    pass


def settings_proposal(
    inventory: LegacyInventory, current: ManagerSettings,
) -> tuple[dict[str, str], tuple[MigrationConflict, ...], tuple[str, ...]]:
    """Derive manager-settings updates from the inspected legacy installation."""
    # The legacy root becomes the DayZ installation root
    proposed = {"dayz_root": str(inventory.root)}
    executable = inventory.root / "DayZServer_x64.exe"
    conflicts: list[MigrationConflict] = []
    # Accept the server executable only when the legacy root provides one
    if executable.is_file():
        proposed["dayz_executable"] = str(executable.resolve(strict=True))
    else:
        conflicts.append(MigrationConflict(
            "DAYZ_EXECUTABLE_MISSING", "settings:dayz-installation",
            "The selected legacy root does not contain DayZServer_x64.exe.",
        ))
    # Classify each proposal against the current settings
    updates: dict[str, str] = {}
    warnings: list[str] = []
    for field, value in proposed.items():
        existing = getattr(current, field)
        if existing is None:
            updates[field] = value
        elif existing.casefold() == value.casefold():
            warnings.append(f"Current {field} already matches the legacy installation.")
        else:
            conflicts.append(MigrationConflict(
                "DESTINATION_CONFLICT", "settings:dayz-installation",
                f"Current {field} is already configured differently.",
            ))
    return updates, tuple(conflicts), tuple(warnings)


def destination_conflicts(
    converted: tuple[ConvertedProfile, ...], existing_ids: set[str],
) -> tuple[ConvertedProfile, ...]:
    """Attach destination conflicts to converted legacy profiles."""
    # Count converted profile identifiers to detect duplicates
    counts: dict[str, int] = {}
    for item in converted:
        if item.profile:
            key = item.profile.profile_id.casefold()
            counts[key] = counts.get(key, 0) + 1
    # Flag profiles that collide with existing or sibling identifiers
    result: list[ConvertedProfile] = []
    folded_existing = {item.casefold() for item in existing_ids}
    for item in converted:
        conflicts = list(item.conflicts)
        if item.profile and item.profile.profile_id.casefold() in folded_existing:
            conflicts.append(MigrationConflict(
                "DESTINATION_CONFLICT", item.item_id,
                "A current profile already uses this identifier.",
            ))
        if item.profile and counts[item.profile.profile_id.casefold()] > 1:
            conflicts.append(MigrationConflict(
                "DUPLICATE_PROFILE_ID", item.item_id,
                "Multiple legacy profiles map to the same identifier.",
            ))
        result.append(ConvertedProfile(
            item.item_id, item.profile, item.warnings, tuple(conflicts),
        ))
    return tuple(result)


def destination_digest(settings: ManagerSettings, profiles: tuple[Any, ...]) -> str:
    """Fingerprint the current settings and profiles as the migration destination."""
    # Fold revisions and semantic digests into one comparable proof
    return migration_fingerprint({
        "settings": {
            "revision": settings.revision,
            **{field: getattr(settings, field) for field in SETTINGS_FIELDS},
        },
        "profiles": [
            {"profile_id": item.values.profile_id, "revision": item.revision,
             "semantic_digest": item.semantic_digest}
            for item in profiles
        ],
    })


def selected_items(value: object) -> set[str]:
    """Validate and normalize a reviewed migration selection."""
    # Reject selections that are not arrays of identifiers
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise MigrationValidationError("selected_items must be an array of identifiers.")
    # Reject duplicates so every item is applied at most once
    if len(value) != len(set(value)):
        raise MigrationValidationError("selected_items must not contain duplicates.")
    return set(value)
