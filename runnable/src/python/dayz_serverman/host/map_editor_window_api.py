"""Expose only implemented, profile-bound calls to one mission-map editor."""

from __future__ import annotations

import threading
from typing import Any

from ..bridge.facade import BridgeFacade


class MissionMapEditorApi:
    """Keep editor requests bound to the profile selected by the native host."""

    def __init__(self, bridge: BridgeFacade, profile_id: str, selections: object) -> None:
        """Retain host dependencies without exposing profile or path selection."""
        self._bridge = bridge
        self._profile_id = profile_id
        self._selections = selections
        self._window: Any = None
        self._counter = 0
        self._lock = threading.Lock()

    def _bind_window(self, window: object) -> None:
        """Bind the editor window after native creation."""
        self._window = window

    def load_plan(self) -> dict[str, Any]:
        """Read the bound profile's active mission plan."""
        return self._invoke("load_mission_map", {})

    def save_plan(self, plan: object, expected_revision: object,
                  background_selection_id: object) -> dict[str, Any]:
        """Forward the draft and guards unchanged to the strict bridge."""
        return self._invoke("save_mission_map_plan", {
            "plan": plan, "expected_revision": expected_revision,
            "background_selection_id": background_selection_id,
        })

    def toggle_full_screen(self) -> dict[str, Any]:
        """Toggle only the bound editor window and return a safe host envelope."""
        request_id = self._next_request_id()
        # Refuse native calls until the registry has bound this editor's window
        try:
            if self._window is None:
                raise RuntimeError("unbound editor")
            self._window.toggle_fullscreen()
        except Exception:
            return {"contract_version": 1, "request_id": request_id, "success": False,
                    "error": {"code": "INTERNAL_FAILURE", "retryable": False,
                              "message": "The map editor could not change full-screen mode."}}
        return {"contract_version": 1, "request_id": request_id,
                "success": True, "value": {}}

    def _next_request_id(self) -> str:
        """Allocate a unique editor request ID across concurrent callbacks."""
        with self._lock:
            self._counter += 1
            return f"editor-{self._counter}"

    def _invoke(self, method: str, parameters: dict[str, object]) -> dict[str, Any]:
        """Send one named, versioned request with the host-owned profile binding."""
        return self._bridge.dispatch({
            "contract_version": 1, "request_id": self._next_request_id(),
            "method": method, "parameters": {**parameters, "profile_id": self._profile_id},
        })
