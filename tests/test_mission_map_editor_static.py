"""Pin editor security, asset order, isolated globals and accessible document regions."""

from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from pathlib import Path

from dayz_serverman.host.assets import SCRIPT_MARKER, STYLE_MARKER, compose_shell_html
from dayz_serverman.host.editor_assets import EDITOR_SCRIPTS, EDITOR_STYLES, compose_editor_html


FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"


class MissionMapEditorStaticTests(unittest.TestCase):
    """Keep the editor offline and independent of the shell's lifecycle globals."""

    def test_csp_markers_regions_and_selectable_status(self) -> None:
        """Editor CSP is identical to the shell and landmarks expose the map layers."""
        document = (FRONTEND / "editor.html").read_text(encoding="utf-8")
        shell = (FRONTEND / "index.html").read_text(encoding="utf-8")
        policy = r'<meta http-equiv="Content-Security-Policy" content="([^"]+)"'
        self.assertEqual(re.findall(policy, document), re.findall(policy, shell))
        self.assertEqual(document.count(STYLE_MARKER), 1)
        self.assertEqual(document.count(SCRIPT_MARKER), 1)
        for region in ("map-identity", "map-toolbar", "map-viewport", "map-panel", "map-status"):
            self.assertIn(f'id="{region}"', document)
        self.assertIn('tabindex="0" aria-label="Map"', document)
        self.assertIn('<canvas id="map-raster"', document)
        self.assertIn('<svg id="map-vector"', document)
        self.assertIn('<details id="map-panel"', document)
        self.assertIn('id="map-announcement" role="status" aria-live="polite"', document)
        self.assertNotRegex(document, r'id="map-status"[^>]*aria-live')
        for slot in ("map-coordinates", "map-tool-status", "map-draft-changes", "map-fit", "map-coordinate-mode"):
            self.assertIn(f'id="{slot}"', document)
        self.assertNotRegex(document, r'\sstyle\s*=')
        styles = (FRONTEND / "map_editor.css").read_text(encoding="utf-8")
        self.assertIn("#map-viewport, #map-toolbar, #map-list { user-select: none; }", styles)
        self.assertNotIn("#map-status { user-select", styles)

    def test_assets_are_exactly_the_implemented_offline_inventory(self) -> None:
        """Asset order follows the accepted contract without future module placeholders."""
        self.assertEqual(EDITOR_STYLES, ("tokens.css", "controls.css", "styles.css", "map_editor.css",
                                         "map_editor_canvas.css", "map_editor_workspace.css", "map_editor_reports.css", "map_editor_toolbar.css"))
        self.assertEqual(EDITOR_SCRIPTS, ("operation_labels.js", "diagnostic_labels.js",
                         "host_sentences.js", "operation_messages.js", "operation_results.js",
                         "operation_failures.js", "busy_controls.js", "map_editor_dom.js",
                         "map_editor_wording.js", "map_editor_bridge.js", "map_editor_layout.js",
                         "map_editor_toolbar.js", "map_editor_workspace_dom.js", "map_editor_workspace.js", "map_editor_workspace_resize.js", "map_editor_workspace_preferences.js", "map_editor_setup.js", "map_editor_report_rows.js", "map_editor_reports.js",
                         "map_editor_coordinates.js", "map_editor_viewport.js", "map_editor_viewport_render.js",
                         "map_editor_viewport_input.js", "map_editor_viewport_ui.js", "map_editor_bounds_plan.js",
                         "map_editor_bounds_save.js", "map_editor_bounds.js", "map_editor_raster.js",
                         "map_editor_tiers.js", "map_editor_background_parts.js", "map_editor_calibration_math.js",
                         "map_editor_background.js", "map_editor_calibration.js", "map_editor_model.js", "map_editor_history.js",
                         "map_editor_layers.js", "map_editor_outline.js", "map_editor_vector.js",
                         "map_editor_plan_save.js", "map_editor_autosave.js", "map_editor_tools.js",
                         "map_editor_outline_draft.js", "map_editor_encounter_wording.js", "map_editor_encounters.js",
                         "map_editor_linked_loot.js", "map_editor_encounter_draw.js", "map_editor_encounter_tool.js", "map_editor_drag.js",
                         "map_editor_properties.js", "map_editor_encounter_properties.js", "map_editor_linked_loot_properties.js",
                         "map_editor_inspect_wording.js", "map_editor_inspect_read.js",
                         "map_editor_tier_control.js", "map_editor_inspect.js", "map_editor_abundance.js", "map_editor_keys.js",
                         "map_editor_fullscreen.js", "map_editor_set_aside.js", "map_editor_close_dialog.js", "map_editor_close.js",
                         "map_editor_preview_words.js", "map_editor_preview_dialog.js",
                         "map_editor_preview_controls.js", "map_editor_preview_apply.js",
                         "map_editor_preview_restore.js", "map_editor_preview_adopt.js", "map_editor_publish_words.js",
                         "map_editor_publish_status.js", "map_editor_follow.js", "map_editor_refresh.js",
                         "map_editor_adopt.js", "map_editor_publish.js", "map_editor_transfer_words.js",
                         "map_editor_transfer_review.js", "map_editor_transfer.js", "map_editor_transfer_import.js",
                         "map_editor_copy_source.js", "map_editor_copy.js", "map_editor_app.js"))
        composed = compose_editor_html(FRONTEND)
        self.assertNotIn(STYLE_MARKER, composed)
        self.assertNotIn(SCRIPT_MARKER, composed)
        self.assertNotIn('src="', composed)
        scripts = "\n".join((FRONTEND / name).read_text(encoding="utf-8") for name in EDITOR_SCRIPTS)
        self.assertIn(scripts, composed)
        for excluded in ("ServerManUi", "ServerManSections", "ServerManTransitions",
                         "request_shutdown", "fetch(", "new Worker(", "blob:"):
            self.assertNotIn(excluded, composed)
        self.assertTrue("ServerManUi" in compose_shell_html(FRONTEND))

    def test_editor_globals_and_top_level_names_are_unique_and_bounded(self) -> None:
        """Each editor file owns its accepted global and uses the map prefix."""
        expected = {"map_editor_dom.js": "ServerManMapDom", "map_editor_wording.js": "ServerManMapWording",
                    "map_editor_bridge.js": "ServerManMapBridge", "map_editor_layout.js": "ServerManMapLayout",
                    "map_editor_coordinates.js": "ServerManMapCoordinates", "map_editor_viewport.js": "ServerManMapViewport",
                    "map_editor_viewport_render.js": "ServerManMapViewportRender",
                    "map_editor_viewport_input.js": "ServerManMapViewportInput", "map_editor_viewport_ui.js": "ServerManMapViewportUi",
                    "map_editor_bounds_plan.js": "ServerManMapBoundsPlan",
                    "map_editor_bounds_save.js": "ServerManMapBoundsSave", "map_editor_bounds.js": "ServerManMapBounds",
                    "map_editor_raster.js": "ServerManMapRaster", "map_editor_tiers.js": "ServerManMapTiers",
                    "map_editor_background_parts.js": "ServerManMapBackgroundParts",
                    "map_editor_calibration_math.js": "ServerManMapCalibrationMath",
                    "map_editor_background.js": "ServerManMapBackground",
                    "map_editor_calibration.js": "ServerManMapCalibration",
                    "map_editor_model.js": "ServerManMapModel", "map_editor_history.js": "ServerManMapHistory",
                    "map_editor_layers.js": "ServerManMapLayers", "map_editor_outline.js": "ServerManMapOutline",
                    "map_editor_vector.js": "ServerManMapVector", "map_editor_plan_save.js": "ServerManMapPlanSave",
                    "map_editor_autosave.js": "ServerManMapAutosave",
                    "map_editor_tools.js": "ServerManMapTools", "map_editor_outline_draft.js": "ServerManMapOutlineDraft",
                    "map_editor_encounter_wording.js": "ServerManMapEncounterWording",
                    "map_editor_encounters.js": "ServerManMapEncounters", "map_editor_encounter_draw.js": "ServerManMapEncounterDraw",
                    "map_editor_linked_loot.js": "ServerManMapLinkedLoot",
                    "map_editor_linked_loot_properties.js": "ServerManMapLinkedLootProperties",
                    "map_editor_encounter_tool.js": "ServerManMapEncounterTool",
                    "map_editor_encounter_properties.js": "ServerManMapEncounterProperties",
                    "map_editor_drag.js": "ServerManMapDrag",
                    "map_editor_properties.js": "ServerManMapProperties", "map_editor_keys.js": "ServerManMapKeys",
                    "map_editor_fullscreen.js": "ServerManMapFullScreen",
                    "map_editor_inspect_wording.js": "ServerManMapInspectWording",
                    "map_editor_inspect_read.js": "ServerManMapInspectRead",
                    "map_editor_tier_control.js": "ServerManMapTierControl", "map_editor_inspect.js": "ServerManMapInspect",
                    "map_editor_abundance.js": "ServerManMapAbundance",
                    "map_editor_set_aside.js": "ServerManMapSetAside",
                    "map_editor_close_dialog.js": "ServerManMapCloseDialog", "map_editor_close.js": "ServerManMapClose",
                    "map_editor_preview_words.js": "ServerManMapPreviewWords",
                    "map_editor_preview_dialog.js": "ServerManMapPreviewDialog",
                    "map_editor_preview_controls.js": "ServerManMapPreviewControls",
                    "map_editor_preview_apply.js": "ServerManMapPreviewApply",
                    "map_editor_preview_restore.js": "ServerManMapPreviewRestore",
                    "map_editor_preview_adopt.js": "ServerManMapPreviewAdopt",
                    "map_editor_adopt.js": "ServerManMapAdopt",
                    "map_editor_publish_words.js": "ServerManMapPublishWords",
                    "map_editor_publish_status.js": "ServerManMapPublishStatus",
                    "map_editor_follow.js": "ServerManMapFollow", "map_editor_refresh.js": "ServerManMapRefresh",
                    "map_editor_publish.js": "ServerManMapPublish",
                    "map_editor_transfer_words.js": "ServerManMapTransferWords",
                    "map_editor_transfer_review.js": "ServerManMapTransferReview",
                    "map_editor_transfer.js": "ServerManMapTransfer",
                    "map_editor_transfer_import.js": "ServerManMapTransferImport",
                    "map_editor_copy_source.js": "ServerManMapCopySource", "map_editor_copy.js": "ServerManMapCopy",
                    "map_editor_app.js": "ServerManMapEditor"}
        expected.update({"map_editor_workspace_dom.js": "ServerManMapWorkspaceDom",
            "map_editor_toolbar.js": "ServerManMapToolbar",
                         "map_editor_workspace.js": "ServerManMapWorkspace",
                         "map_editor_workspace_resize.js": "ServerManMapWorkspaceResize"})
        names = []
        for filename, global_name in expected.items():
            script = (FRONTEND / filename).read_text(encoding="utf-8")
            self.assertEqual(re.findall(r"window\.(ServerMan\w+)\s*=", script), [global_name])
            self.assertLess(len(script.splitlines()), 300)
            # The model holds the one import undo step of 9.1 (Architect 2026-10-17 17:10:42 in evidence 9.1)
            self.assertLessEqual(len(script.splitlines()), 212 if filename == "map_editor_model.js" else 200)
            top = re.findall(r"^(?:const|let|function|async function)\s+(\w+)", script, re.MULTILINE)
            self.assertTrue(all(name.startswith("map") for name in top))
            names.extend(top)
        self.assertEqual(len(names), len(set(names)))

    def test_layout_keeps_display_state_local_and_uses_text_only(self) -> None:
        """Layout cannot store plan data, invent editing calls, or render backend markup."""
        script = (FRONTEND / "map_editor_layout.js").read_text(encoding="utf-8")
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "localStorage",
                          "sessionStorage", "pywebview", ".save(", "fetch("):
            self.assertNotIn(forbidden, script)
        self.assertIn('element.textContent = text', script)
        self.assertIn('font-variant-numeric: tabular-nums', (FRONTEND / "map_editor.css").read_text(encoding="utf-8"))

    def test_missing_or_duplicate_markers_refuse_composition(self) -> None:
        """Ambiguous document markers fail before asset composition."""
        original = (FRONTEND / "editor.html").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="editor_assets_") as temporary:
            root = Path(temporary)
            for marker in (STYLE_MARKER, SCRIPT_MARKER):
                for replacement in ("", marker + marker):
                    with self.subTest(marker=marker, replacement=replacement):
                        (root / "editor.html").write_text(original.replace(marker, replacement), encoding="utf-8")
                        with self.assertRaises(ValueError):
                            compose_editor_html(root)

    def test_missing_asset_refuses_offline_composition(self) -> None:
        """A broken offline bundle cannot silently lose an editor module."""
        with tempfile.TemporaryDirectory(prefix="editor_missing_asset_") as temporary:
            root = Path(temporary)
            for name in ("editor.html", *EDITOR_STYLES, *EDITOR_SCRIPTS):
                shutil.copyfile(FRONTEND / name, root / name)
            (root / "map_editor_bridge.js").unlink()
            with self.assertRaises(FileNotFoundError):
                compose_editor_html(root)

    def test_composition_preserves_utf8_and_text_newlines(self) -> None:
        """Shared reads preserve exact offline output for UTF-8 assets with CRLF."""
        with tempfile.TemporaryDirectory(prefix="editor_text_encoding_") as temporary:
            root = Path(temporary)
            document = "<html>é" + STYLE_MARKER + SCRIPT_MARKER + "\r\n</html>"
            (root / "editor.html").write_bytes(document.encode("utf-8"))
            for name in (*EDITOR_STYLES, *EDITOR_SCRIPTS):
                (root / name).write_bytes((name + " é\r\nnext\r\n").encode("utf-8"))
            # The oracle composes ordinary decoded text independently of the shared adapter
            styles = "\n".join(name + " é\nnext\n" for name in EDITOR_STYLES)
            scripts = "\n".join(name + " é\nnext\n" for name in EDITOR_SCRIPTS)
            expected = document.replace("\r\n", "\n").replace(STYLE_MARKER, f"<style>\n{styles}\n</style>")
            expected = expected.replace(SCRIPT_MARKER, f"<script>\n{scripts}\n</script>")
            self.assertEqual(compose_editor_html(root), expected)
