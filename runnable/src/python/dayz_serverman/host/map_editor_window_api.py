"""Expose only implemented, profile-bound calls to one mission-map editor.

Three lane reads name no profile and no path (8.18; Architect 2026-10-15 15:46:42, ruling 4). They read the
operation events, one operation record and the mission map recovery state of the installation. The fourth
unbound read lists the profiles for Copy from another profile; the host reduces each record (9.2).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ..application.mission_map_images import ImageRefusal, ImageSelectionService
from ..bridge.contracts import BridgeResult, ErrorCode
from ..bridge.facade import BridgeFacade
from .map_editor_transfer_api import MapEditorTransferMethods


# Host codes of a refused dialog choice; budget and type refusals keep the display-read code
_REGISTER_CODES = {"over_budget": ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT,
                   "unsupported_image_type": ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT}
# File types of the native image dialog
IMAGE_FILE_TYPES = ("Map images (*.png;*.jpg;*.jpeg)",)
# The only methods that the editor calls without the bound profile; one dispatch path checks this allowlist
UNBOUND_METHODS = frozenset(("get_operation", "read_operation_events", "inspect_mission_map_recovery",
                             "list_profiles"))
# Kinds whose full record reaches the editor. A record of another kind keeps only REDUCED_FIELDS. So no result
# of another operation, for example a backup path, reaches the editor document
MAP_OPERATION_KINDS = frozenset(("APPLY_MISSION_MAP", "RESTORE_MISSION_MAP_ORIGINAL", "RESTORE_MISSION_MAP_PREVIOUS",
                                 "APPLY_MEDICAL_FEATURE", "ADOPT_MISSION_MAP"))
REDUCED_FIELDS = ("operation_id", "kind", "state", "revision", "progress_phase")
# Page size of the editor follower (ruling 4)
EVENT_PAGE = 100


class MissionMapEditorApi(MapEditorTransferMethods):
    """Keep editor requests bound to the profile selected by the native host."""

    def __init__(self, bridge: BridgeFacade, profile_id: str, selections: ImageSelectionService | None, *,
                 dialogs: tuple[Any, Any] | None = None,
                 protected: Callable[[], Iterable[Path]] | None = None) -> None:
        """Retain host dependencies without exposing profile or path selection.

        `dialogs` holds the open and the save dialog constants of pywebview; `protected` returns the roots
        that an export never writes into (9.1).
        """
        self._bridge = bridge
        self._profile_id = profile_id
        self._selections = selections
        self._window: Any = None
        self._counter = 0
        self._lock = threading.Lock()
        self._dialogs, self._protected, self._import_lock = dialogs, protected, threading.Lock()

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

    def preview_adoption(self) -> dict[str, Any]:
        """Review the adoption of the bound profile's mission (10.13); nothing is written."""
        return self._invoke("preview_mission_map_adoption", {})

    def adopt_mission(self, preview_fingerprint: object) -> dict[str, Any]:
        """Queue the reviewed adoption (10.35); the answer is an operation handle."""
        return self._invoke("adopt_mission_map", {"preview_fingerprint": preview_fingerprint})

    def operation_events(self, after_sequence: object) -> dict[str, Any]:
        """Read the lane events after a cursor; the answer is the read_operation_events answer unchanged."""
        return self._invoke_unbound("read_operation_events", {"after_sequence": after_sequence, "maximum": EVENT_PAGE})

    def operation(self, operation_id: object) -> dict[str, Any]:
        """Read one operation record: in full for a map-affecting kind, else only the reduced fields."""
        answer = self._invoke_unbound("get_operation", {"operation_id": operation_id})
        value = answer.get("value")
        if answer.get("success") is True and isinstance(value, dict) and value.get("kind") not in MAP_OPERATION_KINDS:
            answer = {**answer, "value": {name: value.get(name) for name in REDUCED_FIELDS}}
        return answer

    def check_recovery(self) -> dict[str, Any]:
        """Check the installation-wide mission map recovery (8.5); the answer is unchanged."""
        return self._invoke_unbound("inspect_mission_map_recovery", {})

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

    def _invoke_unbound(self, method: str, parameters: dict[str, object]) -> dict[str, Any]:
        """Send one request without a profile, only for a method of the fixed allowlist."""
        if method not in UNBOUND_METHODS:
            raise ValueError(f"{method} is not an unbound editor method")
        return self._bridge.dispatch({
            "contract_version": 1, "request_id": self._next_request_id(), "method": method, "parameters": parameters,
        })

    def _invoke(self, method: str, parameters: dict[str, object]) -> dict[str, Any]:
        """Send one named, versioned request with the host-owned profile binding."""
        return self._bridge.dispatch({
            "contract_version": 1, "request_id": self._next_request_id(),
            "method": method, "parameters": {**parameters, "profile_id": self._profile_id},
        })
