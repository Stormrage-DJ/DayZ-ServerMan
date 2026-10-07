"""Restore preview and apply use cases."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..domain.backups import SHA256
from ..domain.backup_inventory import mission_payload_prefix, server_config_entry
from ..domain.lifecycle import LifecycleFailure, ServerState
from ..domain.models import RecordUnavailable, RevisionConflict
from ..domain.restores import RestorePreview
from ..repositories.backups import BackupStorage, BackupStorageError
from ..repositories.restore_journal import RestoreJournalRepository
from ..repositories.restore_storage import RestoreStorage, RestoreStorageError
from .folder_writer_scope import OWNER_WRITER_WAIT_SECONDS, WriterScope
from .installation_guard import InstallationGuard
from .lifecycle_ports import InstallationMutexPort, ServerFolderWriterPort
from .profiles import ProfileService
from .settings import SettingsService

# Block reason of a restore recovery that the installation guard refused
RECOVERY_NOT_STOPPED = "Mutations are blocked by an interrupted backup restore while the server is not proven stopped."
# Block reason of a restore recovery without a configured DayZ server folder; a repair save may pass it (QF-069)
RECOVERY_NO_DAYZ_ROOT = "Backup restore recovery requires a configured DayZ root."
# Operation kind of a restore apply; it owns every block that the restore recovery may lift
RESTORE_KIND = "RESTORE_BACKUP"


class RestoreService:
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
    ) -> None:
        """Store the collaborators used by restore preview and apply, and the A13 writer side."""
        self._profiles = profiles
        self._settings = settings
        self._backups = backups
        self._storage = storage
        self._journals = journals
        self._recovery_root = recovery_root
        self._lifecycle = lifecycle
        self._mutex = mutex
        self._folder_writer = folder_writer

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
        # Sign the preview so apply can detect stale expectations
        preview = RestorePreview(
            manifest.backup_id,
            manifest.created_at,
            profile.values.profile_id,
            profile.revision,
            settings.revision,
            manifest.manifest_digest,
            targets,
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
                # Rebuild the signed preview and refuse stale expectations
                current = RestorePreview(
                    manifest.backup_id, manifest.created_at, profile.values.profile_id,
                    profile.revision, settings.revision, manifest.manifest_digest, targets,
                ).signed()
                if (
                    manifest.manifest_digest != expected_manifest_digest
                    or current.fingerprint != preview_fingerprint
                ):
                    raise RevisionConflict("Restore preview is stale or changed.")
                # Apply the journaled restore; A13: the writer side is taken before WRITE_JOURNAL is relayed
                scope = WriterScope(self._folder_writer)
                try:
                    return self._storage.restore(
                        directory, manifest, dayz_root, self._recovery_root, self._journals, operation_id,
                        scope.relay_after_acquire("WRITE_JOURNAL", checkpoint), profile.values.runtime_profile,
                    )
                finally:
                    scope.release()

    def recovery_pending(self) -> bool:
        """Report whether a restore journal exists, without reading settings or taking the mutex."""
        return bool(self._journals.records())

    def inspect_recovery(self, wait_seconds: float = OWNER_WRITER_WAIT_SECONDS) -> dict[str, object]:
        """Finish or undo an interrupted restore and report whether recovery still blocks mutations.

        Recovery copies and renames files in the DayZ root, so it runs only under the
        installation mutex with a proven stopped server (QF-027, QF-039). A refusal
        writes nothing, keeps the journal for a later inspection, and names the reason.
        """
        # Without journal records nothing can be blocking
        if not self._journals.records():
            return {"blocked": False, "diagnostics": []}
        try:
            settings = self._settings.load()
            if settings.dayz_root is None:
                # Named before the guard, so the block can be passed by a save that sets the folder (QF-069)
                return {
                    "blocked": True,
                    "reason": RECOVERY_NO_DAYZ_ROOT,
                    "diagnostics": [{
                        "code": "RECOVERY_REQUIRED",
                        "message": "Restore recovery needs a configured DayZ server folder.",
                        "usable": False,
                    }],
                }
            root = Path(settings.dayz_root)
            # The guard refuses before the block runs; the state is read under the mutex; A13 writer side inside
            guard = InstallationGuard(self._lifecycle, self._mutex, self._folder_writer)
            with guard.stopped(root, folder_wait=wait_seconds):
                return self._storage.inspect(self._journals, root, self._recovery_root)
        except LifecycleFailure as failure:
            # A busy installation, or a server that is not proven stopped: recover later
            return {
                "blocked": True,
                "reason": RECOVERY_NOT_STOPPED,
                "diagnostics": [{
                    "code": failure.code,
                    "message": "Restore recovery needs a stopped server and the installation mutex.",
                    "usable": False,
                }],
            }
        except (OSError, RecordUnavailable, RestoreStorageError) as error:
            # Fail closed when recovery state cannot be inspected safely
            return {
                "blocked": True,
                "diagnostics": [{
                    "code": "RECOVERY_REQUIRED",
                    "message": "Restore recovery state could not be inspected safely.",
                    "usable": False,
                }],
            }

    def _context(self, profile_id: object):
        """Load the selected profile plus the settings needed for restore paths."""
        profile = self._profiles.read(profile_id)
        settings = self._settings.load()
        # Restores require both a revision and a configured DayZ root
        if settings.revision is None or settings.dayz_root is None:
            raise RestoreStorageError("PATH_INVALID", "Restore settings are not configured.")
        return profile, settings, Path(settings.dayz_root)

    @contextmanager
    def _validated_backup(self, root: Path, backup_id: object, profile: Any):
        """Yield the opened backup directory and manifest after all checks."""
        # Open the backup and verify it before exposing its payload
        with self._backups.open_verified(root, backup_id, profile.values.profile_id) as verified:
            directory, manifest = verified
            # Enforce profile, inventory, and restore compatibility rules
            self._require_profile_context(profile, manifest)
            self._require_supported_inventory(profile, manifest)
            self._backups.require_restore_compatible(manifest)
            yield directory, manifest

    def _validate_manifest(self, root: Path, backup_id: object, profile: Any) -> None:
        """Verify the backup manifest and its profile compatibility."""
        manifest = self._backups.verified_manifest(root, backup_id, profile.values.profile_id)
        self._require_profile_context(profile, manifest)
        self._require_supported_inventory(profile, manifest)
        self._backups.require_restore_compatible(manifest)

    @staticmethod
    def _require_profile_context(profile: Any, manifest: Any) -> None:
        """Reject manifests whose stored profile context differs from the selection."""
        if (
            manifest.runtime_profile is None
            or profile.values.runtime_profile is None
            or manifest.profile_revision != profile.revision
            or manifest.semantic_profile_digest != profile.semantic_digest
            or manifest.runtime_profile != profile.values.runtime_profile
        ):
            raise BackupStorageError(
                "PROFILE_CONTEXT_MISMATCH",
                "The backup profile context does not match the selected profile.",
            )

    @staticmethod
    def _require_supported_inventory(profile: Any, manifest: Any) -> None:
        """Reject payload files outside the selected config and complete mission tree."""
        actual = tuple(entry.path for entry in manifest.entries if entry.path.startswith("payload/"))
        config = server_config_entry(profile.values)
        mission_root = profile.values.mission_root
        if mission_root is None:
            # Profile context is digest-bound; infer the mission prefix from the payload paths.
            candidates = {
                "/".join(path.split("/")[:3]) + "/"
                for path in actual if path.startswith("payload/mpmissions/")
            }
            prefix = next(iter(candidates)) if len(candidates) == 1 else ""
        else:
            prefix = mission_payload_prefix(mission_root)
        mission_entries = tuple(path for path in actual if prefix and path.startswith(prefix))
        if (
            config not in actual
            or not mission_entries
            or any(path != config and not (prefix and path.startswith(prefix)) for path in actual)
        ):
            raise BackupStorageError(
                "UNSUPPORTED_SNAPSHOT_CONTENT",
                "Backup content is outside the selected server configuration or mission tree.",
            )

    def _require_stopped(self) -> None:
        """Require a proven stopped server before mutating restore targets."""
        status = self._lifecycle.status()
        if status.state == ServerState.STOPPED:
            return
        # Map the blocking state to a precise failure code
        if status.state == ServerState.RUNNING_EXTERNAL:
            code = "EXTERNAL_PROCESS"
        elif status.state in {ServerState.UNKNOWN, ServerState.AMBIGUOUS}:
            code = "PROCESS_STATE_UNKNOWN"
        else:
            code = "CONTROL_CONFLICT"
        raise LifecycleFailure(code, f"Restore requires STOPPED; current state is {status.state.value}.")


def _revision(value: object, label: str) -> None:
    """Validate a non-negative integer revision value."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RestoreStorageError("INVALID_REQUEST", f"Expected {label} revision is invalid.")
