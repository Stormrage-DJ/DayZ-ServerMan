"""Narrow native-assisted settings methods exposed to the WebView."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


# Settings roles mapped to the dialog kind each one requires
SETTINGS_PATH_KINDS = {
    "dayz_root": "folder",
    "steamcmd_root": "folder",
    "custom_backup_root": "folder",
}
# Fields accepted by the settings save contract
PATH_FIELDS = frozenset(SETTINGS_PATH_KINDS)


class SettingsHostMethods:
    """Provide only the accepted settings selector and save contracts."""

    def _initialize_settings_host(self) -> None:
        """Reset the native path selector before the runtime wires one."""
        self._settings_path_selector: Callable[[str, str], str | None] | None = None

    def _set_settings_path_selector(
        self, selector: Callable[[str, str], str | None],
    ) -> None:
        """Register the native file and folder picker."""
        self._settings_path_selector = selector

    def select_settings_path(self, role: object) -> dict[str, Any]:
        """Select a settings path for one role through the native picker."""
        # Reject roles outside the accepted settings contract
        if not isinstance(role, str) or role not in SETTINGS_PATH_KINDS:
            return self._settings_request_failure("Settings path role is invalid.")
        # Fail safely when the runtime registered no picker
        if self._settings_path_selector is None:
            return self._settings_request_failure("Path selection is unavailable.")
        # Convert picker failures into safe request errors
        try:
            selected = self._settings_path_selector(role, SETTINGS_PATH_KINDS[role])
        except Exception:
            return self._settings_request_failure("Path selection failed safely.")
        # Report cancellation without calling the bridge
        if selected is None:
            return {
                "contract_version": 1,
                "request_id": "native-settings-selection",
                "success": True,
                "value": {"cancelled": True, "role": role},
            }
        return self._invoke(
            "validate_settings_path_selection", {"role": role, "path": selected},
        )

    def save_settings(
        self, settings: object, expected_revision: object,
    ) -> dict[str, Any]:
        """Persist the accepted settings fields with an expected revision."""
        # Accept only the exact settings payload shape
        if not isinstance(settings, dict) or set(settings) != PATH_FIELDS:
            return self._settings_request_failure("Settings payload is invalid.")
        return self._invoke(
            "save_settings", {**settings, "expected_revision": expected_revision},
        )

    @staticmethod
    def _settings_request_failure(message: str) -> dict[str, Any]:
        """Return a safe failure envelope for settings requests."""
        return {
            "contract_version": 1,
            "request_id": "native-settings-selection",
            "success": False,
            "error": {
                "code": "INVALID_REQUEST",
                "message": message,
                "retryable": False,
            },
        }
