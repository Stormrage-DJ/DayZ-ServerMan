"""Name the preview, Apply, restore and recovery owner methods of the map editor (leaf 8.9, A11).

Both previews are synchronous reads of the owner session. Apply and the two
restores are lane operations, and the 8.5 recover handler lifts editor
recovery blocks. Construction registers nothing; the handler table takes
`handlers()`.

Refusals keep the backend texts unchanged: the Product Owner texts of
`NoSavedPlan` and `PreviewStale` (evidence 8.7), the 8.8 restore texts and the
6.11 gate text. They never become the general `REVISION_CONFLICT` or request
text.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from ..bridge.contracts import ErrorCode
from ..bridge.facade import BridgeHandler
from ..domain.mission_map_restore_texts import INVALID_MODE, INVALID_MODE_TEXT, LANE_KINDS
from .mission_map_apply_coordinator import MissionMapApplyCoordinator, apply_error
from .mission_map_coordinator_errors import exact_parameters, refusal
from .mission_map_recovery_coordinator import MissionMapRecoveryCoordinator
from .mission_map_restore_coordinator import MissionMapRestoreCoordinator

# Exact request fields of the two previews
PREVIEW_FIELDS = {"profile_id"}
RESTORE_PREVIEW_FIELDS = {"profile_id", "mode"}


class ApplyPreview(Protocol):
    """The apply preview of 8.6: one review of the saved plan."""

    def preview(self, profile_id: object, expected_revision: int | None = None) -> dict[str, Any]:
        """Return the preview answer with its fingerprint."""
        ...


class RestorePreview(Protocol):
    """The restore preview of 8.8: one review of a restore mode."""

    def preview(self, profile_id: object, mode: object) -> dict[str, Any]:
        """Return the restore answer with its fingerprint."""
        ...


class MissionMapPublicationCoordinator:
    """The five owner methods of the editor publication; every one is owner-only (A11)."""

    def __init__(self, preview: ApplyPreview, restore: RestorePreview, apply: MissionMapApplyCoordinator,
                 restore_lane: MissionMapRestoreCoordinator, recovery: MissionMapRecoveryCoordinator) -> None:
        """Keep both previews, the two lane coordinators and the editor recovery coordinator."""
        self.preview, self.restore = preview, restore
        self.apply, self.restore_lane, self.recovery = apply, restore_lane, recovery

    def handlers(self) -> Mapping[str, BridgeHandler]:
        """Return the bridge methods in handler-table order: Apply preview and Apply, restore, recovery."""
        return {
            "preview_mission_map": self.preview_mission_map,
            "apply_mission_map": self.apply.apply_mission_map,
            "preview_mission_map_restore": self.preview_mission_map_restore,
            "restore_mission_map": self.restore_lane.restore_mission_map,
            "inspect_mission_map_recovery": self.recovery.inspect_mission_map_recovery,
        }

    def preview_mission_map(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Review the saved plan of the profile; the preview writes nothing and takes no lock."""
        exact_parameters(parameters, PREVIEW_FIELDS)
        return _reviewed(lambda: self.preview.preview(parameters["profile_id"]))

    def preview_mission_map_restore(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Review Restore original or Restore previous state; an unknown mode gets the 8.8 text."""
        exact_parameters(parameters, RESTORE_PREVIEW_FIELDS)
        mode = parameters["mode"]
        if not isinstance(mode, str) or mode not in LANE_KINDS:
            raise refusal(ErrorCode.INVALID_REQUEST, INVALID_MODE, INVALID_MODE_TEXT)
        return _reviewed(lambda: self.restore.preview(parameters["profile_id"], mode))


def _reviewed(review: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one review; a failure becomes the refusal that the lane operation of the same review gives."""
    try:
        return review()
    except Exception as error:
        # apply_error keeps NoSavedPlan, PreviewStale, the 6.11 gate and the 8.8 refusals with their own text
        raise apply_error(error) from error
