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

    def test_unassigned_is_lazy_independent_and_display_only(self) -> None:
        """Null category stays separate from Tier1 and a real tier named Unassigned."""
        from tests.test_mission_map_png import rows_of
        from dayz_serverman.domain.mission_map_png import tier_png
        import base64
        png = base64.b64encode(tier_png(rows_of(4, 4, 4, {(0, 0): 1, (3, 3): 2}), None)).decode("ascii")
        self.assertEqual(run_editor_harness(common() + "const zeroPng=" + repr(png) + ";" + r'''
tierAnswers.null=()=>({success:true,value:{tier:null,columns:4,rows:4,world_width:1000,world_height:1000,areaflags_sha256:"f".repeat(64),png:zeroPng}});
await load(value());await until(()=>window.ServerManMapTiers.loaded().length===2,"named layers loaded");
const eye=()=>document.querySelector('[data-map-unassigned="true"]');
check(eye().getAttribute("aria-pressed")==="false"&&tierReads.length===2,"Unassigned starts hidden without a read");
const before=JSON.stringify(mapEditorState.model.snapshot()),history=mapEditorState.model.status().history.length;
const bare=pixel(625,625);eye().click();await until(()=>tierReads.length===3&&!text("map-tiers-status"),"zero layer loaded");
check(tierReads[2][1]===null&&eye().getAttribute("aria-pressed")==="true","null identity is read and enabled");
const filled=pixel(625,625);check(filled[0]>bare[0],"zero cells draw grey");
near(pixel(125,125),terrain,1,"real Tier1 remains individually hidden");
for(const opacity of [0,50,100]){const slider=document.getElementById("map-tiers-opacity");slider.value=opacity;slider.dispatchEvent(new Event("input"));
 near(pixel(625,625),bare.map((v,i)=>Math.round(v+(filled[i]-v)*opacity/100)),2,"original opacity controls zero mask");}
document.getElementById("map-tiers-toggle").click();near(pixel(625,625),bare,1,"master hides zero mask");
document.getElementById("map-tiers-toggle").click();near(pixel(625,625),filled,1,"master restores individual choice");
check(JSON.stringify(mapEditorState.model.snapshot())===before&&mapEditorState.model.status().history.length===history&&saved.length===0,"display creates no model edit/history/save");
tierAnswers.Unassigned=()=>tierAnswers.Tier2();
await load(value(plan(),capabilities(['Tier1','Unassigned'])));await until(()=>window.ServerManMapTiers.loaded().length===2&&!text('map-tiers-status'),'real Unassigned name and null reload');
const realEye=document.querySelector('[data-map-tier-control="Unassigned"][data-map-unassigned="false"]');
check(realEye&&eye()!==realEye&&eye().getAttribute('aria-pressed')==='true','null choice survives reload independently of the real name');
realEye.click();check(eye().getAttribute('aria-pressed')==='true','real named tier cannot toggle null display identity');

'''), "PASS")

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
const eye=tier=>[...document.querySelectorAll('[data-map-tier-control]')].find(button=>button.dataset.mapTierControl===tier);
near(pixel(125,125),terrain,1,"Tier1 starts hidden without losing its mask");
check(eye("Tier1").getAttribute("aria-pressed")==="false"&&!document.querySelector('#map-tiers-legend input'),"individual tiers use eye buttons");
window.ServerManMapWorkspace.open("navigation");await wait();
eye("Tier1").focus();eye("Tier1").click();
check(document.activeElement===eye("Tier1"),"tier toggles retain keyboard focus");
const first=pixel(125,125),second=pixel(875,875);
check(first[0]>terrain[0]&&Math.abs(first[2]-first[0])<15,`Tier1 real mask is grey: ${first}`);
check(second[0]>first[0]&&Math.abs(second[2]-second[0])<15,`Tier2 real mask is white: ${second}`);
eye("Tier2").click();near(pixel(875,875),terrain,1,"individual Tier2 eye hides only its mask");
near(pixel(125,125),first,1,"Tier1 remains shown independently");eye("Tier2").click();
near(pixel(875,125),terrain,1,"cell (3, 0) has no tier, so columns are not mirrored");
near(pixel(125,875),terrain,1,"cell (0, 3) has no tier, so north is up");
const legend=[...document.querySelectorAll("#map-tiers-legend li")];
check(legend.map((item)=>item.dataset.mapTier).join()==="Tier1,Tier2,Unassigned"&&legend.every((item)=>item.querySelector("canvas.map-tier-swatch")),"legend names each tier with its swatch");
check(text("map-tiers-status")==="","no reason while the tiers show");
const status=text("map-status"),draft=text("map-draft-changes"),undo=document.getElementById("map-undo").disabled;
const toggle=document.getElementById("map-tiers-toggle");
const opacity=document.getElementById("map-tiers-opacity");
check(opacity.value==="100"&&opacity.min==="0"&&opacity.max==="100"&&opacity.step==="10","opacity range and preserved default");
for(let percent=0;percent<=100;percent+=10) {
  opacity.value=String(percent);opacity.dispatchEvent(new Event("input",{bubbles:true}));
  const expected=terrain.map((channel,index)=>Math.round(channel+(first[index]-channel)*percent/100));
  near(pixel(125,125),expected,2,`tier opacity ${percent}`);
  near(pixel(625,625),terrain,1,"opacity leaves the base terrain unchanged");
  check(text("map-tiers-opacity-value")===`${percent}%`,"visible opacity value");
}
opacity.value="40";opacity.dispatchEvent(new Event("input",{bubbles:true}));
eye("Tier1").click();
toggle.click();await wait();
check(!toggle.checked&&document.getElementById("map-tiers-legend").hidden,"toggle hides the legend");
check(JSON.stringify(window.ServerManMapRaster.drawn())===JSON.stringify(["terrain"]),"toggle hides the overlay");
near(pixel(125,125),terrain,1,"hidden overlay shows the terrain");
check(text("map-tiers-status")==="Original tiers are hidden.","hidden state is stated");
toggle.click();await wait();
check(tierReads.length===2&&window.ServerManMapRaster.drawn().includes("tiers"),"showing again needs no new read");
check(opacity.value==="40"&&text("map-tiers-opacity-value")==="40%","visibility toggle retains opacity");
check(eye("Tier1").getAttribute("aria-pressed")==="false","master preserves individual hidden state");
near(pixel(125,125),terrain,1,"master on never re-enables individually hidden Tier1");
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
check(document.querySelector('#map-tiers-legend li[data-map-tier="Tier2"] .map-tier-name').textContent==="Tier2: A mission file could not be read.","failure in the legend");
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
