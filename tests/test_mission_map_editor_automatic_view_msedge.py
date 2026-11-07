"""Verify automatic terrain viewing without creating or changing an editing plan."""

from __future__ import annotations

import unittest

from tests.mission_map_display_harness import common
from tests.ui_harness_support import EDGE, run_editor_harness


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapAutomaticViewTests(unittest.TestCase):
    """Use isolated browser profiles and the existing synthetic display fixtures."""

    def test_detected_bounds_show_original_tiers_without_setup_or_saving(self) -> None:
        """An empty plan opens its detected map, loads tier masks and closes without a draft."""
        self.assertEqual(run_editor_harness(common() + r'''
const detected={x_min:0,z_min:0,width:1000,height:1000};
const caps={...capabilities(),detected_bounds:detected};
const current=value(null,caps);current.state="MISSING";current.revision=null;
const original=JSON.stringify(current);
await load(current);
await until(()=>window.ServerManMapTiers.loaded().length===2,"original tiers loaded without plan");
check(JSON.stringify(view().snapshot().bounds)===JSON.stringify(detected),"detected dimensions drive viewport");
check(!document.getElementById("map-fit").disabled,"navigation available immediately");
check(document.getElementById("map-terrain-surface"),"terrain surface visible");
check(!document.querySelector("dialog"),"setup never blocks viewing");
check(text("map-status").includes("No setup is needed to view"),"viewer status explains automatic detection");
check(tierReads.length===2&&saved.length===0,"original tiers read without saving");
check(!window.ServerManMapBounds.typed().changed,"prefilled bounds are not unsaved work");
check(document.getElementById("map-bounds-width").value==="1000","detected width prefilled");
document.getElementById("map-show-setup").click();
check(text("map-bounds-confirmation").includes("in use for viewing"),"setup explains current dimensions");
window.ServerManMapSetup.close();
check(JSON.stringify(current)===original,"loaded snapshot is unchanged");
const closing=await window.ServerManMapEditor.settleForClose();
check(closing==="close"&&saved.length===0&&!document.querySelector("dialog"),"view-only close requires no save");
window.ServerManMapEditor.releaseClose();
'''), "PASS")

    def test_saved_bounds_win_and_absent_detection_stays_unavailable(self) -> None:
        """Saved manual geometry remains authoritative; invalid mission data never invents dimensions."""
        self.assertEqual(run_editor_harness(common() + r'''
const detected={x_min:0,z_min:0,width:16384,height:16384};
const savedBounds={x_min:-200,z_min:50,width:900,height:400};
const documentPlan=plan();documentPlan.terrain.bounds=savedBounds;
await load(value(documentPlan,{...capabilities(),detected_bounds:detected}));
check(JSON.stringify(view().snapshot().bounds)===JSON.stringify(savedBounds),"saved manual bounds win");
check(document.getElementById("map-bounds-width").value==="900","form retains saved values");
const caps=capabilities([], [{operation:"tier_layer",reason:"tier_header_invalid"}]);
await load(value(null,caps));
check(view().snapshot().bounds===null&&document.getElementById("map-fit").disabled,"unknown bounds remain unavailable");
check(!document.getElementById("map-terrain-surface"),"no invented terrain extent");
check(!text("map-status").includes("Viewing detected"),"failure status stays truthful");
check(saved.length===0,"neither load writes a plan");
'''), "PASS")
