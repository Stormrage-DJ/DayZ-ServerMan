"""Explicit named application methods exposed to the browser runtime."""
from __future__ import annotations
import threading
from collections.abc import Callable
from typing import Any
from ..bridge.facade import BridgeFacade
from .mod_publication_api import ModPublicationHostMethods
from .medical_feature_api import MedicalFeatureHostMethods
from .mod_inventory_api import ModInventoryHostMethods
from .operational_api import OperationalHostMethods
from .preference_api import PreferenceHostMethods
from .schedule_api import ScheduleHostMethods
from .settings_api import SettingsHostMethods

class HostApi(MedicalFeatureHostMethods, SettingsHostMethods, ModInventoryHostMethods,
              ModPublicationHostMethods, OperationalHostMethods, PreferenceHostMethods,
              ScheduleHostMethods):
    """Translate approved browser calls into strict bridge requests."""
    def __init__(self, bridge: BridgeFacade) -> None:
        """Store the bridge and prepare request tracking and selection hooks."""
        self._bridge = bridge
        self._counter = 0
        # Serialize request identifier allocation across browser callbacks
        self._lock = threading.Lock()
        # Native selection hooks stay unbound until the runtime wires them
        self._legacy_folder_selector: Callable[[], str | None] | None = None
        self._initialize_settings_host()

    def _set_legacy_folder_selector(self, selector: Callable[[], str | None]) -> None:
        """Register the native folder picker used by the legacy import flow."""
        self._legacy_folder_selector = selector

    def select_legacy_root(self) -> dict[str, Any]:
        """Select a legacy root through the native folder picker."""
        # Fail safely when the runtime registered no picker
        if self._legacy_folder_selector is None:
            return self._native_selection_failure("Folder selection is unavailable.")
        try:
            root = self._legacy_folder_selector()
        except Exception:
            return self._native_selection_failure("Folder selection failed safely.")
        # Treat a cancelled selection as an unconfirmed choice
        if root is None:
            return {
                "contract_version": 1, "request_id": "native-selection",
                "success": True, "value": {"cancelled": True},
            }
        return self._invoke("select_legacy_root", {"root": root})

    def preview_legacy_import(self, selection_id: object) -> dict[str, Any]:
        """Preview a legacy import for the selected root."""
        return self._invoke("preview_legacy_import", {"selection_id": selection_id})

    def apply_legacy_import(
        self, preview_id: object, preview_fingerprint: object, selected_items: object,
    ) -> dict[str, Any]:
        """Apply a confirmed legacy import preview."""
        return self._invoke("apply_legacy_import", {
            "preview_id": preview_id,
            "preview_fingerprint": preview_fingerprint,
            "selected_items": selected_items,
        })

    def list_legacy_backup_references(self) -> dict[str, Any]:
        """List discovered legacy backup references."""
        return self._invoke("list_legacy_backup_references", {})

    def revalidate_legacy_backup_references(
        self, expected_revision: object,
    ) -> dict[str, Any]:
        """Revalidate legacy backup references against an expected revision."""
        return self._invoke(
            "revalidate_legacy_backup_references",
            {"expected_revision": expected_revision},
        )

    def get_application_snapshot(self) -> dict[str, Any]:
        """Return the composed application snapshot for the shell."""
        return self._invoke("get_application_snapshot", {})

    def read_operation_events(
        self,
        after_sequence: object = 0,
        maximum: object = 100,
    ) -> dict[str, Any]:
        """Read operation events after a known sequence number."""
        return self._invoke(
            "read_operation_events",
            {"after_sequence": after_sequence, "maximum": maximum},
        )

    def get_operation(self, operation_id: object) -> dict[str, Any]:
        """Return one operation by identifier."""
        return self._invoke("get_operation", {"operation_id": operation_id})

    def request_operation_cancellation(self, operation_id: object) -> dict[str, Any]:
        """Request cancellation of a running operation."""
        return self._invoke(
            "request_operation_cancellation",
            {"operation_id": operation_id},
        )

    def request_shutdown(self) -> dict[str, Any]:
        """Request application shutdown through the bridge."""
        return self._invoke("request_shutdown", {})

    def save_steam_settings(
        self, expected_revision: object, authentication_mode: object, account_name: object,
    ) -> dict[str, Any]:
        """Persist Steam authentication settings with an expected revision."""
        return self._invoke("save_steam_settings", {"expected_revision": expected_revision,
            "authentication_mode": authentication_mode, "account_name": account_name})

    def authenticate_steamcmd(self, expected_settings_revision: object) -> dict[str, Any]:
        """Start SteamCMD authentication for the expected settings revision."""
        return self._invoke("authenticate_steamcmd", {
            "expected_settings_revision": expected_settings_revision})

    def update_workshop_items(
        self, profile_id: object, expected_profile_revision: object,
        expected_semantic_profile_digest: object, expected_settings_revision: object,
        authentication_mode: object, account_name: object, update_all_and_start: object,
    ) -> dict[str, Any]:
        """Update workshop items for a profile, optionally starting the server."""
        return self._invoke("update_workshop_items", {"profile_id": profile_id,
            "expected_profile_revision": expected_profile_revision,
            "expected_semantic_profile_digest": expected_semantic_profile_digest,
            "expected_settings_revision": expected_settings_revision,
            "authentication_mode": authentication_mode, "account_name": account_name,
            "update_all_and_start": update_all_and_start})

    def list_profiles(self) -> dict[str, Any]:
        """List stored server profiles."""
        return self._invoke("list_profiles", {})

    def read_profile(self, profile_id: object) -> dict[str, Any]:
        """Read one server profile by identifier."""
        return self._invoke("read_profile", {"profile_id": profile_id})

    def preview_profile_command(self, profile_id: object) -> dict[str, Any]:
        """Preview the launch command for a profile."""
        return self._invoke("preview_profile_command", {"profile_id": profile_id})

    def save_profile(self, profile: object, expected_revision: object) -> dict[str, Any]:
        """Save a profile with an expected revision guard."""
        return self._invoke(
            "save_profile", {"profile": profile, "expected_revision": expected_revision},
        )

    def delete_profile(self, profile_id: object, expected_revision: object) -> dict[str, Any]:
        """Delete a profile with an expected revision guard."""
        return self._invoke(
            "delete_profile",
            {"profile_id": profile_id, "expected_revision": expected_revision},
        )

    def list_backups(self, profile_id: object) -> dict[str, Any]:
        """List backups belonging to a profile."""
        return self._invoke("list_backups", {"profile_id": profile_id})

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

    def load_configuration(self, profile_id: object, target: object) -> dict[str, Any]:
        """Load a configuration target for editing."""
        return self._invoke(
            "load_configuration",
            {"profile_id": profile_id, "target": target},
        )

    def preview_configuration(
        self,
        profile_id: object,
        target: object,
        expected_profile_revision: object,
        expected_settings_revision: object,
        expected_digest: object,
        expected_server_digest: object,
        updates: object,
    ) -> dict[str, Any]:
        """Preview configuration updates with digest guards."""
        return self._configuration_edit(
            "preview_configuration",
            profile_id,
            target,
            expected_profile_revision,
            expected_settings_revision,
            expected_digest,
            expected_server_digest,
            updates,
        )

    def apply_configuration(
        self,
        profile_id: object,
        target: object,
        expected_profile_revision: object,
        expected_settings_revision: object,
        expected_digest: object,
        expected_server_digest: object,
        updates: object,
    ) -> dict[str, Any]:
        """Apply reviewed configuration updates."""
        return self._configuration_edit(
            "apply_configuration",
            profile_id,
            target,
            expected_profile_revision,
            expected_settings_revision,
            expected_digest,
            expected_server_digest,
            updates,
        )

    def load_mission_configuration(self, profile_id: object, target: object) -> dict[str, Any]:
        """Load a mission configuration target for editing."""
        return self._invoke("load_mission_configuration", {"profile_id": profile_id, "target": target})

    def preview_mission_configuration(
        self, profile_id: object, target: object, expected_profile_revision: object,
        expected_settings_revision: object, expected_digest: object, updates: object,
    ) -> dict[str, Any]:
        """Preview mission configuration updates."""
        return self._mission_edit("preview_mission_configuration", profile_id, target,
            expected_profile_revision, expected_settings_revision, expected_digest, updates)

    def apply_mission_configuration(
        self, profile_id: object, target: object, expected_profile_revision: object,
        expected_settings_revision: object, expected_digest: object, updates: object,
    ) -> dict[str, Any]:
        """Apply reviewed mission configuration updates."""
        return self._mission_edit("apply_mission_configuration", profile_id, target,
            expected_profile_revision, expected_settings_revision, expected_digest, updates)

    def convert_starter_loadout(
        self, profile_id: object, expected_profile_revision: object,
        expected_settings_revision: object, expected_digest: object,
    ) -> dict[str, Any]:
        """Convert the starter loadout with digest guards."""
        return self._invoke("convert_starter_loadout", {"profile_id": profile_id,
            "expected_profile_revision": expected_profile_revision,
            "expected_settings_revision": expected_settings_revision, "expected_digest": expected_digest})

    def _mission_edit(
        self, method: str, profile_id: object, target: object,
        expected_profile_revision: object, expected_settings_revision: object,
        expected_digest: object, updates: object,
    ) -> dict[str, Any]:
        """Dispatch a mission configuration edit request."""
        return self._invoke(method, {"profile_id": profile_id, "target": target,
            "expected_profile_revision": expected_profile_revision,
            "expected_settings_revision": expected_settings_revision,
            "expected_digest": expected_digest, "updates": updates})

    def _configuration_edit(self, method: str, profile_id: object, target: object,
                            expected_profile_revision: object, expected_settings_revision: object,
                            expected_digest: object, expected_server_digest: object,
                            updates: object) -> dict[str, Any]:
        """Dispatch a configuration edit request with digest guards."""
        return self._invoke(method, {"profile_id": profile_id, "target": target,
            "expected_profile_revision": expected_profile_revision,
            "expected_settings_revision": expected_settings_revision,
            "expected_digest": expected_digest, "expected_server_digest": expected_server_digest,
            "updates": updates})

    def _invoke(self, method: str, parameters: dict[str, object]) -> dict[str, Any]:
        """Dispatch one bridge request with a unique identifier."""
        # Allocate the request identifier without racing browser callbacks
        with self._lock:
            self._counter += 1
            request_id = f"webview-{self._counter}"
        # Send the versioned envelope to the strict bridge
        return self._bridge.dispatch(
            {
                "contract_version": 1,
                "request_id": request_id,
                "method": method,
                "parameters": parameters,
            }
        )

    @staticmethod
    def _native_selection_failure(message: str) -> dict[str, Any]:
        """Return a safe failure envelope for native selection problems."""
        return {
            "contract_version": 1, "request_id": "native-selection", "success": False,
            "error": {"code": "PATH_INVALID", "message": message, "retryable": False},
        }
