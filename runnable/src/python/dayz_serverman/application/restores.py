"""Restore preview and apply use cases."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import Any

from ..domain.backup_entries import RESTORE_PENDING_RUNTIME
from ..domain.backups import SHA256
from ..domain.models import RevisionConflict
from ..domain.restores import RestorePreview
from ..repositories.backups import BackupStorage, BackupStorageError
from ..repositories.restore_journal import RestoreJournalRepository
from ..repositories.restore_storage import RestoreStorage, RestoreStorageError
from .folder_writer_scope import WriterScope
from .lifecycle_ports import InstallationMutexPort, ServerFolderWriterPort
from .profiles import ProfileService
from .restore_editor import RestoreEditor
from .restore_editor_files import EditorRestore
# Recovery lives in its own module (10.3); its block reasons are re-exported for existing importers
from .restore_recovery import RECOVERY_NO_DAYZ_ROOT, RECOVERY_NOT_STOPPED, RestoreRecovery  # noqa: F401
# The backup and server-state checks live in their own module (10.3)
from .restore_validation import RestoreValidation
from .settings import SettingsService

# Operation kind of a restore apply; it owns every block that the restore recovery may lift
RESTORE_KIND = "RESTORE_BACKUP"


class RestoreService(RestoreRecovery, RestoreValidation):
    """Preview and apply backup restores through verified manifests."""

    def __init__(
        self,
        profiles: ProfileService,
        settings: SettingsService,
        backups: BackupStorage,
        storage: RestoreStorage,
        journals: RestoreJournalRepository,
        recovery_root: Path,
        lifecycle: Any,
        mutex: InstallationMutexPort,
        folder_writer: ServerFolderWriterPort | None = None,
        editor: RestoreEditor | None = None,
    ) -> None:
        """Store the collaborators used by restore preview and apply, the A13 writer side and the editor part."""
        self._profiles = profiles
        self._settings = settings
        self._backups = backups
        self._storage = storage
        self._journals = journals
        self._recovery_root = recovery_root
        self._lifecycle = lifecycle
        self._mutex = mutex
        self._folder_writer = folder_writer
        # The map editor records of a same-profile restore (D19, 10.10); without it a schema 4 archive is refused
        self._editor = editor

    def preview(self, profile_id: object, backup_id: object) -> dict[str, Any]:
        """Return a signed restore preview for the selected backup."""
        # Resolve the profile, settings, and DayZ root as one context
        profile, settings, dayz_root = self._context(profile_id)
        backup_root = self._settings.backup_root(settings)
        # Verify the backup manifest before locking or scanning targets
        self._validate_manifest(backup_root, backup_id, profile)
        # Hold the installation mutex and require a proven stopped server
        with self._mutex.guard(str(dayz_root)):
            self._require_stopped()
            with self._validated_backup(backup_root, backup_id, profile) as (directory, manifest):
                targets = self._storage.targets(
                    directory, manifest, dayz_root, profile.values.runtime_profile,
                )
                editor = self._editor_restore(profile, directory, manifest, targets, "preview")
        # Sign the preview so apply can detect stale expectations; the editor summary binds the editor records
        preview = RestorePreview(
            manifest.backup_id,
            manifest.created_at,
            profile.values.profile_id,
            profile.revision,
            settings.revision,
            manifest.manifest_digest,
            targets,
            editor=None if editor is None else editor.summary,
            editor_notes=() if editor is None else tuple(editor.notes),
        ).signed()
        value = preview.to_dict()
        # Describe recovery behavior and target counts for the confirmation UI
        value["recovery_plan"] = (
            "Existing targets receive verified recovery copies. Publication compensates in reverse order."
        )
        value["replacement_count"] = sum(item.action == "REPLACE" for item in targets)
        value["creation_count"] = sum(item.action == "CREATE" for item in targets)
        return value

    def apply(
        self,
        profile_id: object,
        backup_id: object,
        expected_profile_revision: object,
        expected_settings_revision: object,
        expected_manifest_digest: object,
        preview_fingerprint: object,
        operation_id: str,
        checkpoint: Any,
    ) -> dict[str, object]:
        """Apply a previewed restore as one journaled operation."""
        # Validate revisions and digests before any state is loaded
        _revision(expected_profile_revision, "profile")
        _revision(expected_settings_revision, "settings")
        if not isinstance(expected_manifest_digest, str) or SHA256.fullmatch(expected_manifest_digest) is None:
            raise RestoreStorageError("INVALID_REQUEST", "Expected manifest digest is invalid.")
        if not isinstance(preview_fingerprint, str) or SHA256.fullmatch(preview_fingerprint) is None:
            raise RestoreStorageError("INVALID_REQUEST", "Restore preview fingerprint is invalid.")
        profile, settings, dayz_root = self._context(profile_id)
        # Refuse stale profile or settings revisions before touching storage
        if profile.revision != expected_profile_revision or settings.revision != expected_settings_revision:
            raise RevisionConflict("Restore profile or settings revision changed.")
        backup_root = self._settings.backup_root(settings)
        self._validate_manifest(backup_root, backup_id, profile)
        with self._mutex.guard(str(dayz_root)):
            self._require_stopped()
            with self._validated_backup(backup_root, backup_id, profile) as (directory, manifest):
                targets = self._storage.targets(
                    directory, manifest, dayz_root, profile.values.runtime_profile,
                )
                # The editor part reads the records again and computes the shared check before the journal
                editor = self._editor_restore(profile, directory, manifest, targets, operation_id)
                # Rebuild the signed preview and refuse stale expectations
                current = RestorePreview(
                    manifest.backup_id, manifest.created_at, profile.values.profile_id,
                    profile.revision, settings.revision, manifest.manifest_digest, targets,
                    editor=None if editor is None else editor.summary,
                    editor_notes=() if editor is None else tuple(editor.notes),
                ).signed()
                if (
                    manifest.manifest_digest != expected_manifest_digest
                    or current.fingerprint != preview_fingerprint
                ):
                    raise RevisionConflict("Restore preview is stale or changed.")
                # Apply the journaled restore; A13: the writer side is taken before WRITE_JOURNAL is relayed
                scope = WriterScope(self._folder_writer)
                try:
                    with ExitStack() as held:
                        return self._storage.restore(
                            directory, manifest, dayz_root, self._recovery_root, self._journals, operation_id,
                            self._locked(scope.relay_after_acquire("WRITE_JOURNAL", checkpoint), editor, held),
                            profile.values.runtime_profile, editor=() if editor is None else editor.writes,
                            manager=None if self._editor is None else self._editor.paths,
                        )
                finally:
                    scope.release()

    def _editor_restore(self, profile: Any, directory: Path, manifest: Any, targets: Any,
                        operation_id: str) -> EditorRestore | None:
        """Return the editor files of the restore; a schema 4 archive needs the bound editor part."""
        if self._editor is None:
            if manifest.editor is not None:
                raise BackupStorageError(
                    RESTORE_PENDING_RUNTIME,
                    "This backup contains a target form that the current restore mapper does not support.",
                )
            return None
        return self._editor.build(profile.values.profile_id, directory, manifest, targets, operation_id=operation_id)

    def _locked(self, relay: Any, editor: EditorRestore | None, held: ExitStack) -> Any:
        """Wrap the checkpoint: after the writer side at WRITE_JOURNAL, take the association lock (D2 lock order)."""
        def checkpoint(phase: str, percent: int) -> None:
            """Relay the phase, then hold the lock until the restore returns."""
            relay(phase, percent)
            if phase == "WRITE_JOURNAL" and editor is not None and self._editor is not None:
                held.enter_context(self._editor.hold(editor))
        return checkpoint

    def _context(self, profile_id: object):
        """Load the selected profile plus the settings needed for restore paths."""
        profile = self._profiles.read(profile_id)
        settings = self._settings.load()
        # Restores require both a revision and a configured DayZ root
        if settings.revision is None or settings.dayz_root is None:
            raise RestoreStorageError("PATH_INVALID", "Restore settings are not configured.")
        return profile, settings, Path(settings.dayz_root)


def _revision(value: object, label: str) -> None:
    """Validate a non-negative integer revision value."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RestoreStorageError("INVALID_REQUEST", f"Expected {label} revision is invalid.")
