"""Named backup and restore host API methods."""
from typing import Any


class BackupHostMethods:
    """Translate exact archive and restore parameters into bridge calls."""
    def _set_backup_archive_selector(self, selector):
        """Register the native single-ZIP picker."""
        self._backup_archive_selector = selector

    def select_backup_archive(self):
        """Browse for one ZIP, treating dismissal as a successful cancellation."""
        if self._backup_archive_selector is None:
            return self._settings_request_failure("Backup archive selection is unavailable.")
        try:
            path = self._backup_archive_selector()
        except Exception:
            return self._settings_request_failure("Backup archive selection failed safely.")
        if path is None:
            return {"contract_version": 1, "request_id": "native-backup-selection", "success": True, "value": {"cancelled": True}}
        return self._invoke("inspect_backup_archive", {"path": path})

    def list_backups(self, profile_id: object) -> dict[str, Any]:
        """List backups belonging to a profile."""
        return self._invoke("list_backups", {"profile_id": profile_id})

    def list_backup_catalog(self):
        """List independently retained server archives."""
        return self._invoke("list_backup_catalog", {})

    def create_backup(
        self,
        profile_id: object,
        expected_profile_revision: object,
        expected_settings_revision: object,
    ) -> dict[str, Any]:
        """Create a backup for a profile with revision guards."""
        return self._invoke(
            "create_backup",
            {
                "profile_id": profile_id,
                "expected_profile_revision": expected_profile_revision,
                "expected_settings_revision": expected_settings_revision,
            },
        )

    def preview_restore(self, profile_id: object, backup_id: object) -> dict[str, Any]:
        """Preview restoring a backup into a profile."""
        return self._invoke(
            "preview_restore", {"profile_id": profile_id, "backup_id": backup_id},
        )

    def apply_restore(
        self,
        profile_id: object,
        backup_id: object,
        expected_profile_revision: object,
        expected_settings_revision: object,
        expected_manifest_digest: object,
        preview_fingerprint: object,
    ) -> dict[str, Any]:
        """Apply a reviewed restore preview."""
        return self._invoke(
            "apply_restore",
            {
                "profile_id": profile_id,
                "backup_id": backup_id,
                "expected_profile_revision": expected_profile_revision,
                "expected_settings_revision": expected_settings_revision,
                "expected_manifest_digest": expected_manifest_digest,
                "preview_fingerprint": preview_fingerprint,
            },
        )

    def inspect_restore_recovery(self) -> dict[str, Any]:
        """Report pending restore recovery state."""
        return self._invoke("inspect_restore_recovery", {})

    def preview_profile_restore(self, backup_id, profile_id, display_name, storage_policy, game_port, steam_query_port):
        """Preview reconstructing a deleted profile directly from an archive."""
        return self._invoke("preview_profile_restore", dict(backup_id=backup_id, profile_id=profile_id,
            display_name=display_name, storage_policy=storage_policy, game_port=game_port, steam_query_port=steam_query_port))

    def restore_profile_from_backup(self, backup_id, profile_id, display_name, storage_policy, game_port, steam_query_port,
                                    expected_manifest_digest, preview_fingerprint, overwrite_confirmation):
        """Queue the reviewed direct profile restoration."""
        return self._invoke("restore_profile_from_backup", dict(backup_id=backup_id, profile_id=profile_id,
            display_name=display_name, storage_policy=storage_policy, game_port=game_port, steam_query_port=steam_query_port,
            expected_manifest_digest=expected_manifest_digest, preview_fingerprint=preview_fingerprint,
            overwrite_confirmation=overwrite_confirmation))

