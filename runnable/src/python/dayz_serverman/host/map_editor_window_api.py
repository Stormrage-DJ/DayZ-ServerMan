"""Expose only implemented, profile-bound calls to one mission-map editor."""

from __future__ import annotations

import threading
from typing import Any

from ..application.mission_map_images import ImageRefusal, ImageSelectionService
from ..bridge.contracts import BridgeResult, ErrorCode
from ..bridge.facade import BridgeFacade


# Host codes of a refused dialog choice; budget and type refusals keep the display-read code
_REGISTER_CODES = {"over_budget": ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT,
                   "unsupported_image_type": ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT}
# File types of the native image dialog
IMAGE_FILE_TYPES = ("Map images (*.png;*.jpg;*.jpeg)",)


class MissionMapEditorApi:
    """Keep editor requests bound to the profile selected by the native host."""

    def __init__(self, bridge: BridgeFacade, profile_id: str, selections: ImageSelectionService | None) -> None:
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

    def set_aside_plan(self, mission_key: object) -> dict[str, Any]:
        """Set aside the invalid saved plan of the loaded mission; the window confirms first and then loads again."""
        return self._invoke("set_aside_mission_map_plan", {"mission_key": mission_key})

    def preview_plan(self) -> dict[str, Any]:
        """Review the saved plan of the bound profile's active mission for Apply (8.9); nothing is written."""
        return self._invoke("preview_mission_map", {})

    def apply_plan(self, preview_fingerprint: object, choices: object) -> dict[str, Any]:
        """Queue Apply of the reviewed preview with the admin's choices; the answer is an operation handle."""
        return self._invoke("apply_mission_map", {"preview_fingerprint": preview_fingerprint, "choices": choices})

    def preview_restore(self, mode: object) -> dict[str, Any]:
        """Review Restore original ("original") or Restore previous state ("previous"); nothing is written."""
        return self._invoke("preview_mission_map_restore", {"mode": mode})

    def restore_mission(self, mode: object, preview_fingerprint: object) -> dict[str, Any]:
        """Queue the reviewed restore of the mission files; the answer is an operation handle."""
        return self._invoke("restore_mission_map", {"mode": mode, "preview_fingerprint": preview_fingerprint})

    def inspect(self, plan: object, target: object) -> dict[str, Any]:
        """Start an Inspect read of a point or an object; plan is the draft, or None for the saved plan (4.6)."""
        return self._invoke("inspect_mission_map", {"plan": plan, "target": target})

    def continue_inspection(self, job_id: object) -> dict[str, Any]:
        """Advance the Inspect job of this editor by one call."""
        return self._invoke("continue_mission_map_inspection", {"job_id": job_id})

    def cancel_inspection(self, job_id: object) -> dict[str, Any]:
        """Retire the Inspect job of this editor; the call opens no file."""
        return self._invoke("cancel_mission_map_inspection", {"job_id": job_id})

    def read_tier_layer(self, mission_key: object, tier: object) -> dict[str, Any]:
        """Read one original tier of the bound profile's mission as a 1-bit PNG."""
        return self._invoke("read_mission_map_tier_layer", {"mission_key": mission_key, "tier": tier})

    def read_background(self, mission_key: object, selection_id: object, part: object) -> dict[str, Any]:
        """Read one part of the chosen image (a token) or of the stored image (null)."""
        return self._invoke("read_mission_map_background",
                            {"mission_key": mission_key, "selection_id": selection_id, "part": part})

    def select_background(self, mission_key: object) -> dict[str, Any]:
        """Open the native image dialog on this editor, register the choice and read its first part.

        A cancelled dialog gives {"cancelled": true}. A successful answer is the background read of
        part 0 with the new token added as `selection_id`. The path never leaves the host.
        """
        request_id = self._next_request_id()
        try:
            if self._window is None or self._selections is None:
                raise RuntimeError("unbound editor")
            chosen = self._window.create_file_dialog(allow_multiple=False, file_types=IMAGE_FILE_TYPES)
        except Exception:
            return BridgeResult.failed(request_id, ErrorCode.INTERNAL_FAILURE,
                                       "The map editor could not open the image dialog.").to_dict()
        if not chosen:
            return BridgeResult.ok(request_id, {"cancelled": True}).to_dict()
        path = str(chosen[0]) if isinstance(chosen, (tuple, list)) else str(chosen)
        try:
            token, _image = self._selections.register(self._profile_id, path)
        except ImageRefusal as refusal:
            # The dialog reports file problems as path errors and budget or type problems as content errors
            code = _REGISTER_CODES.get(refusal.reason, ErrorCode.PATH_INVALID)
            return BridgeResult.failed(request_id, code, str(refusal), False,
                                       {"reason": refusal.reason, **refusal.details}).to_dict()
        answer = self.read_background(mission_key, token, 0)
        if answer.get("success") and isinstance(answer.get("value"), dict):
            answer["value"]["selection_id"] = token
        return answer

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
