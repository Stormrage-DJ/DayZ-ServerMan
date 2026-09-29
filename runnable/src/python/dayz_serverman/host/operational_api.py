"""Operational lifecycle and diagnostic methods exposed to WebView2."""

from __future__ import annotations

from typing import Any


class OperationalHostMethods:
    """Native methods for server lifecycle control and log access."""
    def get_server_status(self) -> dict[str, Any]:
        """Return the current server status."""
        return self._invoke("get_server_status", {})

    def start_server(
        self,
        profile_id: object,
        expected_profile_revision: object,
        expected_settings_revision: object,
    ) -> dict[str, Any]:
        """Start the server for a profile with revision guards."""
        return self._invoke("start_server", {
            "profile_id": profile_id,
            "expected_profile_revision": expected_profile_revision,
            "expected_settings_revision": expected_settings_revision,
        })

    def stop_server(
        self, profile_id: object, expected_profile_revision: object,
        expected_settings_revision: object, backup_after_stop: object,
    ) -> dict[str, Any]:
        """Stop the server and optionally back up after the stop."""
        return self._invoke("stop_server", {
            "profile_id": profile_id,
            "expected_profile_revision": expected_profile_revision,
            "expected_settings_revision": expected_settings_revision,
            "backup_after_stop": backup_after_stop,
        })

    def restart_server(
        self,
        profile_id: object,
        expected_profile_revision: object,
        expected_settings_revision: object,
        backup_after_stop: object,
    ) -> dict[str, Any]:
        """Restart the server and optionally back up after the stop."""
        return self._invoke("restart_server", {
            "profile_id": profile_id,
            "expected_profile_revision": expected_profile_revision,
            "expected_settings_revision": expected_settings_revision,
            "backup_after_stop": backup_after_stop,
        })

    def read_log(self, source: object, maximum_lines: object = 300) -> dict[str, Any]:
        """Read the tail of a server or manager log."""
        return self._invoke("read_log", {
            "source": source,
            "maximum_lines": maximum_lines,
        })
