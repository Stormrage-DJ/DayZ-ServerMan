"""Update status methods exposed to WebView2."""

from __future__ import annotations

from typing import Any


class UpdateStatusHostMethods:
    """Native methods for the update status, the check request and file verification."""
    def get_update_status(self, profile_id: object) -> dict[str, Any]:
        """Return the update status for a profile."""
        return self._invoke("get_update_status", {"profile_id": profile_id})

    def request_update_check(self, scope: object, force: object) -> dict[str, Any]:
        """Ask for a remote update check; the call returns at once."""
        return self._invoke("request_update_check", {"scope": scope, "force": force})

    def verify_mod_files(
        self, profile_id: object, expected_profile_revision: object,
        expected_settings_revision: object,
    ) -> dict[str, Any]:
        """Queue a full hash of the profile's Workshop mods and their server-folder copies."""
        return self._invoke("verify_mod_files", {
            "profile_id": profile_id,
            "expected_profile_revision": expected_profile_revision,
            "expected_settings_revision": expected_settings_revision,
        })
