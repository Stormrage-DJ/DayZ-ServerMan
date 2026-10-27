"""Verify the background image in composed Edge: parts, alignment at known points, calibration and missing images (3.16)."""

from __future__ import annotations

import unittest

from tests.mission_map_display_harness import common
from tests.ui_harness_support import EDGE, run_editor_harness


# Choose the synthetic image, click known native points and type their coordinates
STEPS = r'''
const key="k".repeat(32);
const choose=async()=>{document.getElementById("map-background-choose").click();
  await until(()=>window.ServerManMapBackground.current().shown,"image shown");};
const clickAt=(x,z)=>{const element=document.getElementById("map-viewport"),box=element.getBoundingClientRect(),point=view().toScreen({x,z});
  const options={bubbles:true,cancelable:true,button:0,clientX:box.left+element.clientLeft+point.x,clientY:box.top+element.clientTop+point.y};
  element.dispatchEvent(new PointerEvent("pointerdown",{...options,pointerId:1}));element.dispatchEvent(new MouseEvent("click",options));};
const type=(index,first,second)=>{for(const [axis,number] of [["first",first],["second",second]]){
  const input=document.getElementById(`map-calibration-${index}-${axis}`);input.value=String(number);
  input.dispatchEvent(new Event("change",{bubbles:true}));}};
const calibration=()=>window.ServerManMapBackground.current().background.calibration;
const math=window.ServerManMapCalibrationMath;
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorBackgroundEdgeTests(unittest.TestCase):
    """Use one synthetic quadrant image, fake display reads and isolated headless profiles."""

    def test_chosen_image_joins_parts_fits_bounds_and_lies_below_tiers(self) -> None:
        """The quadrants land on the fitted bounds; tiers draw above the image; a save sends the token."""
        self.assertEqual(run_editor_harness(common() + STEPS + r'''
await load(value());
await until(()=>window.ServerManMapTiers.loaded().length===2,"tiers loaded");
await choose();
check(JSON.stringify(imageReads)===JSON.stringify([[key,"select"],[key,"token-1",1]]),"part 0 from the dialog, part 1 with the token");
const background=window.ServerManMapBackground.current().background;
check(background.path===null&&background.sha256===image.sha256&&background.byte_size===image.byte_size&&
  background.pixel_width===100&&background.pixel_height===100&&background.file_name==="map.png","reference without a path");
check(JSON.stringify(calibration())===JSON.stringify({a:10,b:0,c:0,d:0,e:-10,f:1000}),"a new image starts fitted to the bounds");
check(!document.getElementById("map-undo").disabled&&text("map-background-status")==="Map image: map.png","one undo action and the file name");
check(JSON.stringify(window.ServerManMapRaster.drawn())===JSON.stringify(["terrain","image","tiers"]),"image between terrain and tiers");
near(pixel(375,625),[255,0,0],2,"north-west red");
near(pixel(625,625),[0,255,0],2,"north-east green");
near(pixel(375,375),[0,0,255],2,"south-west blue");
near(pixel(625,375),[255,255,255],2,"south-east white");
// Tier1 starts hidden (README, Original tiers); its eye shows it so its tint can lie over the image
near(pixel(125,125),[0,0,255],2,"hidden Tier1 leaves the blue quadrant unchanged");
[...document.querySelectorAll("[data-map-tier-control]")].find((button)=>button.dataset.mapTierControl==="Tier1").click();
// The grey Tier1 tint lifts red and green equally; the blue image still shows through it
const tinted=pixel(125,125);
check(tinted[0]>60&&Math.abs(tinted[0]-tinted[1])<=2&&tinted[2]<250&&tinted[2]>tinted[0]+60,`Tier1 tint lies over the blue quadrant: ${tinted}`);
const opacity=document.getElementById("map-tiers-opacity");
opacity.value="0";opacity.dispatchEvent(new Event("input",{bubbles:true}));
near(pixel(125,125),[0,0,255],1,"zero tier opacity reveals the unchanged map image");
opacity.value="50";opacity.dispatchEvent(new Event("input",{bubbles:true}));
near(pixel(125,125),tinted.map((channel,index)=>Math.round((channel+[0,0,255][index])/2)),2,"half opacity blends tiers over the image");
opacity.value="100";opacity.dispatchEvent(new Event("input",{bubbles:true}));
document.getElementById("map-background-dim").click();await wait();
near(pixel(375,625),[142,19,25],3,"Dim image draws at alpha 0.5 over the terrain");
document.getElementById("map-bounds-save").click();
await until(()=>saved.length===1,"bounds save sent");
check(saved[0].selection==="token-1"&&saved[0].plan.background.sha256===image.sha256&&saved[0].plan.background.path===null,
  "a save names the chosen image by its token, never by a path");
check(window.ServerManMapBackground.selection(plan())===null,"no token for a plan without the chosen image");
''', budget=8000), "PASS")

    def test_three_point_calibration_aligns_known_points_and_moves_only_the_image(self) -> None:
        """Typed points solve a to f exactly; the image moves, the grid and the object list stay."""
        self.assertEqual(run_editor_harness(common() + STEPS + r'''
const document0=plan();document0.objects=[{id:"c1",name:"Loot circle 1",kind:"loot_circle",visible:true,locked:false,
  centre:{x:100,z:100},radius:50,tier:null,abundance:"rich"}];
await load(value(document0,capabilities([])));
document.getElementById("map-show-setup").click();check(document.querySelector("#map-setup[open]"),"setup is modal");
await choose();
const rows=text("map-list"),grid=document.getElementById("map-vector").innerHTML;
document.getElementById("map-calibrate").click();
await wait();check(!document.querySelector("dialog")&&!document.getElementById("map-calibration-workspace").hidden,"calibration releases the modal and exposes map picking");
check(!document.getElementById("map-calibration").hidden&&document.getElementById("map-calibrate").disabled,"calibration open");
check(text("map-calibration-guidance")==="Choose three points far apart and not on one line, such as grid crossings or map corners.","guidance");
check(text("map-calibration-0-first-label")==="X (m)","Both mode takes native X and Z");
clickAt(0,1000);clickAt(1000,1000);clickAt(0,0);
const picked=window.ServerManMapCalibration.state().points.map((point)=>point.pixel);
for(const [index,[x,y]] of [[0,0],[100,0],[0,100]].entries()){
  check(Math.hypot(picked[index].x-x,picked[index].y-y)<0.5,`pick ${index} maps through the fitted calibration: ${JSON.stringify(picked[index])}`);}
check(window.ServerManMapCalibration.state().picking===null,"three picks complete");
const typed=[[0,500],[500,500],[0,0]];
typed.forEach(([x,z],index)=>type(index,x,z));
const preview=window.ServerManMapCalibration.state().preview;
check(preview&&!document.getElementById("map-calibration-accept").disabled,"preview ready");
typed.forEach(([x,z],index)=>{const native=math.apply(preview,picked[index]);
  check(Math.abs(native.x-x)<1e-6&&Math.abs(native.z-z)<1e-6,`point ${index} solves exactly`);});
check(text("map-calibration-warning")==="","no warning for a straight north-up image");
check(text("map-calibration-beside-0")==="Grid X 0.0000, Z 5.0000","grid value beside the native value");
document.getElementById("map-calibration-accept").click();await wait();
check(document.getElementById("map-calibration").hidden&&document.querySelector("#map-setup[open]"),"calibration returns to setup");
check(document.activeElement.id==="map-calibrate","calibration returns focus to its setup entry");
window.ServerManMapSetup.close();check(!document.querySelector("dialog"),"inactive setup is removed");
const stored=calibration();
check(JSON.stringify(stored)===JSON.stringify(preview)&&Math.abs(stored.a-5)<0.05&&Math.abs(stored.e+5)<0.05,"accepted values stored");
near(pixel(125,375),[255,0,0],3,"red at the north-west of the half-size image");
near(pixel(375,375),[0,255,0],3,"green at its north-east");
near(pixel(125,125),[0,0,255],3,"blue at its south-west");
near(pixel(375,125),[255,255,255],3,"white at its south-east");
near(pixel(750,750),terrain,1,"terrain outside the image");
check(text("map-list")===rows&&document.getElementById("map-vector").innerHTML===grid,"objects and grid stay in place");
check(saved.length===0,"calibration changes only the draft");
''', budget=8000), "PASS")

    def test_refusals_warnings_cancel_escape_and_grid_mode(self) -> None:
        """Points on one line refuse; a mirror warns but does not block; Cancel and Escape keep the values."""
        self.assertEqual(run_editor_harness(common() + STEPS + r'''
await load(value(plan(),capabilities([])));
await choose();
const fitted=JSON.stringify(calibration());
document.getElementById("map-calibrate").click();
// One native Z gives one screen row, so the three picks lie on one image line exactly
clickAt(100,500);clickAt(300,500);clickAt(700,500);
[[0,0],[1,1],[2,2]].forEach(([x,z],index)=>type(index,x,z));
check(text("map-calibration-result")==="The three image points are on one line. Choose points far apart and not on one line.","collinear refusal");
check(document.getElementById("map-calibration-accept").disabled,"no accept for a refused solve");
document.getElementById("map-calibration-fit").click();
check(text("map-calibration-result")==="The preview fits the image to the terrain bounds. Accept stores it."&&
  !document.getElementById("map-calibration-accept").disabled,"fit to terrain bounds starts again");
check(window.ServerManMapCalibration.state().picking===null,"fit needs no picks");
document.getElementById("map-calibration-cancel").click();
check(JSON.stringify(calibration())===fitted&&document.getElementById("map-calibration").hidden,"cancel keeps the old values");
document.getElementById("map-calibrate").click();
clickAt(0,1000);clickAt(1000,1000);clickAt(0,0);
[[0,0],[1000,0],[0,1000]].forEach(([x,z],index)=>type(index,x,z));
check(text("map-calibration-warning")==="These points mirror the image. Check that X and Z are not swapped.","mirror warning");
check(!document.getElementById("map-calibration-accept").disabled,"a warning does not block");
document.getElementById("map-viewport").dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true,cancelable:true}));
check(!window.ServerManMapCalibration.state().active&&JSON.stringify(calibration())===fitted,"Escape cancels the calibration");
const mode=document.getElementById("map-coordinate-mode");mode.value="Grid";mode.dispatchEvent(new Event("change",{bubbles:true}));
document.getElementById("map-calibrate").click();
check(text("map-calibration-0-first-label")==="Grid X"&&text("map-calibration-0-second-label")==="Grid Z","Grid mode takes grid values");
type(0,0,5);
check(text("map-calibration-beside-0")==="Native X 0.000 m, Z 500.000 m","native value beside the grid value");
'''), "PASS")

    def test_missing_replaced_over_budget_and_discarded_images_keep_the_plan(self) -> None:
        """Each reason shows with the choose-again action; the reference, calibration, grid and overlays stay."""
        self.assertEqual(run_editor_harness(common() + STEPS + r'''
const reference={file_name:"map.png",sha256:image.sha256,byte_size:image.byte_size,pixel_width:100,pixel_height:100,
  path:null,calibration:{a:5,b:0,c:0,d:0,e:-5,f:500}};
const kept=" The grid, overlays and objects stay in place.";
for(const [answer,expected] of [[{available:false,reason:"missing"},"The map image file is missing."],
  [{available:false,reason:"replaced"},"The map image file was replaced by a different file."],
  [{available:false,reason:"over_budget",limit:33554432,size:41943040,unit:"bytes"},
    "This image is 40.0 MiB. The map editor shows images up to 32 MiB. Save a smaller JPEG or PNG file and choose it again."]]){
  imageAnswers.stored=()=>({success:true,value:answer});
  await load(value(plan(reference)));
  await until(()=>text("map-background-status")===expected+kept,`reason ${answer.reason}`);
  check(text("map-background-choose")==="Choose the image again"&&!document.getElementById("map-background-choose").disabled,"choose again");
  check(JSON.stringify(calibration())===JSON.stringify(reference.calibration),"calibration stays");
  await until(()=>window.ServerManMapTiers.loaded().length===2,"overlay still loads");
  check(JSON.stringify(window.ServerManMapRaster.drawn())===JSON.stringify(["terrain","tiers"])&&
    document.getElementById("map-vector").querySelectorAll(".map-grid-lines line").length===22,"grid and overlays stay");
  check(document.getElementById("map-calibrate").disabled&&!document.getElementById("map-fit").disabled,"no calibration without the image");
}
await choose();
check(JSON.stringify(calibration())===JSON.stringify(reference.calibration),"the same image keeps its calibration");
check(window.ServerManMapBackground.selection(plan(reference))==="token-1","the same image goes with its new token");
near(pixel(125,375),[255,0,0],3,"the image shows at its kept calibration");
imageAnswers.chosen=(index)=>({success:true,value:part(index,{sha256:"0".repeat(64)})});
await load(value(plan(),capabilities([])));
document.getElementById("map-background-choose").click();
await until(()=>text("map-background-status").includes("PNG or JPEG"),"discarded transfer explained");
check(window.ServerManMapBackground.current().background===null&&document.getElementById("map-undo").disabled,"a discarded image changes nothing");
imageAnswers.select=()=>refuse("UNSUPPORTED_SNAPSHOT_CONTENT",{reason:"over_budget",limit:8192,size:9000,unit:"pixels",width:9000,height:20});
document.getElementById("map-background-choose").click();
await until(()=>text("map-background-status").startsWith("This image is 9,000"),"pixel budget explained");
check(text("map-background-status")==="This image is 9,000 × 20 pixels. The map editor shows images up to 8,192 pixels on each side. Make the image smaller and choose it again."+kept,"pixel budget text");
imageAnswers.select=()=>({success:true,value:{cancelled:true}});
document.getElementById("map-background-choose").click();
await until(()=>text("map-background-status")==="No map image. The plain terrain is shown.","cancel restores the plain state");
let release;imageAnswers.stored=()=>new Promise((resolve)=>{release=resolve;});
await load(value(plan(reference)));
await load(value(plan(),capabilities([])));
release({success:true,value:part(0)});await wait();await wait();
check(!window.ServerManMapBackground.current().shown&&text("map-background-status")==="No map image. The plain terrain is shown.","late stored answer dropped");
''', budget=10000), "PASS")

    def test_cancelled_close_reads_the_stored_image_again(self) -> None:
        """QA-316-1: a close stops the stored read; a cancelled close reads it again, as the tier layer does."""
        self.assertEqual(run_editor_harness(common() + STEPS + r'''
const reference={file_name:"map.png",sha256:image.sha256,byte_size:image.byte_size,pixel_width:100,pixel_height:100,
  path:null,calibration:{a:10,b:0,c:0,d:0,e:-10,f:1000}};
let release;imageAnswers.stored=()=>new Promise((resolve)=>{release=resolve;});
await load(value(plan(reference)));
check(text("map-background-status")==="Loading the map image"&&imageReads.length===1,"stored read running");
check(await window.ServerManMapEditor.settleForClose()==="close","close settles");
release({success:true,value:part(0)});await wait();await wait();
check(!window.ServerManMapBackground.current().shown&&imageReads.length===1,"the stopped read is dropped");
imageAnswers.stored=(index)=>({success:true,value:part(index)});
window.ServerManMapEditor.releaseClose();
await until(()=>window.ServerManMapBackground.current().shown,"stored image read again after the cancelled close");
check(JSON.stringify(imageReads.map((read)=>read[2]))===JSON.stringify([0,0,1]),"part 0 again, then part 1");
check(text("map-background-status")==="Map image: map.png","status is not left at loading");
near(pixel(375,625),[255,0,0],2,"image drawn at its stored calibration");
''', budget=8000), "PASS")


if __name__ == "__main__":
    unittest.main()
