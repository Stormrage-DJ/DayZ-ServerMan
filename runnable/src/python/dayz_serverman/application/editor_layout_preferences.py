"""Validate the narrow editor layout preference subtree."""
from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.models import RecordState

DIMENSIONS = {"navigation_width": (200, 480), "inspector_width": (320, 640), "report_height": (120, 600)}
FLAGS = {"navigation_open", "inspector_open", "report_open"}
REGIONS = {"navigation", "inspector", "report"}


def valid_layout_value(key, value):
    """Require finite bounded integer dimensions and exact flag types."""
    if key in DIMENSIONS:
        low, high = DIMENSIONS[key]
        return type(value) is int and low <= value <= high
    if key in FLAGS:
        return type(value) is bool
    return key == "last_explicit_opened_region" and isinstance(value, str) and value in REGIONS


class EditorLayoutPreferences:
    """Read independently of stale profile choices; save additive patches."""

    def __init__(self, store):
        """Use the coordinator shared transaction boundary."""
        self.store = store

    def get(self, parameters):
        """Return sanitized layout fields independently of profile preferences."""
        if parameters:
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "layout parameters are invalid")
        inspection = self.store.repository.inspect()
        raw = inspection.document.fields.get("editor_layout", {}) if inspection.state == RecordState.VALID and inspection.document else {}
        layout = {key: value for key, value in raw.items() if valid_layout_value(key, value)} if isinstance(raw, dict) else {}
        return {"editor_layout": layout, "storage_status": inspection.state.value}

    def save(self, parameters):
        """Validate and merge a partial layout patch."""
        patch = parameters.get("patch")
        if set(parameters) != {"patch"} or not isinstance(patch, dict) or not patch or any(
            not valid_layout_value(key, value) for key, value in patch.items()
        ):
            raise ApplicationCallError(ErrorCode.INVALID_REQUEST, "layout patch is invalid")
        def merge(fields):
            """Preserve unrelated fields during the serialized merge."""
            old = fields.get("editor_layout", {})
            fields["editor_layout"] = {**(old if isinstance(old, dict) else {}), **patch}
            return fields
        self.store.update(merge)
        return {"editor_layout": dict(patch)}
