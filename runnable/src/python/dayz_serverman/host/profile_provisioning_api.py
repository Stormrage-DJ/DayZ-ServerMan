"""Profile provisioning methods exposed to WebView2."""

from __future__ import annotations

from typing import Any


class ProfileProvisioningHostMethods:
    """Native methods for mission discovery and guided profile creation."""

    def list_profile_missions(self) -> dict[str, Any]:
        """Return installed missions for the current DayZ root."""
        return self._invoke("list_profile_missions", {})

    def provision_profile(
        self, profile: object, expected_settings_revision: object,
    ) -> dict[str, Any]:
        """Queue creation of a profile and its operational files."""
        return self._invoke("provision_profile", {
            "profile": profile,
            "expected_settings_revision": expected_settings_revision,
        })
