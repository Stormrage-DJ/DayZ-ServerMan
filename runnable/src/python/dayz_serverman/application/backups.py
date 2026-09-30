"""Manual verified ZIP-backup use cases."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

from ..domain.models import RevisionConflict
from ..domain.backups import entry_path_key
from ..domain.backups import restore_compatibility
from ..domain.backup_inventory import MISSION_INVENTORY, mission_payload_prefix
from ..domain.profiles import ProfileRecord
from ..repositories.backups import BackupSource, BackupStorage, BackupStorageError
from .profiles import ProfileService
from .mission_configuration import MissionPathError, resolve_profile_mission
from .settings import SettingsService


class BackupService:
    """Create verified ZIP backups and report per-profile backup history."""

    def __init__(
        self,
        profiles: ProfileService,
        settings: SettingsService,
        storage: BackupStorage,
        *,
        clock: Callable[[], datetime] | None = None,
        identifier: Callable[[], str] | None = None,
        local_timezone: tzinfo | None = None,
    ) -> None:
        """Bind profile, settings, storage, and the injectable clock and timezone."""
        self._profiles = profiles
        self._settings = settings
        self._storage = storage
        # Fall back to the current UTC time when no clock is injected
        self._clock = clock or (lambda: datetime.now(UTC))
        self._local_timezone = local_timezone

    def history(self, profile_id: object) -> dict[str, Any]:
        """Return the stored history for one profile with its revision context."""
        # Collect the profile, settings, and storage root that scope the query
        profile = self._profiles.read(profile_id)
        settings = self._settings.load()
        root = self._settings.backup_root(settings)
        # Load the verified history under that root
        history = self._storage.history(root, profile.values.profile_id)
        # Report the destination kind so callers can show where backups live
        return {
            "profile_id": profile.values.profile_id,
            "profile_revision": profile.revision,
            "semantic_profile_digest": profile.semantic_digest,
            "runtime_profile": profile.values.runtime_profile,
            "settings_revision": settings.revision,
            "destination_kind": "custom" if settings.custom_backup_root else "default",
            **history,
        }

    def create(
        self,
        profile_id: object,
        expected_profile_revision: object,
        expected_settings_revision: object,
        checkpoint: Callable[[str, int], None],
    ) -> dict[str, Any]:
        """Create a verified backup for one profile and return the manifest summary."""
        # Load the profile and settings with the revision guards the caller holds
        profile = self._profiles.read(profile_id)
        settings = self._settings.load()
        _require_revision(expected_profile_revision, profile.revision, "profile")
        if settings.revision is None:
            raise BackupStorageError("BACKUP_CONTEXT_INVALID", "Manager settings are not configured.")
        _require_revision(expected_settings_revision, settings.revision, "settings")
        if settings.dayz_root is None:
            raise BackupStorageError("BACKUP_CONTEXT_INVALID", "The DayZ root is not configured.")
        # Refuse a backup that would silently omit the runtime profile directory
        if profile.values.runtime_profile is None:
            raise BackupStorageError(
                "RUNTIME_PROFILE_UNRESOLVED",
                "Set a runtime profile directory in Profiles before creating a backup.",
            )
        # Build the ordered payload inventory from the current install
        sources = self._inventory(Path(settings.dayz_root), profile)
        # Re-read the context so a concurrent edit cannot be captured silently
        current_profile = self._profiles.read(profile.values.profile_id)
        current_settings = self._settings.load()
        if (
            current_profile.revision != profile.revision
            or current_profile.semantic_digest != profile.semantic_digest
            or current_settings.revision != settings.revision
        ):
            raise RevisionConflict("backup context changed while the inventory was prepared")
        # Use local time for the human-readable id and UTC for the stored timestamp
        instant = self._clock()
        created = instant.astimezone(UTC)
        local = instant.astimezone(self._local_timezone)
        backup_id = f"{profile.values.profile_id}_{local.strftime('%Y-%m-%d_%H-%M-%S')}"
        # Write the archive and manifest through storage with checkpoints
        manifest = self._storage.create(
            self._settings.backup_root(settings),
            backup_id,
            profile.values.profile_id,
            profile.revision,
            settings.revision,
            created.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            profile.semantic_digest,
            profile.values.runtime_profile,
            sources,
            checkpoint,
        )
        # Judge restore compatibility before reporting the new backup
        compatibility, reason = restore_compatibility(manifest)
        return {
            "backup_id": manifest.backup_id,
            "profile_id": manifest.profile_id,
            "created_at": manifest.created_at,
            "entry_count": len(manifest.entries),
            "total_size": sum(item.size for item in manifest.entries),
            "manifest_digest": manifest.manifest_digest,
            "semantic_profile_digest": manifest.semantic_profile_digest,
            "runtime_profile": manifest.runtime_profile,
            "runtime_file_count": sum(
                entry.path.startswith("runtime-profile/") for entry in manifest.entries
            ),
            "restore_compatibility": compatibility,
            "restore_compatibility_reason": reason,
            "status": "USABLE",
        }

    def _inventory(self, dayz_root: Path, profile: ProfileRecord) -> tuple[BackupSource, ...]:
        """Collect the ordered, duplicate-free backup sources for one profile."""
        runtime_profile = profile.values.runtime_profile
        if runtime_profile is None:
            raise BackupStorageError("RUNTIME_PROFILE_UNRESOLVED", "Runtime profile is not configured.")
        # Capture the server configuration and the complete resolved mission tree.
        sources = (self._storage.source(dayz_root, profile.values.server_config),)
        try:
            mission_root, _mission = resolve_profile_mission(dayz_root.resolve(strict=True), profile)
        except MissionPathError as error:
            raise BackupStorageError("MISSION_UNRESOLVED", str(error)) from error
        mission_sources = self._storage.directory_sources(
            dayz_root, mission_root, mission_payload_prefix(mission_root),
        )
        if not mission_sources:
            raise BackupStorageError("MISSION_EMPTY", "The selected mission directory contains no files.")
        sources += mission_sources
        sources += self._storage.runtime_sources(dayz_root, runtime_profile)
        # Sort by entry path so the manifest stays deterministic
        ordered = tuple(sorted(sources, key=lambda item: entry_path_key(item.entry_path)))
        # Reject duplicate targets that differ only by letter case
        folded = [item.entry_path.casefold() for item in ordered]
        if len(folded) != len(set(folded)):
            raise BackupStorageError("BACKUP_SOURCE_INVALID", "The backup inventory contains duplicate targets.")
        return ordered


def _require_revision(value: object, current: int, label: str) -> None:
    """Validate an expected revision value against the current revision."""
    # Reject non-integer or negative revisions as invalid requests
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BackupStorageError("INVALID_REQUEST", f"Expected {label} revision is invalid.")
    # A mismatch means the caller's context is stale
    if value != current:
        raise RevisionConflict(f"expected {label} revision {value}, current revision is {current}")
