"""Previewable copy-only legacy import workflow."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from ..domain.migrations import ConvertedProfile, convert_legacy_profile, migration_fingerprint
from ..repositories.json_store import VersionedJsonRepository
from ..repositories.legacy_backup_index import (
    LegacyBackupIndex, LegacyBackupIndexRepository,
)
from ..repositories.legacy_source import LegacyInventory, inspect_legacy_root
from ..repositories.migrations import MigrationStorage
from ..repositories.migration_journal import MigrationJournalRepository
from ..repositories.migration_publication import MigrationPublication
from ..repositories.paths import PortablePaths
from ..repositories.profiles import ProfileRepository
from .operations.context import OperationContext
from .profiles import ProfileService
from .migration_targets import build_publication_targets
from .settings import SettingsService
from .migration_preview import (
    MigrationConflictError,
    MigrationValidationError,
    SourceChangedError,
    destination_conflicts,
    destination_digest,
    selected_items,
    settings_proposal,
)


# Callback receiving migration phase names for progress reporting
MigrationHook = Callable[[str], None]


@dataclass(frozen=True)
class MigrationPreviewState:
    """Frozen review state binding a preview identifier to its proof."""
    preview_id: str
    fingerprint: str
    inventory: LegacyInventory
    destination_digest: str
    settings_revision: int | None
    settings_updates: dict[str, str]
    settings_selectable: bool
    profiles: tuple[ConvertedProfile, ...]
    backup_index: LegacyBackupIndex | None
    backup_index_revision: int | None
    backup_index_digest: str | None
    backup_selectable: bool


class MigrationService:
    """Coordinate legacy discovery, preview, and reviewed copy-only import."""

    def __init__(
        self, paths: PortablePaths, settings: SettingsService,
        settings_repository: VersionedJsonRepository, profiles: ProfileService,
        profile_repository: ProfileRepository, storage: MigrationStorage,
        hook: MigrationHook | None = None, publisher: MigrationPublication | None = None,
        backup_index_repository: LegacyBackupIndexRepository | None = None,
    ) -> None:
        """Store collaborators and default the publisher and backup index."""
        self._paths = paths
        self._settings = settings
        self._settings_repository = settings_repository
        self._profiles = profiles
        self._profile_repository = profile_repository
        self._storage = storage
        self._backup_indexes = backup_index_repository or LegacyBackupIndexRepository(
            paths.migrations / "legacy-backup-index.json",
        )
        self._hook = hook
        self._publisher = publisher or MigrationPublication(
            paths.root, storage,
            MigrationJournalRepository(paths.migrations / "publication-journals"),
            hook,
        )
        # Serialize selection and preview state for concurrent bridge calls
        self._lock = RLock()
        self._selections: dict[str, LegacyInventory] = {}
        self._previews: dict[str, MigrationPreviewState] = {}

    def select_root(self, root: object) -> dict[str, Any]:
        """Inspect the selected legacy root and register a one-shot selection."""
        # Inspect the root now so every later step reuses one consistent inventory
        inventory = inspect_legacy_root(root, self._paths.root, self._paths.data)
        selection_id = uuid.uuid4().hex
        with self._lock:
            # Replace prior selections so only the newest root stays active
            self._selections = {selection_id: inventory}
            self._previews.clear()
        return {
            "selection_id": selection_id,
            "source_label": inventory.source_label,
            "profile_count": len(inventory.profiles),
            "backup_count": inventory.backup_count,
            "backup_size": inventory.backup_size,
            "backup_policy": "EXTERNAL_REFERENCE",
        }

    def preview(self, selection_id: object) -> dict[str, Any]:
        """Build the reviewed migration plan and freeze it under a fingerprint."""
        # Require the one-shot selection recorded by discovery
        inventory = self._selection(selection_id)
        # Load the current destination so conflicts are classified against it
        current_settings = self._settings.load()
        current_profiles = self._profiles.list()
        dayz_root = Path(current_settings.dayz_root) if current_settings.dayz_root else inventory.root
        # Convert legacy profiles with the effective DayZ root
        converted = tuple(
            convert_legacy_profile(relative, raw, dayz_root, dict(inventory.workshop_ids))
            for relative, raw in inventory.profiles
        )
        # Flag profiles that collide with the current destination
        converted = destination_conflicts(converted, {item.values.profile_id for item in current_profiles})
        # Propose settings changes and classify conflicts and warnings
        settings_updates, settings_conflicts, settings_warnings = settings_proposal(
            inventory, current_settings,
        )
        # Compare reviewed backup history with the stored external index
        current_index = self._backup_indexes.load_optional()
        candidate_index = (
            self._backup_indexes.from_inventory(inventory) if inventory.backups else None
        )
        backup_changed = candidate_index is not None and (
            current_index is None
            or current_index.source_root_identity != candidate_index.source_root_identity
            or current_index.inventory_digest != candidate_index.inventory_digest
        )
        destination_proof = destination_digest(current_settings, current_profiles)
        public = {
            "source_label": inventory.source_label,
            "source_digest": inventory.source_digest,
            "inventory": [item.to_dict() for item in inventory.files],
            "profiles": [item.to_dict() for item in converted],
            "settings": {
                "item_id": "settings:dayz-installation",
                "selectable": bool(settings_updates) and not settings_conflicts,
                "fields": [{"role": key, "action": "SET"} for key in settings_updates],
                "warnings": list(settings_warnings),
                "conflicts": [item.to_dict() for item in settings_conflicts],
            },
            "ignored": _ignored_items(inventory),
            "backup_inventory": {
                "item_id": "backups:external-index",
                "count": inventory.backup_count,
                "size": inventory.backup_size,
                "status": "EXTERNAL_REFERENCE",
                "selectable": backup_changed,
                "action": "REPLACE" if current_index is not None and backup_changed else (
                    "CREATE" if backup_changed else "UNCHANGED"
                ),
                "entries": [item.to_dict() for item in inventory.backups],
                "warnings": (["The reviewed external index will replace a different current index."]
                             if current_index is not None and backup_changed else []),
                "conflicts": [],
                "copied": False,
            },
        }
        # Fingerprint the review so apply can detect destination drift
        fingerprint = migration_fingerprint(public)
        preview_id = uuid.uuid4().hex
        state = MigrationPreviewState(
            preview_id, fingerprint, inventory, destination_proof,
            current_settings.revision, settings_updates,
            public["settings"]["selectable"] is True, converted, candidate_index,
            current_index.revision if current_index else None,
            current_index.digest if current_index else None,
            backup_changed,
        )
        with self._lock:
            # Retain only the newest preview as the valid apply proof
            self._previews = {preview_id: state}
        return {"preview_id": preview_id, "preview_fingerprint": fingerprint, **public}

    def apply(
        self, preview_id: object, preview_fingerprint: object,
        selected_values: object, context: OperationContext,
    ) -> dict[str, Any]:
        """Re-verify the reviewed preview and publish the selected items."""
        # Require the exact preview proof issued at review time
        state = self._preview_state(preview_id, preview_fingerprint)
        # Validate the operator selection against the frozen preview
        selected = selected_items(selected_values)
        selectable = {
            item.item_id for item in state.profiles if item.selectable
        } | ({"settings:dayz-installation"} if state.settings_selectable else set())
        if state.backup_selectable:
            selectable.add("backups:external-index")
        if not selected or not selected.issubset(selectable):
            raise MigrationValidationError("Migration selection contains a blocked or unknown item.")
        # Re-inspect source and destination so a stale preview cannot publish
        context.checkpoint("DISCOVER", 5)
        refreshed = inspect_legacy_root(
            str(state.inventory.root), self._paths.root, self._paths.data,
        )
        if refreshed.source_digest != state.inventory.source_digest:
            raise SourceChangedError("Legacy source changed after preview.")
        if destination_digest(self._settings.load(), self._profiles.list()) != state.destination_digest:
            raise MigrationConflictError("Migration destination changed after preview.")
        self._require_index_proof(state)
        # Stage the import copy beside publication so partial work stays contained
        stage = self._storage.create_stage(context.operation_id)
        try:
            # Record the unpublished plan so recovery can finish or discard the stage
            self._storage.write_plan(stage, {
                "schema_version": 1, "kind": "SOURCE_COPY_NOT_PUBLISHED",
                "migration_id": context.operation_id,
                "source_digest": refreshed.source_digest,
                "publication_started": False,
            })
            # Copy only the profiles the operator selected
            profile_sources = tuple(
                relative for relative, _raw in refreshed.profiles
                if f"profile:{relative}" in selected
            )
            self._storage.copy_source(refreshed.root, profile_sources, stage)
            context.checkpoint("COPY_SOURCE", 25)
            self._call_hook("COPY_SOURCE")
            # Verify the source digest again after the copy so edits abort the import
            after_copy = inspect_legacy_root(
                str(state.inventory.root), self._paths.root, self._paths.data,
            )
            if after_copy.source_digest != state.inventory.source_digest:
                raise SourceChangedError("Legacy source changed while it was copied.")
            if state.backup_index is not None:
                refreshed_index = self._backup_indexes.from_inventory(after_copy)
                if (
                    refreshed_index.source_root_identity != state.backup_index.source_root_identity
                    or refreshed_index.inventory_digest != state.backup_index.inventory_digest
                ):
                    raise SourceChangedError("Legacy backup history changed while it was verified.")
            chosen_profiles = tuple(item for item in state.profiles if item.item_id in selected)
            context.checkpoint("CONVERT", 45)
            self._call_hook("CONVERT")
            for item in chosen_profiles:
                if item.profile is None:
                    raise MigrationValidationError("Selected profile conversion is unavailable.")
            context.checkpoint("VERIFY", 60)
            self._call_hook("VERIFY")
            if destination_digest(self._settings.load(), self._profiles.list()) != state.destination_digest:
                raise MigrationConflictError("Migration destination changed before publication.")
            self._require_index_proof(state)
            # Drop the staging copy and commit through the journaled publisher
            self._storage.cleanup(stage)
            migration_id = uuid.uuid4().hex
            published, targets = build_publication_targets(
                migration_id, state, selected, chosen_profiles, refreshed, selectable,
                settings=self._settings,
                settings_repository=self._settings_repository,
                profile_repository=self._profile_repository,
                backup_indexes=self._backup_indexes,
                storage=self._storage,
            )
            # Publish the reviewed targets and record the publication journal
            self._publisher.publish(
                migration_id, refreshed.source_digest, state.fingerprint, targets,
            )
            context.checkpoint("PUBLISH", 90)
            context.checkpoint("REPORT", 100)
            return {"migration_id": migration_id, "published": published, "backup_policy": "EXTERNAL_REFERENCE"}
        except Exception:
            # Never leave a staging copy behind when publication aborts
            if stage.exists():
                self._storage.cleanup(stage)
            raise

    def inspect_recovery(self) -> dict[str, object]:
        """Return the recovery state of the migration publication journal."""
        return self._publisher.inspect_recovery()

    def _require_index_proof(self, state: MigrationPreviewState) -> None:
        """Reject the operation when the stored backup index moved since review."""
        current = self._backup_indexes.load_optional()
        revision = current.revision if current else None
        digest = current.digest if current else None
        if revision != state.backup_index_revision or digest != state.backup_index_digest:
            raise MigrationConflictError("Legacy backup index changed after preview.")

    def _selection(self, value: object) -> LegacyInventory:
        """Return the live inventory behind a selection or fail as conflict."""
        if not isinstance(value, str):
            raise MigrationValidationError("Legacy selection is invalid.")
        with self._lock:
            inventory = self._selections.get(value)
        if inventory is None:
            # Selections are single-use; a missing identifier means discovery restarted
            raise MigrationConflictError("Legacy selection expired. Choose the folder again.")
        return inventory

    def _preview_state(self, preview_id: object, fingerprint: object) -> MigrationPreviewState:
        """Return the frozen preview behind its identifier and fingerprint."""
        if not isinstance(preview_id, str) or not isinstance(fingerprint, str):
            raise MigrationValidationError("Migration preview proof is invalid.")
        with self._lock:
            state = self._previews.get(preview_id)
        if state is None or state.fingerprint != fingerprint:
            # The fingerprint must match so a stale review cannot be applied
            raise MigrationConflictError("Migration preview is stale or invalid.")
        return state

    def _call_hook(self, phase: str) -> None:
        """Notify the optional progress hook about a migration phase."""
        if self._hook is not None:
            self._hook(phase)


def _ignored_items(inventory: LegacyInventory) -> list[str]:
    """List the legacy items that migration deliberately does not import."""
    # State the skipped legacy items up front for the review document
    ignored = ["Legacy UI selection state is not imported."]
    if inventory.has_legacy_settings:
        ignored.append("Legacy Steam authentication settings are not imported.")
    # Surface one entry per unsupported ignored source item
    ignored.extend("Unsupported source item was ignored." for _item in inventory.ignored)
    return ignored
