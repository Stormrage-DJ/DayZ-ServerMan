"""Verify explicit bounds confirmation, placement review and revision-safe saves in composed Edge."""

from __future__ import annotations

import unittest

from tests.ui_harness_support import EDGE, run_editor_harness


# The fake backend returns real save metadata without a fabricated plan echo.
COMMON = r'''
await window.ServerManMapEditor.load(); await wait();
const association={profile_id:"alpha",mission_root:"mpmissions\\dayzOffline.synthetic",mission_key:"a".repeat(32),runtime_profile_key:null};
const bounds=(x_min=0,z_min=0,width=2000,height=1000)=>({x_min,z_min,width,height});
const plan=(rectangle=bounds(),confirmed=true)=>({format:"dayz-serverman/mission-map-plan",version:1,plan_id:"stable-plan",
  association:structuredClone(association),terrain:{terrain_id:"synthetic",bounds:rectangle,bounds_source:"manual",bounds_confirmed:confirmed},
  global_abundance:0,objects:[],background:null,records:{originals:[],baseline:null,applied:null}});
const value=(document=null,detected=null)=>({...editorValue,association:structuredClone(association),plan:document,
  revision:document?4:null,capabilities:{terrain_id:"synthetic",detected_bounds:detected},draft_changes:false});
const saved=[];
const success=(revision,draft_changes=false)=>({success:true,value:{association:structuredClone(association),revision,
  draft_changes,fingerprint:"b".repeat(64),interrupted_evidence:[]}});
window.pywebview.api.save_plan=async(document,revision,selection)=>{
  saved.push({plan:structuredClone(document),revision,selection});return success(revision===null?0:revision+1);
};
const load=async(current)=>{editorAnswer={success:true,value:current};await window.ServerManMapEditor.load();await wait()};
const fill=(rectangle)=>{for(const [name,number] of Object.entries(rectangle))document.getElementById(`map-bounds-${name}`).value=String(number)};
const manual=()=>document.getElementById("map-bounds-save").click();
const fields=()=>document.getElementById("map-bounds-fields");
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorBoundsEdgeTests(unittest.TestCase):
    """Use only isolated headless profiles and synthetic profile-bound plans."""

    def test_unknown_family_proved_header_confirmation_retains_truthful_provenance(self) -> None:
        """Header proof survives unknown family recognition; manual submissions retain manual provenance."""
        self.assertEqual(run_editor_harness(COMMON + r'''
const detected=bounds(0,0,8192,4096),unknownPlan=plan(detected,false);
unknownPlan.terrain.terrain_id="unknown-Mysteryterrain";unknownPlan.terrain.bounds_source="detected";
const current=value(unknownPlan,detected);current.capabilities.terrain_id=null;current.mission_name="Mystery terrain ! ü";
await load(current);
check(saved.length===0&&!unknownPlan.terrain.bounds_confirmed,"unknown family remains explicitly unconfirmed");
check(document.getElementById("map-terrain").textContent==="Unknown terrain (mission Mystery terrain ! ü)","mission identity shown without invented family");
check(!document.getElementById("map-bounds-detected").disabled,"proved header permits explicit detected confirmation");
document.getElementById("map-bounds-detected").click();await wait();
check(saved[0].plan.terrain.bounds_source==="detected"&&saved[0].plan.terrain.bounds_confirmed,"header confirmation retains detected source");
check(JSON.stringify(saved[0].plan.terrain.bounds)===JSON.stringify(detected)&&saved[0].plan.terrain.terrain_id===unknownPlan.terrain.terrain_id,"exact header and unknown identity preserved");
manual();await wait();check(saved[1].plan.terrain.bounds_source==="manual","deliberate manual submission stays manual even when equal to header");
fill(bounds(0,0,9000,4096));manual();await wait();
check(saved[2].plan.terrain.bounds.width===9000&&saved[2].plan.terrain.bounds_source==="manual","edited values retain manual provenance");
check(unknownPlan.terrain.bounds.width===8192&&!unknownPlan.terrain.bounds_confirmed,"loaded unknown snapshot stays unchanged");
'''), "PASS")

    def test_detected_confirmation_adopts_actual_metadata_without_echo(self) -> None:
        """Detected values need an explicit click, while identity and neutral fields stay exact."""
        self.assertEqual(run_editor_harness(COMMON + r'''
const detected=bounds(0,0,20480,8192),loadedPlan=plan(detected,false);loadedPlan.terrain.bounds_source="detected";
await load(value(loadedPlan,detected));
check(saved.length===0&&!loadedPlan.terrain.bounds_confirmed,"load never silently confirms or saves");
check(document.getElementById("map-bounds-confirmation").textContent==="Bounds not confirmed","visible unconfirmed state");
check(document.getElementById("map-bounds-detection").textContent.includes("20480"),"actual detected extent shown");
const announcement=document.getElementById("map-announcement").textContent;
document.getElementById("map-bounds-detected").click();await wait();
check(document.getElementById("map-announcement").textContent===announcement,"routine Saving/Saved does not announce");
check(saved.length===1&&saved[0].revision===4&&saved[0].selection===null,"revision and null image selection forwarded");
const submitted=saved[0].plan;
check(submitted.terrain.bounds_confirmed&&submitted.terrain.bounds_source==="detected","explicit detected confirmation");
check(submitted.plan_id==="stable-plan"&&submitted.global_abundance===0&&submitted.background===null,"neutral stored fields retained");
check(JSON.stringify(submitted.records)===JSON.stringify(loadedPlan.records)&&submitted.objects.length===0,"records unchanged");
check(document.getElementById("map-draft-changes").textContent==="No"&&document.getElementById("map-status").textContent==="Bounds saved","actual save comparison adopted");
manual();await wait();check(saved[1].revision===5,"next explicit save uses returned revision");
check(Object.keys(window.ServerManMapEditor).sort().join(",")==="load,releaseClose,settleForClose","host-facing surface unchanged");
'''), "PASS")

    def test_manual_unknown_neutral_plan_exact_limits_and_subnormal_extent(self) -> None:
        """Missing bounds stay blank and manual values never pass through display rounding or HTML steps."""
        self.assertEqual(run_editor_harness(COMMON + r'''
const current=value();current.capabilities.terrain_id=null;current.mission_name="Mystery terrain ! ü";
await load(current);
check(["x_min","z_min","width","height"].every(name=>document.getElementById(`map-bounds-${name}`).value===""),"missing numeric fields are not guessed zero");
check(document.getElementById("map-terrain").textContent==="Unknown terrain (mission Mystery terrain ! ü)","accepted unknown terrain identity");
check(document.getElementById("map-bounds-detected").disabled,"no invented detection");
const exact=bounds(-1000000,1000000,100000,777.123456789);fill(exact);manual();await wait();
const first=saved[0].plan;
check(first.format==="dayz-serverman/mission-map-plan"&&first.version===1&&/^[0-9a-f]{16}$/.test(first.plan_id),"correct neutral schema and stable identifier");
check(first.terrain.terrain_id==="unknown-Mysteryterrain"&&first.terrain.bounds_source==="manual"&&first.terrain.bounds_confirmed,"manual mission-derived identity confirmed");
check(JSON.stringify(first.terrain.bounds)===JSON.stringify(exact)&&first.global_abundance===0&&first.background===null&&first.objects.length===0,"exact manual neutral geometry");
check(JSON.stringify(first.association)===JSON.stringify(association)&&JSON.stringify(first.records)==='{"originals":[],"baseline":null,"applied":null}',"correct association and empty records");
fill(bounds(1000000,0,Number.MIN_VALUE,1000));manual();await wait();
check(saved[1].plan.terrain.bounds.width===Number.MIN_VALUE&&saved[1].plan.plan_id===first.plan_id,"subnormal admitted without new ID or rounding");
check(document.getElementById("map-bounds-width").type==="text"&&!document.getElementById("map-bounds-width").hasAttribute("step"),"no HTML numeric-step narrowing");
check(window.ServerManMapViewportUi.view().snapshot().failure.axes.x&&!fields().disabled,"bounds review stays available despite failed map X");
fill(bounds(0,0,1000,1000));manual();await wait();
check(!window.ServerManMapViewportUi.view().snapshot().failure,"explicit bounds replacement recovers transform");
'''), "PASS")

    def test_invalid_manual_values_refuse_before_backend_without_guessing(self) -> None:
        """Each scalar partition follows D1, including blank, nonfinite and strict positive extents."""
        self.assertEqual(run_editor_harness(COMMON + r'''
await load(value());
for(const [name,text] of [["x_min",""],["z_min"," "],["x_min","NaN"],["z_min","Infinity"],
  ["x_min","-1000000.1"],["z_min","1000000.1"],["width","0"],["height","-1"],
  ["width","100000.1"],["height","Infinity"],["width","1e-400"]]) {
  fill(bounds());document.getElementById(`map-bounds-${name}`).value=text;manual();await wait();
  check(saved.length===0,"invalid fields never reach backend");
  check(document.getElementById(`map-bounds-${name}`).getAttribute("aria-invalid")==="true","failing field identified");
  check(document.getElementById("map-bounds-error").textContent.length>0,"finite limit guidance visible");
}
check(!document.querySelector("[style]"),"static CSS only");
'''), "PASS")

    def test_changed_confirmed_bounds_review_cancel_and_confirm_preserve_placements(self) -> None:
        """Review shows native placements; cancellation saves nothing and confirmation moves nothing."""
        self.assertEqual(run_editor_harness(COMMON + r'''
const original=plan();original.objects=[{id:"circle-1",name:"<img src=x onerror=bad()> native zone",kind:"loot_circle",
  centre:{x:100.123456789,z:200.987654321},radius:12.123456789,tier:null,abundance:"rich",visible:false,locked:true}];
original.records={originals:[{target_key:association.mission_key,manifest_sha256:"c".repeat(64)}],baseline:{revision:7,manifest_sha256:"d".repeat(64)},applied:null};
const before=JSON.stringify(original);Object.freeze(original.objects[0].centre);Object.freeze(original.objects[0]);Object.freeze(original.objects);Object.freeze(original);
await load(value(original));const proposed=bounds(-500.125,-250.75,3000.123456789,2000.987654321);fill(proposed);manual();await wait();
let dialog=document.querySelector("dialog");check(dialog&&saved.length===0,"changed confirmed placements require explicit review");
check(dialog.textContent.includes("100.123456789")&&dialog.textContent.includes("3000.123456789")&&dialog.textContent.includes("circle-1"),"actual old/new bounds and placements shown");
check(!dialog.querySelector("img")&&dialog.querySelector("button")===document.activeElement,"safe text and Cancel gets focus");
dialog.querySelector("button").click();await wait();check(saved.length===0&&JSON.stringify(original)===before,"review cancellation leaves all stored data");
manual();await wait();document.querySelector("dialog").querySelectorAll("button")[1].click();await wait();
check(saved.length===1&&JSON.stringify(saved[0].plan.objects)===JSON.stringify(original.objects),"no reprojection, deletion or property change");
check(JSON.stringify(saved[0].plan.records)===JSON.stringify(original.records)&&saved[0].plan.plan_id===original.plan_id,"ID and references retained");
check(JSON.stringify(saved[0].plan.terrain.bounds)===JSON.stringify(proposed)&&JSON.stringify(original)===before,"exact proposed bounds and immutable loaded snapshot");
manual();await wait();check(saved.length===2&&!document.querySelector("dialog"),"unchanged confirmed bounds need no redundant placement review");
'''), "PASS")

    def test_authoritative_refusal_and_retry_keep_candidate_and_revision_truthful(self) -> None:
        """Backend geometry/stale refusals remain visible and a manual neutral ID survives retry."""
        self.assertEqual(run_editor_harness(COMMON + r'''
await load(value());fill(bounds(-12.123456789,33.987654321,1000,500));
let refuse=true;window.pywebview.api.save_plan=async(document,revision,selection)=>{
  saved.push({plan:structuredClone(document),revision,selection});
  return refuse?{success:false,error:{code:"REVISION_CONFLICT",message:"changed",details:{reason:"stale_revision"}}}:success(0,true);
};
manual();await wait();check(document.getElementById("map-status").textContent.includes("Reload the plan before saving again"),"existing editor stale guidance retained");
check(document.getElementById("map-bounds-confirmation").textContent==="Bounds not confirmed"&&!fields().disabled,"refused candidate remains retryable, not saved");
refuse=false;manual();await wait();check(saved[0].plan.plan_id===saved[1].plan.plan_id&&saved.every(item=>item.revision===null),"stable new identifier and no invented revision");
check(document.getElementById("map-draft-changes").textContent==="Yes","backend true comparison adopted");
check(document.getElementById("map-announcement").textContent==="Bounds saved","save recovery after failure announced");
const existing=plan();existing.objects=[{id:"outside",name:"Stored zone",kind:"loot_circle",centre:{x:100,z:200},radius:20}];
await load(value(existing));window.pywebview.api.save_plan=async(document,revision,selection)=>{
  saved.push({plan:structuredClone(document),revision,selection});return {success:false,error:{code:"INVALID_REQUEST",
    message:"outside: centre is outside terrain bounds",details:{reason:"invalid_document",problems:[{object_id:"outside",rule:"invalid_document",message:"outside bounds"}]}}};
};
fill(bounds(0,0,50,1000));manual();await wait();document.querySelector("dialog").querySelectorAll("button")[1].click();await wait();
check(document.getElementById("map-status").textContent.includes("outside")&&document.getElementById("map-bounds-width").value==="50","authoritative refusal keeps entered fields and reason");
check(existing.terrain.bounds.width===2000&&existing.objects[0].centre.x===100,"no object repair or loaded mutation");
'''), "PASS")

    def test_read_only_loading_late_save_review_and_close_keep_newer_state(self) -> None:
        """Invalidated answers release busy controls without replacing identity, revision or Closing."""
        self.assertEqual(run_editor_harness(COMMON + r'''
for(const reason of ["invalid_saved_data","mission_path_changed","returned_to_earlier_path"]) {
  await load({...value(plan(),bounds()),read_only:true,read_only_reason:reason});
  check(fields().disabled&&document.getElementById("map-bounds-detected").disabled,"read-only guards bounds controls");manual();
}
check(saved.length===0,"read-only makes no save");
await load({...value(plan()),capabilities:{terrain_id:null,detected_bounds:null},read_only:true,read_only_reason:"mission_path_changed"});
check(document.getElementById("map-terrain").textContent==="synthetic","missing current detection cannot replace a known loaded terrain identity");
await load(value(plan()));let finish;window.pywebview.api.save_plan=(document,revision,selection)=>{
  saved.push({plan:structuredClone(document),revision,selection});return new Promise(resolve=>finish=resolve);
};
manual();await wait();check(fields().disabled&&fields().getAttribute("aria-busy")==="true","pending save is visible and cannot overlap");
await load({...value(plan(bounds(0,0,5000,5000))),revision:9});
finish(success(5,true));await wait();check(document.getElementById("map-bounds-width").value==="5000"&&document.getElementById("map-draft-changes").textContent==="No","late save cannot overwrite newer loaded state");
manual();await wait();check(saved[1].revision===9,"new save uses the newer load revision");
check(await window.ServerManMapEditor.settleForClose()==="close","accepted immediate close Promise retained");
finish(success(10,true));await wait();check(document.getElementById("map-status").textContent==="Closing"&&fields().disabled,"late save cannot replace Closing");
window.ServerManMapEditor.releaseClose();check(!fields().disabled&&document.getElementById("map-draft-changes").textContent==="Yes"&&mapEditorState.model.status().revision===10,"release shows the answer committed during the close (QA-5.2-F5)");
const reviewed=plan();reviewed.objects=[{id:"retained",name:"Retained",centre:{x:1,z:1}}];await load(value(reviewed));
fill(bounds(0,0,1000,1000));manual();await wait();check(document.querySelector("dialog"),"review active");
await load({...value(plan(bounds(0,0,3000,3000))),revision:12});
check(!document.querySelector("dialog")&&document.getElementById("map-bounds-width").value==="3000"&&saved.length===2,"new load cancels old review without a save");
'''), "PASS")
