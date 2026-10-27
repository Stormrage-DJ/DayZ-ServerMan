"""Export plan, Import plan and Copy from another profile of one mission-map editor window.

The page never gets a path; answers name only the base file name (Architect 2026-10-17 17:10:42 in evidence 9.1,
sections 2 to 5). Export writes the draft that the page sends, so it works while autosave is stopped, and it does
not call the bridge. Import keeps the file text in one pending slot of this editor, behind a random selection ID.
It runs the Replace and the Merge previews of the owner bridge with that text.

No method takes a path. Copy names only the source profile; the host binds the destination and only reads the source plan.
"""

from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ..adapters.windows.user_files import UserFileError, checked_target, read_text, write_file
from ..application.mission_map_transfer_coordinator import REFUSAL_TEXTS
from ..bridge.contracts import BridgeResult, ErrorCode
from ..domain.mission_map_plan import PlanForm, parse_plan, portable_text
from ..domain.mission_map_values import (
    MAX_DOCUMENT_BYTES, PlanValidationError, refuse_json_constant, unique_json_keys,
)

# File types of the native dialogs (Product Owner XFER-6 and XFER-7 in evidence 9.1)
PLAN_FILE_TYPES = ("Map plans (*.json)",)
IMPORT_FILE_TYPES = ("Map plans (*.json)", "All files (*.*)")
# The suggested file name of the save dialog; {terrain} is the terrain ID of the draft (XFER-6)
SUGGESTED_NAME = "{terrain}-map-plan.json"
# The bridge code of each refused export target; a write failure is a storage failure
TARGET_CODES = {"no_folder": ErrorCode.PATH_INVALID, "folder_or_link": ErrorCode.PATH_INVALID,
                "protected": ErrorCode.PATH_OUTSIDE_ALLOWED_ROOT, "exists": ErrorCode.PATH_INVALID,
                "not_written": ErrorCode.STORAGE_FAILURE}
MODES = ("replace", "merge")


def protected_roots(root: Path, settings: Any) -> tuple[Path, ...]:
    """Return the roots that an export never writes into, by the rule of `map export` (DC-3).

    The roots are the manager root, the backup folder of the settings and the DayZ root when it is set.
    """
    current = settings.load()
    roots = [Path(root), Path(settings.backup_root(current))]
    if current.dayz_root is not None:
        roots.append(Path(current.dayz_root))
    return tuple(roots)


def _chosen_path(chosen: object) -> str | None:
    """Return the one path of a dialog answer; pywebview gives None or an empty answer for a cancel."""
    if not chosen:
        return None
    return str(chosen[0]) if isinstance(chosen, (tuple, list)) else str(chosen)


class MapEditorTransferMethods:
    """Host methods of Export plan and Import plan; `MissionMapEditorApi` inherits them.

    The editor API sets `_bridge`, `_profile_id`, `_window`, `_dialogs` (the open and the save dialog constants),
    `_protected` (a callable that returns the protected roots) and `_import_lock`.
    """

    _window: Any
    _dialogs: tuple[Any, Any] | None
    _protected: Callable[[], Iterable[Path]] | None
    _import_lock: threading.Lock
    _profile_id: str
    _invoke: Callable[[str, dict[str, object]], dict[str, Any]]
    _invoke_unbound: Callable[[str, dict[str, object]], dict[str, Any]]
    _pending: tuple[str, str, str] | None = None

    def export_plan(self, plan: object) -> dict[str, Any]:
        """Write the shown draft to the file of the native save dialog; the answer is `cancelled` or `file_name`.

        A draft that fails the D1 save check is refused with each problem and writes nothing. The save dialog
        asks the replace question itself, so an existing file is replaced. No model, history or saved plan changes.
        """
        request_id = self._next_request_id()
        # The same D1 check as the save; the portable text is the text of `map export`
        try:
            parsed = parse_plan(plan, PlanForm.STORED)
        except PlanValidationError as error:
            problems = [{"object_id": problem.object_id, "rule": "invalid_document", "message": problem.message}
                        for problem in error.problems]
            return BridgeResult.failed(request_id, ErrorCode.INVALID_REQUEST, "The map plan in the window is not valid.",
                                       False, {"reason": "invalid_document", "problems": problems}).to_dict()
        payload = portable_text(parsed).encode("utf-8")
        suggested = SUGGESTED_NAME.format(terrain=parsed["terrain"]["terrain_id"])
        try:
            path = _chosen_path(self._dialog(1, save_filename=suggested, file_types=PLAN_FILE_TYPES))
        except Exception:
            return self._dialog_failure(request_id)
        if path is None:
            return BridgeResult.ok(request_id, {"cancelled": True}).to_dict()
        file_name = Path(path).name
        # D17 also holds for the window: no folder, link or protected root, and a checked staged write
        try:
            target = checked_target(path, tuple(self._protected() if self._protected else ()), overwrite=True)
            write_file(target, payload, overwrite=True)
        except UserFileError as error:
            return BridgeResult.failed(request_id, TARGET_CODES[error.reason], "The map plan file was not written.",
                                       False, {"reason": error.reason, "file_name": file_name}).to_dict()
        return BridgeResult.ok(request_id, {"file_name": file_name}).to_dict()

    def select_import_file(self) -> dict[str, Any]:
        """Open the native open dialog, read the file once and keep its text, then run both previews.

        The answer is `cancelled`; a file refusal with `kind` and `file_name`; or `selection_id`, `file_name`
        and `previews`. A new selection replaces the pending one. A file problem keeps no text.
        """
        request_id = self._next_request_id()
        try:
            path = _chosen_path(self._dialog(0, allow_multiple=False, file_types=IMPORT_FILE_TYPES))
        except Exception:
            return self._dialog_failure(request_id)
        if path is None:
            return BridgeResult.ok(request_id, {"cancelled": True}).to_dict()
        file_name = Path(path).name
        # The bounded shared read, then the strict JSON parse of D1, so each file problem has its own kind
        try:
            text = read_text(Path(path), MAX_DOCUMENT_BYTES)
            json.loads(text, parse_constant=refuse_json_constant, object_pairs_hook=unique_json_keys)
        except UserFileError as error:
            return self._file_refusal(request_id, error.reason, file_name)
        except (ValueError, RecursionError):
            return self._file_refusal(request_id, "not_json", file_name)
        selection_id = secrets.token_hex(16)
        with self._import_lock:
            self._pending = (selection_id, text, file_name)
        return self._previews(request_id, "preview_mission_map_import", {"text": text},
                              {"selection_id": selection_id, "file_name": file_name})

    def preview_import(self, selection_id: object) -> dict[str, Any]:
        """Run both previews again for the pending file (Preview again); the text is the text that was read."""
        request_id = self._next_request_id()
        pending = self._pending_text(selection_id)
        if pending is None:
            return self._unknown_selection(request_id)
        return self._previews(request_id, "preview_mission_map_import", {"text": pending[0]},
                              {"selection_id": str(selection_id), "file_name": pending[1]})

    def import_plan(self, selection_id: object, mode: object, expected_revision: object,
                    confirmed: object) -> dict[str, Any]:
        """Import the pending file as one revision-guarded save; the answer has `revision` and `report`.

        A success drops the pending text. A refusal keeps it, so Preview again works. A change of the file on
        disk after the selection does not change what the admin reviewed.
        """
        pending = self._pending_text(selection_id)
        if pending is None:
            return self._unknown_selection(self._next_request_id())
        answer = self._invoke("import_mission_map_plan", {"text": pending[0], "mode": mode,
                                                          "expected_revision": expected_revision,
                                                          "confirmed": confirmed})
        if answer.get("success") is True:
            self._forget_import(str(selection_id))
        return answer

    def copy_sources(self) -> dict[str, Any]:
        """List the profiles that the picker offers; each keeps only ID, display name and mission folder.

        The bound profile is marked `self` and a profile without a mission link is marked `no_mission`; the page
        shows both disabled. No other profile field and no plan state reaches the page (PO-91-1 a).
        """
        answer = self._invoke_unbound("list_profiles", {})
        if answer.get("success") is not True or not isinstance(answer.get("value"), list):
            return answer
        profiles = [{"profile_id": record.get("profile_id"), "display_name": record.get("display_name"),
                     "mission_root": record.get("mission_root"),
                     "disabled": "self" if record.get("profile_id") == self._profile_id else
                     ("no_mission" if not record.get("mission_root") else None)}
                    for record in answer["value"] if isinstance(record, dict)]
        return {**answer, "value": {"profiles": profiles}}

    def preview_copy(self, source_profile_id: object) -> dict[str, Any]:
        """Run both copy previews for one source profile; the source plan is only read."""
        return self._previews(self._next_request_id(), "preview_mission_map_copy",
                              {"source_profile_id": source_profile_id}, {"source_profile_id": source_profile_id})

    def copy_plan(self, source_profile_id: object, mode: object, expected_revision: object,
                  confirmed: object) -> dict[str, Any]:
        """Copy the plan of the source profile as one revision-guarded save; the answer has `revision`, `report`."""
        return self._invoke("copy_mission_map_plan", {"source_profile_id": source_profile_id, "mode": mode,
                                                      "expected_revision": expected_revision,
                                                      "confirmed": confirmed})

    def _previews(self, request_id: str, method: str, parameters: dict[str, Any],
                  extra: dict[str, Any]) -> dict[str, Any]:
        """Run the Replace and the Merge previews with one destination revision; else once more, then stale.

        A refusal is the same for both modes, so the first one is the answer. It gets `extra` added: the selection
        ID and the file name, or the source profile. A plan limit of one mode stays in its report, with the bridge
        text of the commit refusal.
        """
        for _attempt in range(2):
            previews: dict[str, Any] = {}
            for mode in MODES:
                answer = self._invoke(method, {**parameters, "mode": mode})
                if answer.get("success") is not True:
                    error = answer.get("error") or {}
                    details = {**(error.get("details") or {}), **extra}
                    return {**answer, "request_id": request_id, "error": {**error, "details": details}}
                previews[mode] = _with_limit_text(answer["value"])
            if previews["replace"].get("destination_revision") == previews["merge"].get("destination_revision"):
                return BridgeResult.ok(request_id, {**extra, "previews": previews}).to_dict()
        return BridgeResult.failed(request_id, ErrorCode.REVISION_CONFLICT, "The saved map plan changed.", True,
                                   {"reason": "stale_revision", **extra}).to_dict()

    def _dialog(self, index: int, **options: Any) -> object:
        """Open the open (0) or save (1) dialog on this editor window; an unbound editor raises."""
        if self._window is None or self._dialogs is None:
            raise RuntimeError("unbound editor")
        return self._window.create_file_dialog(self._dialogs[index], **options)

    def _pending_text(self, selection_id: object) -> tuple[str, str] | None:
        """Return the text and the file name of the pending file, or None for another or no selection."""
        with self._import_lock:
            pending = self._pending
        if pending is None or not isinstance(selection_id, str) or not secrets.compare_digest(pending[0], selection_id):
            return None
        return pending[1], pending[2]

    def _forget_import(self, selection_id: str | None = None) -> None:
        """Drop the pending file: after a successful import, or at the close of the window (None)."""
        with self._import_lock:
            if selection_id is None or (self._pending is not None and self._pending[0] == selection_id):
                self._pending = None

    @staticmethod
    def _file_refusal(request_id: str, kind: str, file_name: str) -> dict[str, Any]:
        """Return the refusal of a file that cannot be imported: unreadable, too large, not UTF-8 or not JSON."""
        code = ErrorCode.PATH_INVALID if kind == "unreadable" else ErrorCode.INVALID_REQUEST
        return BridgeResult.failed(request_id, code, "The chosen file cannot be imported.", False,
                                   {"reason": "import_file_refused", "kind": kind, "file_name": file_name}).to_dict()

    @staticmethod
    def _unknown_selection(request_id: str) -> dict[str, Any]:
        """Return the refusal of a selection ID that this editor no longer holds."""
        return BridgeResult.failed(request_id, ErrorCode.INVALID_REQUEST, "The chosen file is no longer known.",
                                   False, {"reason": "import_selection_unknown"}).to_dict()

    @staticmethod
    def _dialog_failure(request_id: str) -> dict[str, Any]:
        """Return the fixed failure of a dialog exception; the page shows the shared internal-failure text."""
        return BridgeResult.failed(request_id, ErrorCode.INTERNAL_FAILURE,
                                   "The map editor could not open the file dialog.").to_dict()


def _with_limit_text(report: dict[str, Any]) -> dict[str, Any]:
    """Add the bridge text of the plan limit to a report whose mode the limit refuses (XFER-31)."""
    if report.get("refusal") is None:
        return report
    text = REFUSAL_TEXTS[("plan_limit", "destination")].format(objects=report.get("result_object_count"),
                                                               vertices=report.get("result_vertex_count"))
    return {**report, "refusal_text": text}
