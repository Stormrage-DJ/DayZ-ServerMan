"""Verify the original tiers overlay in composed Edge: draw order, display-only toggle and missing data (3.15)."""

from __future__ import annotations

import unittest

from tests.mission_map_display_harness import common
from tests.ui_harness_support import EDGE, run_editor_harness

# A dropped answer leaves no trace, so a check of it must wait until the late decode has ended.
# Decode the same mask while message hops keep virtual time still; a bare await lets the time budget run out.
SETTLE = r'''
const settleDecode=async(png)=>{const probe=new Image();let ended=false;probe.src=`data:image/png;base64,${png}`;
  probe.decode().then(()=>{ended=true;},()=>{ended=true;});
  for(let step=0;step<200000&&!ended;step++)await hop();
  check(ended,"the probe decode ended");
  for(let step=0;step<2000;step++)await hop();await wait();};
'''

@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorTiersEdgeTests(unittest.TestCase):
    """Use synthetic masks and isolated headless profiles only."""

    def test_tiers_draw_above_terrain_below_grid_and_toggle_changes_display_only(self) -> None:
        """Each tier reads once by mission key; the toggle hides and shows without a save or an edit."""
        self.assertEqual(run_editor_harness(common() + r'''
await load(value());
await until(()=>window.ServerManMapTiers.loaded().length===2,"both tier layers loaded");
check(JSON.stringify(tierReads)===JSON.stringify([["k".repeat(32),"Tier1"],["k".repeat(32),"Tier2"]]),"one read per tier in value-flag order");
check(JSON.stringify(window.ServerManMapRaster.drawn())===JSON.stringify(["terrain","tiers"]),"tiers drawn above the terrain");
const raster=document.getElementById("map-raster"),vector=document.getElementById("map-vector");
check(raster.compareDocumentPosition(vector)&Node.DOCUMENT_POSITION_FOLLOWING,"grid and bounds line lie above the raster");
check(vector.querySelectorAll(".map-grid-lines line").length===22&&document.getElementById("map-terrain-surface"),"grid and bounds line stay");
near(pixel(625,625),terrain,1,"cell without a tier shows the terrain surface");
const first=pixel(125,125),second=pixel(875,875);
check(first[2]-first[0]>40,`Tier1 tint is blue: ${first}`);
check(second[1]-second[0]>40,`Tier2 tint is green: ${second}`);
near(pixel(875,125),terrain,1,"cell (3, 0) has no tier, so columns are not mirrored");
near(pixel(125,875),terrain,1,"cell (0, 3) has no tier, so north is up");
const legend=[...document.querySelectorAll("#map-tiers-legend li")];
check(legend.map((item)=>item.dataset.mapTier).join()==="Tier1,Tier2"&&legend.every((item)=>item.querySelector("canvas.map-tier-swatch")),"legend names each tier with its swatch");
check(text("map-tiers-status")==="","no reason while the tiers show");
const status=text("map-status"),draft=text("map-draft-changes"),undo=document.getElementById("map-undo").disabled;
const toggle=document.getElementById("map-tiers-toggle");
toggle.click();await wait();
check(!toggle.checked&&document.getElementById("map-tiers-legend").hidden,"toggle hides the legend");
check(JSON.stringify(window.ServerManMapRaster.drawn())===JSON.stringify(["terrain"]),"toggle hides the overlay");
near(pixel(125,125),terrain,1,"hidden overlay shows the terrain");
check(text("map-tiers-status")==="Original tiers are hidden.","hidden state is stated");
toggle.click();await wait();
check(tierReads.length===2&&window.ServerManMapRaster.drawn().includes("tiers"),"showing again needs no new read");
check(saved.length===0&&text("map-status")===status&&text("map-draft-changes")===draft&&document.getElementById("map-undo").disabled===undo,
  "the toggle saves nothing and makes no draft change");
check(!document.querySelector("[style]"),"no inline style attributes");
''', budget=8000), "PASS")

    def test_missing_areaflags_budget_refusal_and_failed_tier_keep_the_plan_usable(self) -> None:
        """A mission without a usable areaflags.map shows the reason; refusals never block the grid or bounds."""
        self.assertEqual(run_editor_harness(common() + r'''
await load(value(plan(),capabilities([],[{operation:"detect_bounds",reason:"areaflags_missing"},
  {operation:"tier_layer",reason:"areaflags_missing"},{operation:"tier_writes",reason:"areaflags_missing"}])));
check(tierReads.length===0,"no read without a usable areaflags.map");
check(text("map-tiers-status")==="This mission has no areaflags.map, so its original tiers cannot be shown.","missing file reason");
check(document.getElementById("map-tiers-legend").hidden,"no legend without layers");
check(JSON.stringify(window.ServerManMapRaster.drawn())===JSON.stringify(["terrain"])&&!document.getElementById("map-fit").disabled,
  "terrain and navigation stay usable");
tierAnswers.Tier1=()=>refuse("UNSUPPORTED_SNAPSHOT_CONTENT",{reason:"over_budget",limit:6291456,size:7000000,unit:"bytes"});
await load(value());
await until(()=>text("map-tiers-status").startsWith("The original tiers"),"budget text shown");
check(text("map-tiers-status")==="The original tiers of this mission are too large to show on the map. Inspect still shows the tier of each point.","budget text");
check(tierReads.length===1&&window.ServerManMapTiers.loaded().length===0,"a budget refusal stops the reads");
check(!document.getElementById("map-bounds-fields").disabled&&!document.getElementById("map-fit").disabled,"plan stays usable");
tierAnswers.Tier1=()=>({success:true,value:{tier:"Tier1",columns:4,rows:4,world_width:1000,world_height:1000,
  areaflags_sha256:"f".repeat(64),byte_size:1,png:masks.Tier1}});
tierAnswers.Tier2=()=>refuse("STORAGE_FAILURE",{reason:"mission_file_unreadable"});
await load(value());
await until(()=>document.querySelector('#map-tiers-legend li[data-map-tier="Tier2"]')?.textContent.includes("could not"),"tier failure shown");
check(text("map-tiers-status")===""&&window.ServerManMapTiers.loaded().join()==="Tier1","other tiers still show");
check(document.querySelector('#map-tiers-legend li[data-map-tier="Tier2"]').textContent==="Tier2: A mission file could not be read.","failure in the legend");
'''), "PASS")

    def test_late_answer_after_a_new_load_is_dropped(self) -> None:
        """A read answer of an earlier load never draws over the current mission."""
        self.assertEqual(run_editor_harness(common() + SETTLE + r'''
let release;const pending=new Promise((resolve)=>{release=resolve;});
tierAnswers.Tier1=()=>pending;
await load(value());
check(tierReads.length===1,"first read started");
await load(value(plan(),capabilities([],[{operation:"tier_layer",reason:"tier_header_invalid"}])));
release({success:true,value:{tier:"Tier1",columns:4,rows:4,world_width:1000,world_height:1000,
  areaflags_sha256:"f".repeat(64),byte_size:1,png:masks.Tier1}});
await settleDecode(masks.Tier1);
check(window.ServerManMapTiers.loaded().length===0,"late answer dropped");
check(text("map-tiers-status")==="The tier map of this mission is not valid.","current mission reason stays");
check(JSON.stringify(window.ServerManMapRaster.drawn())===JSON.stringify(["terrain"]),"nothing drawn from the earlier load");
'''), "PASS")

    def test_cancelled_close_reads_the_missing_tier_layers_again(self) -> None:
        """QA-315-2: a close drops a running tier read; a cancelled close reads only the missing layers again."""
        self.assertEqual(run_editor_harness(common() + SETTLE + r'''
const answer=tierAnswers.Tier2;let release;const pending=new Promise((resolve)=>{release=resolve;});
tierAnswers.Tier2=()=>pending;
await load(value());
await until(()=>window.ServerManMapTiers.loaded().join()==="Tier1"&&tierReads.length===2,"Tier1 loaded and Tier2 read running");
check(await window.ServerManMapEditor.settleForClose()==="close","close settles");
release(answer());
await settleDecode(masks.Tier2);
check(JSON.stringify(window.ServerManMapTiers.loaded())===JSON.stringify(["Tier1"]),"the stopped Tier2 answer is dropped");
tierAnswers.Tier2=answer;
window.ServerManMapEditor.releaseClose();
await until(()=>window.ServerManMapTiers.loaded().length===2,"both layers loaded after the cancelled close");
check(JSON.stringify(tierReads.map((read)=>read[1]))===JSON.stringify(["Tier1","Tier2","Tier2"]),"only the missing layer is read again");
check(JSON.stringify(window.ServerManMapRaster.drawn())===JSON.stringify(["terrain","tiers"]),"tiers drawn above the terrain");
check(text("map-tiers-status")==="","no reason and no reading text");
'''), "PASS")


if __name__ == "__main__":
    unittest.main()
