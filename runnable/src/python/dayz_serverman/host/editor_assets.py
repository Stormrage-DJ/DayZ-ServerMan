"""Compose the independent editor document from bundled offline assets."""

from __future__ import annotations

from pathlib import Path

from ..adapters.windows.shared_files import read_text_shared
from .assets import SCRIPT_MARKER, STYLE_MARKER


# Include implemented editor styles in accepted order; the canvas styles hold the editor tier tokens
EDITOR_STYLES = ("tokens.css", "controls.css", "styles.css", "map_editor.css", "map_editor_canvas.css")
# Shared wording has no shell lifecycle dependency; editor modules use unique globals
EDITOR_SCRIPTS = (
    "operation_labels.js", "diagnostic_labels.js", "host_sentences.js", "operation_messages.js",
    "map_editor_dom.js", "map_editor_wording.js", "map_editor_bridge.js", "map_editor_layout.js",
    "map_editor_coordinates.js", "map_editor_viewport.js", "map_editor_viewport_render.js",
    "map_editor_viewport_input.js", "map_editor_viewport_ui.js", "map_editor_bounds_plan.js",
    "map_editor_bounds_save.js", "map_editor_bounds.js", "map_editor_raster.js",
    "map_editor_tiers.js", "map_editor_background_parts.js", "map_editor_calibration_math.js",
    "map_editor_background.js", "map_editor_calibration.js", "map_editor_model.js", "map_editor_history.js",
    "map_editor_layers.js", "map_editor_outline.js", "map_editor_vector.js",
    "map_editor_plan_save.js", "map_editor_autosave.js", "map_editor_tools.js", "map_editor_drag.js",
    "map_editor_properties.js", "map_editor_inspect_wording.js", "map_editor_inspect_read.js",
    "map_editor_tier_control.js", "map_editor_inspect.js", "map_editor_keys.js",
    "map_editor_set_aside.js", "map_editor_close_dialog.js", "map_editor_close.js", "map_editor_app.js",
)


def compose_editor_html(frontend_root: Path) -> str:
    """Inline editor assets while requiring exactly one marker of each kind."""
    root = frontend_root.resolve(strict=True)
    # Read bundled assets through the same shared-read boundary as runtime files
    document = read_text_shared(root / "editor.html", encoding="utf-8")
    # Reject ambiguous documents before composing any script or stylesheet
    if document.count(STYLE_MARKER) != 1 or document.count(SCRIPT_MARKER) != 1:
        raise ValueError("editor document must contain exactly one style and script marker")
    styles = "\n".join(read_text_shared(root / name, encoding="utf-8") for name in EDITOR_STYLES)
    scripts = "\n".join(read_text_shared(root / name, encoding="utf-8") for name in EDITOR_SCRIPTS)
    return document.replace(STYLE_MARKER, f"<style>\n{styles}\n</style>").replace(
        SCRIPT_MARKER, f"<script>\n{scripts}\n</script>",
    )
