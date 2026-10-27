"""Legacy import and legacy backup reference methods exposed to the WebView."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class LegacyImportHostMethods:
    """Translate legacy import and legacy backup calls into bridge requests."""

    def _initialize_legacy_import_host(self) -> None:
        """Reset the native folder selector before the runtime wires one."""
        self._legacy_folder_selector: Callable[[], str | None] | None = None

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

    @staticmethod
    def _native_selection_failure(message: str) -> dict[str, Any]:
        """Return a safe failure envelope for native selection problems."""
        return {
            "contract_version": 1, "request_id": "native-selection", "success": False,
            "error": {"code": "PATH_INVALID", "message": message, "retryable": False},
        }
