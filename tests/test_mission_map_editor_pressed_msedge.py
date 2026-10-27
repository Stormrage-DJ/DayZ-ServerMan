"""Verify the pressed style of every editor toggle and its limit to the editor document (UI-UX 10:54:32, QA-7.3-F1)."""

from __future__ import annotations

import re
import unittest

from tests.mission_map_display_harness import common
from tests.test_mission_map_editor_background_msedge import STEPS
from tests.test_mission_map_editor_encounters_msedge import ENCOUNTERS
from tests.ui_harness_support import EDGE, FRONTEND, run_editor_harness
from dayz_serverman.host.assets import compose_shell_html


# A pressed toggle has the inset bar and the action border; an unpressed one has no bar
PRESSED = r'''
const probe=document.createElement("div");probe.style.color="var(--color-action)";document.getElementById("map-editor").append(probe);
const action=getComputedStyle(probe).color;probe.remove();
const bar=(element)=>{const style=getComputedStyle(element);return style.boxShadow.includes("inset")&&style.borderTopColor===action};
const plain=(element)=>getComputedStyle(element).boxShadow==="none";
'''


class MissionMapEditorPressedScopeTests(unittest.TestCase):
    """The rule stays in the editor styles; the main window composition does not get it."""

    def test_rule_is_scoped_to_the_editor_document(self) -> None:
        """The editor rule names #map-editor, and no stylesheet of the main window holds it."""
        styles = (FRONTEND / "map_editor_canvas.css").read_text(encoding="utf-8")
        self.assertIn('#map-editor .button[aria-pressed="true"] { border-color: var(--color-action); '
                      'box-shadow: inset 0 -3px 0 var(--color-action); }', styles)
        self.assertNotIn("#map-tool-buttons .button[aria-pressed", styles)
        shell = compose_shell_html(FRONTEND)
        self.assertNotIn("#map-editor .button[aria-pressed", shell)
        self.assertIsNone(re.search(r"aria-pressed=\"true\"\][^{]*\{[^}]*inset 0 -3px", shell))


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorPressedEdgeTests(unittest.TestCase):
    """Use synthetic plans, fake display reads and isolated headless profiles."""

    def test_tools_and_list_toggles_show_the_bar(self) -> None:
        """Tool, Visible and Locked buttons show the bar only while pressed; the selected name marks its row."""
        self.assertEqual(run_editor_harness(ENCOUNTERS + PRESSED + r'''
const start=plan();start.objects=[circle("C1",500,500,100)];await loadWith(start);
const control=(name)=>document.querySelector(`[data-map-control="C1:${name}"]`);
tool("circle");check(bar(document.getElementById("map-tool-circle"))&&plain(document.getElementById("map-tool-select")),"pressed tool only");
// The compact object row keeps borders transparent, so a pressed row toggle shows only the action-coloured bar.
const rowBar=(element)=>{const shadow=getComputedStyle(element).boxShadow;return shadow.includes("inset")&&shadow.includes(action)};
check(rowBar(control("visible"))&&plain(control("locked"))&&plain(control("select")),"Visible pressed; Locked and the name not");
// The selected name is marked by the row's selection edge and bold text instead of the bar.
pick("C1");const row=control("select").closest(".map-layer");
check(control("select").getAttribute("aria-pressed")==="true"&&plain(control("select"))&&getComputedStyle(control("select")).fontWeight==="600"
  &&row.classList.contains("map-layer-selected")&&getComputedStyle(row).borderLeftColor!=="rgba(0, 0, 0, 0)","the selected item's row shows the selection");
control("visible").click();check(control("visible").getAttribute("aria-pressed")==="false"&&plain(control("visible")),"a hidden item has no bar on Visible");
control("locked").click();check(control("locked").getAttribute("aria-pressed")==="true"&&rowBar(control("locked")),"a locked item shows the bar on Locked");
const outside=document.createElement("button");outside.className="button";outside.setAttribute("aria-pressed","true");document.body.append(outside);
check(plain(outside),"a pressed button outside the editor document keeps the shared look");
'''), "PASS")

    def test_calibration_pick_buttons_show_the_bar(self) -> None:
        """The pick button of the point being picked shows the bar; the others do not."""
        self.assertEqual(run_editor_harness(common() + STEPS + PRESSED + r'''
await load(value(plan(),capabilities([])));await choose();
document.getElementById("map-calibrate").click();
const pick=(index)=>document.getElementById(`map-calibration-pick-${index}`);
check(pick(0).getAttribute("aria-pressed")==="true"&&bar(pick(0))&&plain(pick(1))&&plain(pick(2)),"only the active pick button");
pick(2).click();check(bar(pick(2))&&plain(pick(0)),"the bar follows the pick");
'''), "PASS")


if __name__ == "__main__":
    unittest.main()
