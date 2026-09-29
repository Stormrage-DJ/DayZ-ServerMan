"""Operator preference methods exposed to WebView2."""

from __future__ import annotations

from typing import Any


class PreferenceHostMethods:
    """Native methods for reading and persisting operator preferences."""
    def get_ui_preferences(self) -> dict[str, Any]:
        """Return the stored UI preferences."""
        return self._invoke("get_ui_preferences", {})

    def save_selected_profile(self, profile_id: object) -> dict[str, Any]:
        """Persist the profile selected in the desktop UI."""
        return self._invoke("save_selected_profile", {"profile_id": profile_id})

    def save_backup_after_stop(
        self, profile_id: object, enabled: object,
    ) -> dict[str, Any]:
        """Persist the backup-after-stop preference for a profile."""
        return self._invoke("save_backup_after_stop", {
            "profile_id": profile_id,
            "enabled": enabled,
        })
