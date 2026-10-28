"""Manual verified ZIP-backup use cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, tzinfo
from pathlib import Path, PureWindowsPath
from typing import Any

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.models import RevisionConflict
from ..domain.backups import entry_path_key
from ..domain.backups import restore_compatibility
from ..domain.backup_inventory import MISSION_INVENTORY, mission_payload_prefix
from ..domain.profiles import ProfileRecord
from ..repositories.backup_editor_records import EditorArchive, EditorRecordsInvalid, collect_editor_records
from ..repositories.backups import BackupSource, BackupStorage, BackupStorageError
from .profiles import ProfileService
from .mission_configuration import MissionPathError, resolve_profile_mission
from .settings import SettingsService


# Product Owner text (2026-10-16 16:47:54 in evidence 10.9), shown after the success line of a backup
# whose editor records were left out because they are not valid
EDITOR_RECORDS_OMITTED = ("The backup was created without the map editor records of this profile, because they "
                          "are not valid. A restore of this backup does not bring them back.")



def editor_records_notice(result: object) -> str | None:
    """Return the text that follows the success line when a backup left out corrupt editor records, else None.

    A `create` result holds the flag itself. A stop or restart with backup holds it in its `backup` result.
    """
    result = result if isinstance(result, Mapping) else {}
    backup = result.get("backup") if isinstance(result.get("backup"), Mapping) else {}
    omitted = result.get("editor_records_omitted") is True or backup.get("editor_records_omitted") is True
    return EDITOR_RECORDS_OMITTED if omitted else None


def retention_notice(result: object) -> str | None:
    """Return the text that names the older backups deleted by retention, or whose deletion failed, else None.

    A `create` result holds the lists itself. A stop or restart with backup holds them in its `backup` result.
    frontend/operation_results.js words the same notice.
    """
    result = result if isinstance(result, Mapping) else {}
    backup = result.get("backup") if isinstance(result.get("backup"), Mapping) else result
    pruned, failed = backup.get("pruned_backups"), backup.get("prune_failures")
    parts = []
    if isinstance(pruned, list) and pruned:
        parts.append(f"Deleted the older backups beyond the newest {backup.get('backups_to_keep')} of this "
                     f"profile: {', '.join(map(str, pruned))}.")
    if isinstance(failed, list) and failed:
        parts.append(f"These older backups could not be deleted: {', '.join(map(str, failed))}.")
    return " ".join(parts) or None


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
        # Editor records stay out of the backup until bind_editor gives the plan service, its locks and the area
        self._editor: tuple[Any, Any, Path] | None = None

    def bind_editor(self, plans: Any, locks: Any, area: Path) -> None:
        """Bind the mission map plan service, its association lock set and the editor area (10.9)."""
        self._editor = (plans, locks, area)

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

    def catalog(self) -> dict[str, Any]:
        """List retained archives without consulting the active profile catalog."""
        from ..repositories.backup_catalog import list_catalog
        settings = self._settings.load()
        return {"settings_revision": settings.revision,
                "destination_kind": "custom" if settings.custom_backup_root else "default",
                **list_catalog(self._settings.backup_root(settings))}

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
        from ..repositories.backup_reconstruction import directory_inventory
        from ..repositories.profile_restore_configuration import read_restore_configuration
        from ..domain.backup_reconstruction import reconstruction_metadata
        mission_root, _ = resolve_profile_mission(Path(settings.dayz_root).resolve(strict=True), profile)
        configuration = read_restore_configuration(sources_config_bytes(sources, profile))
        if configuration.mission_template.casefold() != PureWindowsPath(mission_root).name.casefold():
            raise BackupStorageError("MISSION_UNRESOLVED", "Configuration and selected mission disagree.")
        directories = tuple(sorted((*directory_inventory(Path(settings.dayz_root), mission_root, mission_payload_prefix(mission_root)),
                                    *directory_inventory(Path(settings.dayz_root), profile.values.runtime_profile, "runtime-profile")), key=entry_path_key))
        metadata = reconstruction_metadata(profile.values, mission_root, configuration, directories)
        # Re-read the context so a concurrent edit cannot be captured silently
        current_profile = self._profiles.read(profile.values.profile_id)
        current_settings = self._settings.load()
        if (
            current_profile.revision != profile.revision
            or current_profile.semantic_digest != profile.semantic_digest
            or current_settings.revision != settings.revision
        ):
            raise RevisionConflict("backup context changed while the inventory was prepared")
        editor, omitted = self._editor_records(Path(settings.dayz_root).resolve(strict=True), profile)
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
            reconstruction=metadata, directories=directories,
            validate_context=lambda: self._validate_capture(profile, settings, directories, mission_root),
            editor=editor,
        )
        # Keep only the newest verified backups of this profile, as the settings name
        retention = self._storage.prune(self._settings.backup_root(settings), profile.values.profile_id,
                                        settings.backups_to_keep, manifest.backup_id)
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
            # True when corrupt editor records were left out; the result then shows EDITOR_RECORDS_OMITTED
            "editor_records_omitted": omitted,
            # Older backups of the profile deleted by retention, and those whose deletion failed
            **retention,
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

    def _editor_records(self, root: Path, profile: ProfileRecord) -> tuple[EditorArchive | None, bool]:
        """Read the records of the active association under its lock; None keeps the backup at schema 3 (D18).

        The active association is that of the mission key that the profile resolves now. A profile in the
        path-change state has none, so its backup carries no editor record. A corrupt record or content
        does not refuse the backup: it is made without editor records, and the flag says so (Product Owner,
        evidence 10.9). A record that changed during the read still refuses.
        """
        if self._editor is None:
            return None, False
        from .mission_map_plans import MissionPathChanged, _runtime_key
        plans, locks, area = self._editor
        association = plans.active_association(profile.values.profile_id)
        with locks.hold(association["profile_id"], association["mission_key"]):
            try:
                if plans.load(profile.values.profile_id)["path_change"] is not None:
                    return None, False
            except MissionPathChanged:
                return None, False
            # The target keys are the mission folder and the runtime profile folder of the profile
            keys = (association["mission_key"], _runtime_key(root, profile))
            try:
                return collect_editor_records(area, association, keys), False
            except EditorRecordsInvalid:
                return None, True

    def _validate_capture(self, profile, settings, directories, mission_root):
        """Recheck identity and directory inventory after the complete payload capture."""
        from ..repositories.backup_reconstruction import directory_inventory
        current = self._profiles.read(profile.values.profile_id)
        if current != profile or self._settings.load() != settings:
            raise RevisionConflict("Backup context changed during capture.")
        captured = tuple(sorted((*directory_inventory(Path(settings.dayz_root), mission_root, mission_payload_prefix(mission_root)),
                                 *directory_inventory(Path(settings.dayz_root), profile.values.runtime_profile, "runtime-profile")), key=entry_path_key))
        if captured != directories:
            raise BackupStorageError("BACKUP_SOURCE_CHANGED", "Backup directories changed during capture.")


def sources_config_bytes(sources: tuple[BackupSource, ...], profile: ProfileRecord) -> bytes:
    """Read the selected configuration from its already validated source identity."""
    entry = "payload/" + "/".join(PureWindowsPath(profile.values.server_config).parts)
    return next(read_bytes_shared(source.source) for source in sources if source.entry_path == entry)


def _require_revision(value: object, current: int, label: str) -> None:
    """Validate an expected revision value against the current revision."""
    # Reject non-integer or negative revisions as invalid requests
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BackupStorageError("INVALID_REQUEST", f"Expected {label} revision is invalid.")
    # A mismatch means the caller's context is stale
    if value != current:
        raise RevisionConflict(f"expected {label} revision {value}, current revision is {current}")
