"""Expose the single accepted desktop action for opening a profile's editor."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..adapters.windows.window_work_area import WindowGeometryError
from ..bridge.contracts import BridgeResult, ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.profiles import ProfileValidationError
from ..repositories.profiles import ProfileNotFound


class MapEditorHostMethods:
    """Keep native window opening outside the bridge and command surfaces."""

    def _set_map_editor_opener(self, opener: Callable[[object], dict[str, bool]]) -> None:
        """Bind the registry's opener after the main window exists."""
        self._map_editor_opener = opener

    def open_map_editor(self, profile_id: object) -> dict[str, Any]:
        """Open or focus a profile editor with a safe, versioned host envelope."""
        # Share the host request sequence across native and bridge calls
        with self._lock:
            self._counter += 1
            request_id = f"webview-{self._counter}"
        try:
            if self._map_editor_opener is None:
                raise RuntimeError("editor opener is unbound")
            return BridgeResult.ok(request_id, self._map_editor_opener(profile_id)).to_dict()
        except ApplicationCallError as error:
            return BridgeResult.failed(request_id, error.code, error.safe_message,
                                       error.retryable, error.details).to_dict()
        except ProfileNotFound:
            return BridgeResult.failed(request_id, ErrorCode.NOT_FOUND,
                                       "The profile was not found.").to_dict()
        except ProfileValidationError:
            return BridgeResult.failed(request_id, ErrorCode.INVALID_REQUEST,
                                       "The profile identifier is invalid.").to_dict()
        except WindowGeometryError as error:
            # The geometry adapter supplies only bounded, operator-safe refusal messages
            return BridgeResult.failed(request_id, ErrorCode.INTERNAL_FAILURE, str(error)).to_dict()
        except Exception:
            # Native or storage failures must never reveal paths or exception text
            return BridgeResult.failed(request_id, ErrorCode.INTERNAL_FAILURE,
                                       "The map editor could not be opened.").to_dict()
