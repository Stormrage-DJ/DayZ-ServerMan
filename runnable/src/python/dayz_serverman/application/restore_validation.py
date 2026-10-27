"""Backup and server-state checks of a restore, a part of the restore service (moved from `restores.py` in 10.3)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..domain.backup_inventory import mission_payload_prefix, server_config_entry
from ..domain.lifecycle import LifecycleFailure, ServerState
from ..repositories.backups import BackupStorageError


class RestoreValidation:
    """The check methods of `RestoreService`.

    The service sets the attributes that these methods read: `_backups` and `_lifecycle`.
    """

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
