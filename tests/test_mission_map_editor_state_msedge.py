"""Verify shared exact draft actions, ordered rows and explicit bounds history in composed Edge."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_bounds_msedge import COMMON
from tests.test_mission_map_editor_tools_msedge import TOOLS
from tests.mission_map_gesture_harness import SELECT
from tests.ui_harness_support import EDGE, run_editor_harness


# Mixed objects retain all native geometry, flags, provider properties and record references.
STATE = COMMON + r'''
const mixed=()=>{
  const result=plan();result.objects=[
    {id:"Circle_A",kind:"loot_circle",name:"Loot circle 0009",visible:true,locked:false,
      centre:{x:100.123456789,z:200.987654321},radius:12.123456789,tier:"Tier2",abundance:"rich"},
    {id:"polygon-b",kind:"loot_polygon",name:"<img src=x onerror=bad()> polygon",visible:false,locked:true,
      vertices:[{x:300.125,z:300.25},{x:400.5,z:300.25},{x:400.5,z:400.75}],tier:null,abundance:"sparse"},
    {id:"encounter-c",kind:"encounter",name:"Encounter 2",visible:true,locked:false,centre:{x:500.5,z:500.25},
      radius:40.125,provider:"native_infected",properties:{event_name:"InfectedVillage",smin:1,smax:2,dmin:3,dmax:4},
      excluded:false,linked_loot:{enabled:true,tier:"Tier3",abundance:"very_rich",radius:null}}];
  result.records={originals:[{target_key:association.mission_key,manifest_sha256:"c".repeat(64)}],baseline:null,applied:null};return result;
};
const model=mapEditorState.model;
const control=(id,name)=>[...document.querySelectorAll("#map-list button")].find(button=>button.dataset.mapControl===`${id}:${name}`);
const key=async(target,name,shift=false)=>{target.focus();target.dispatchEvent(new KeyboardEvent("keydown",{key:name,ctrlKey:true,shiftKey:shift,bubbles:true,cancelable:true}));await wait()};
const acceptReview=async()=>{await wait();const dialog=document.querySelector("dialog");check(dialog,"placement review required");dialog.querySelectorAll("button")[1].click();await wait()};
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorStateEdgeTests(unittest.TestCase):
    """Exercise production modules with synthetic plans and bounded owned headless profiles."""

    def test_exact_names_identifiers_and_generic_action_guards(self) -> None:
        """D1 naming uses exact ASCII integer patterns and preserves supported Unicode names."""
        self.assertEqual(run_editor_harness(STATE + r'''
await load(value(mixed()));
const huge="9".repeat(60),names=[{name:`Loot circle ${huge}`},{name:"Loot circle 0009"},
  {name:"loot circle 99"},{name:"Loot circle １２"},{name:"Loot circle 77x"},{name:"Loot circle 4\n"}];
check(window.ServerManMapModel.nextName("loot_circle",names)===`Loot circle 1${"0".repeat(60)}`,"huge suffix increment never uses binary64");
check(window.ServerManMapModel.nextName("loot_polygon",[...names,{name:"Loot polygon 99\n"}])==="Loot polygon 1"&&model.nextName("encounter")==="Encounter 3","kind patterns and full matches stay exact");
check(window.ServerManMapModel.name("\u0085\u001c😀 name\u3000")==="😀 name","D1 Unicode whitespace and code-point name semantics");
check(window.ServerManMapModel.name("😀".repeat(80)).length===160,"80 code points remain accepted");
for(const bad of ["", "\t", "a\u0085b", "😀".repeat(81)]) {
  let refused=false;try{window.ServerManMapModel.name(bad)}catch{refused=true}check(refused,"invalid D1 name refuses");
}
const original=model.snapshot(),id=model.newId();check(/^[0-9a-f]{16}$/.test(id),"new ID is explicit stable ASCII data");
for(const mutate of [draft=>draft.objects[0].id="polygon-b",draft=>draft.objects[0].id="POLYGON-B",
  draft=>draft.objects[0].name="bad\nname",draft=>draft.objects[0].centre.x=Infinity,
  draft=>draft.records.originals=[],draft=>draft.objects[1].vertices[0].x=301]) {
  let refused=false;try{model.action("invalid",mutate)}catch{refused=true}check(refused,"invalid or locked action refuses");
  check(JSON.stringify(model.snapshot())===JSON.stringify(original)&&model.status().history.length===0,"refusal retains draft/history");
}
model.edit("Circle_A",{name:"  Exact editable name  ",centre:{x:100.123456789123,z:200.987654321123}});
check(model.snapshot().objects[0].name==="Exact editable name"&&model.snapshot().objects[0].id==="Circle_A","deliberate name edit trims without changing ID");
check(model.snapshot().objects[0].centre.x===100.123456789123&&saved.length===0,"generic action retains exact geometry and never saves");
'''), "PASS")

    def test_rows_priority_visibility_lock_and_shared_history(self) -> None:
        """Rows change actual priority; hidden and locked objects retain complete native contribution."""
        self.assertEqual(run_editor_harness(STATE + r'''
const original=mixed(),before=JSON.stringify(original);await load(value(original));
check(!document.querySelector("#map-list img")&&document.querySelectorAll("#map-list li").length===3,"safe actual rows");
check(document.getElementById("map-list").textContent.includes("Zone capacity: Very Rich +100%")&&document.getElementById("map-list").textContent.includes("Tier: Inherit"),"stored applicable summaries");
check(control("Circle_A","up").disabled&&control("encounter-c","down").disabled,"priority boundary controls");
control("polygon-b","select").click();check(window.ServerManMapLayers.selected()==="polygon-b","hidden locked item is selectable from list");
control("Circle_A","visible").click();control("polygon-b","locked").click();control("polygon-b","up").click();
check(model.snapshot().objects[0].id==="polygon-b"&&!model.snapshot().objects[0].locked,"actual priority and explicit unlock");
model.edit("polygon-b",{name:"Renamed polygon"});
check(model.status().history.length===4&&model.snapshot().objects[1].visible===false,"all current property/list edits share history");
for(let index=0;index<4;index++)await model.undo();
check(JSON.stringify(model.snapshot())===before&&model.status().draft_changes===false,"undo restores exact snapshot and actual comparison");
for(let index=0;index<4;index++)await model.redo();
const changed=model.snapshot();check(changed.objects[0].vertices[0].x===300.125&&JSON.stringify(changed.records)===JSON.stringify(original.records),"flags/order preserve geometry and references");
await model.undo();model.visible("encounter-c");check(!model.status().history.redo,"new branch drops redo");
const count=model.status().history.length;model.move("polygon-b",-1);check(model.status().history.length===count,"boundary action is not an undo step");
check(JSON.stringify(original)===before&&saved.length===0,"loaded fixture immutable; no host save or Apply");
'''), "PASS")

    def test_keyboard_focus_dialog_and_current_navigation_guards(self) -> None:
        """Text owns undo while map, range and ordinary buttons use the shared draft history."""
        self.assertEqual(run_editor_harness(STATE + r'''
await load(value(mixed()));model.visible("Circle_A");const map=document.getElementById("map-viewport");
for(const type of ["text","number","search"]) {
  const input=document.createElement("input");input.type=type;document.body.append(input);await key(input,"z");input.remove();
  check(!model.snapshot().objects[0].visible,"text input owns undo");
}
for(const tag of ["textarea","select","div"]) {
  const input=document.createElement(tag);if(tag==="div")input.contentEditable="true";document.body.append(input);await key(input,"z");input.remove();
  check(!model.snapshot().objects[0].visible,"text, select and editable focus guarded");
}
const modal=window.ServerManMapDom.dialog("Review","Synthetic",[{label:"Cancel",value:"cancel"}]);await wait();
await key(document.querySelector("dialog button"),"z");check(!model.snapshot().objects[0].visible,"modal owns its keys");
document.querySelector("dialog button").click();await modal;
map.classList.add("map-panning");await key(map,"z");check(!model.snapshot().objects[0].visible,"active pan owns input");map.classList.remove("map-panning");
await key(map,"z");check(model.snapshot().objects[0].visible,"map undo");
await key(document.getElementById("map-fit"),"y");check(!model.snapshot().objects[0].visible,"button Ctrl+Y redo");
await key(map,"z");await key(map,"z",true);check(!model.snapshot().objects[0].visible,"Ctrl+Shift+Z redo");
const range=document.createElement("input");range.type="range";document.body.append(range);await key(range,"z");range.remove();
check(model.snapshot().objects[0].visible&&saved.length===0,"range Ctrl shortcut and no save");
'''), "PASS")

    def test_bounds_confirmation_history_review_and_actual_saved_metadata(self) -> None:
        """Terrain history preserves prior object edits and reviews traversal without rolling back saved metadata."""
        self.assertEqual(run_editor_harness(STATE + r'''
await load(value(mixed()));model.visible("Circle_A");fill(bounds(-100,-200,3000,2000));manual();await acceptReview();
check(saved.length===1&&!saved[0].plan.objects[0].visible&&model.status().history.length===2,"edited draft submitted; one terrain action");
check(model.status().revision===5&&model.status().draft_changes===false,"actual revision/comparison adopted");
const before=model.snapshot(),cursor=model.status().history.position;
const canceled=model.undo();await wait();document.querySelector("dialog button").click();await canceled;
check(JSON.stringify(model.snapshot())===JSON.stringify(before)&&model.status().history.position===cursor,"traversal Cancel preserves cursor and draft");
const undone=model.undo();await acceptReview();await undone;
check(model.snapshot().terrain.bounds.width===2000&&!model.snapshot().objects[0].visible&&model.status().revision===5,"terrain undo preserves object edit and actual revision");
check(model.status().draft_changes===null&&document.getElementById("map-draft-changes").textContent==="Updating","unsaved draft has no comparison; Updating while its save waits");
const redone=model.redo();await acceptReview();await redone;
check(model.snapshot().terrain.bounds.width===3000&&model.status().draft_changes===false,"return to backend-confirmed snapshot restores actual comparison");
const count=model.status().history.length;manual();await wait();check(saved.length===2&&model.status().history.length===count&&model.status().revision===6,"no-op terrain save creates no duplicate action");
check(JSON.stringify(model.snapshot().records)===JSON.stringify(saved[0].plan.records)&&saved.length===2,"history neither changes record authority nor saves");
'''), "PASS")

    def test_pending_save_preserves_intervening_history_branch_and_snapshot(self) -> None:
        """Success joins the current branch and preserves newer object edits and their truthful unsaved status."""
        self.assertEqual(run_editor_harness(STATE + r'''
await load(value(mixed()));model.visible("Circle_A");let finish;
window.pywebview.api.save_plan=(document,revision,selection)=>{saved.push({plan:structuredClone(document),revision,selection});return new Promise(resolve=>finish=resolve)};
fill(bounds(-100,-200,3000,2000));manual();await acceptReview();const submitted=JSON.stringify(saved[0].plan);
await model.undo();model.lock("encounter-c");check(model.status().history.length===1&&!model.status().history.redo,"intervening branch stays current");
finish(success(5,true));await wait();
check(model.snapshot().objects[0].visible&&model.snapshot().objects[2].locked&&model.snapshot().terrain.bounds.width===3000,"success merges only terrain into current object draft");
check(JSON.stringify(saved[0].plan)===submitted&&model.status().history.length===2,"captured submission immutable; terrain joins current branch");
check(model.status().revision===5&&model.status().draft_changes===null&&document.getElementById("map-status").textContent.includes("unsaved changes"),"newer edits not shown as saved/current comparison");
window.ServerManMapEditor.settleForClose();await wait();check(saved.length===2,"a close saves the unsaved draft at once (5.4)");window.ServerManMapEditor.releaseClose();
check(document.getElementById("map-draft-changes").textContent==="Updating","release never revives obsolete comparison; the unsaved draft waits for its save");
'''), "PASS")

    def test_refusal_invalid_metadata_cancel_reload_and_late_response_preserve_history(self) -> None:
        """Refused or superseded saves cannot create terrain actions or revive previous draft history."""
        self.assertEqual(run_editor_harness(STATE + r'''
await load(value(mixed()));model.visible("Circle_A");const original=model.snapshot(),cursor=model.status().history.position;
fill(bounds(-100,-200,3000,2000));manual();await wait();document.querySelector("dialog button").click();await wait();
check(saved.length===0&&model.status().history.position===cursor,"confirmation Cancel leaves history");
for(const response of [{success:false,error:{code:"REVISION_CONFLICT",message:"changed",details:{reason:"stale_revision"}}},
  {success:true,value:{association,revision:5,draft_changes:"false"}}]) {
  window.pywebview.api.save_plan=async(document,revision,selection)=>{saved.push({plan:structuredClone(document),revision,selection});return response};
  manual();await acceptReview();check(JSON.stringify(model.snapshot())===JSON.stringify(original)&&model.status().history.position===cursor&&model.status().revision===4,"failure or malformed metadata makes no successful action");
}
let finish;window.pywebview.api.save_plan=(document,revision,selection)=>{saved.push({plan:structuredClone(document),revision,selection});return new Promise(resolve=>finish=resolve)};
manual();await acceptReview();await load({...value(mixed()),revision:11});finish(success(5,true));await wait();
check(model.status().revision===11&&model.status().history.length===0&&model.snapshot().terrain.bounds.width===2000,"new load clears history and rejects late success");
model.visible("Circle_A");manual();await wait();const closing=window.ServerManMapEditor.settleForClose();finish(success(12,true));await wait();
check(model.status().revision===12&&model.status().history.length===1&&document.getElementById("map-status").textContent==="Closing","a committed answer during a close is adopted and keeps Closing (QA-5.2-F5)");
check(await closing==="close","the adopted bounds answer leaves nothing unsaved");
window.ServerManMapEditor.releaseClose();check(!model.snapshot().objects[0].visible&&!model.status().dirty&&model.status().draft_changes===true,"close release keeps the edit that the adopted answer saved");
'''), "PASS")

    def test_unconfirmed_numeric_failure_and_unavailable_states_guard_mutation(self) -> None:
        """Unconfirmed and failed-map states allow list edits; absent, read-only, loading and Closing refuse them."""
        self.assertEqual(run_editor_harness(STATE + r'''
const current=mixed();current.terrain.bounds_confirmed=false;current.terrain.bounds=bounds(1000000,0,Number.MIN_VALUE,1000);
await load(value(current));check(window.ServerManMapViewportUi.view().snapshot().failure.axes.x,"actual X conversion fails");
model.visible("Circle_A");model.lock("encounter-c");model.move("polygon-b",-1);
check(model.status().history.length===3&&!model.snapshot().terrain.bounds_confirmed,"three non-map list actions preserve unconfirmed state");
await load(value());check(!model.visible("Circle_A")&&!model.action("no plan",()=>{}),"no-plan ordinary mutations refuse");
await load({...value(mixed()),read_only:true,read_only_reason:"invalid_saved_data"});check(!model.visible("Circle_A"),"read-only refuses");
await load(value(mixed()));let release;window.pywebview.api.load_plan=()=>new Promise(resolve=>release=resolve);
const loading=window.ServerManMapEditor.load();await wait();check(!model.lock("Circle_A"),"loading refuses");
release({success:true,value:value(mixed())});await loading;
await window.ServerManMapEditor.settleForClose();check(!model.move("Circle_A",1)&&!(await model.undo()),"Closing refuses");
window.ServerManMapEditor.releaseClose();check(model.visible("Circle_A"),"released valid draft editable");
'''), "PASS")

    def test_first_manual_save_null_before_undo_redo_keeps_saved_identity(self) -> None:
        """Local null history never deletes saved content or regenerates its retained identity."""
        self.assertEqual(run_editor_harness(STATE + r'''
await load(value());fill(bounds(-100,-200,3000,2000));manual();await wait();
const accepted=model.snapshot();check(model.status().revision===0&&model.status().history.length===1,"first save is one terrain creation action");
await model.undo();check(model.snapshot()===null&&model.status().revision===0&&model.status().history.redo,"undo preserves exact local absence and current saved revision");
check(document.getElementById("map-status").textContent.includes("saved plan is unchanged")&&model.status().draft_changes===null,"local absence makes no stored deletion claim");
check(!model.visible("anything")&&!model.action("missing",()=>{}),"ordinary no-plan edits disabled");
await model.redo();check(JSON.stringify(model.snapshot())===JSON.stringify(accepted)&&model.status().draft_changes===false,"redo restores same accepted identity, geometry and actual comparison");
check(saved.length===1&&JSON.stringify(model.status().association)===JSON.stringify(association),"history neither saves nor rolls association back");
'''), "PASS")

    def test_history_keeps_100_entries_and_buttons_traverse_it(self) -> None:
        """101 actions keep 100 entries; 100 undos reach action 1, 100 redos return; the buttons step the same history."""
        self.assertEqual(run_editor_harness(STATE + r'''
await load(value(mixed()));const after=[];
for(let index=1;index<=101;index++){model.edit("Circle_A",{name:`Name ${index}`});after.push(JSON.stringify(model.snapshot()))}
check(model.status().history.length===100&&model.status().history.position===100,"101 actions keep 100 entries at position 100");
for(let index=0;index<100;index++)check(await model.undo(),"each of 100 undo steps applies");
check(JSON.stringify(model.snapshot())===after[0]&&!model.status().history.undo&&!(await model.undo()),"100 undos reach the plan after action 1, then undo stops");
for(let index=0;index<100;index++)check(await model.redo(),"each of 100 redo steps applies");
check(JSON.stringify(model.snapshot())===after[100]&&!model.status().history.redo,"100 redos return to the plan after action 101");
const undo=document.getElementById("map-undo"),redo=document.getElementById("map-redo");
check(!undo.disabled&&redo.disabled,"button states at the newest entry");
undo.click();await wait();undo.click();await wait();
check(JSON.stringify(model.snapshot())===after[98]&&!redo.disabled,"Undo button steps back twice");
redo.click();await wait();check(JSON.stringify(model.snapshot())===after[99]&&model.status().history.position===99,"Redo button steps forward once");
check(saved.length===0,"history buttons never save");
'''), "PASS")

    def test_compact_rows_keep_selected_priority_and_control_focus(self) -> None:
        """Compact rows retain full names, semantic flags, selected priority and focused controls."""
        self.assertEqual(run_editor_harness(STATE + r'''
await load(value(mixed()));
model.edit("Circle_A",{name:"A long object name that must remain accessible across a compact row"});
control("Circle_A","select").click();
window.ServerManMapWorkspace.open("navigation");await wait();
for(const row of document.querySelectorAll("#map-list li")) {
  const select=row.querySelector('[data-map-control$=":select"]');
  check(Math.abs(select.getBoundingClientRect().height-36)<=2,"main object line remains compact");
}
check(document.querySelectorAll(".map-layer-priority:not([hidden])").length===1,"only selected object exposes priority strip");
const visible=control("Circle_A","visible");visible.focus();visible.click();
check(document.activeElement.dataset.mapControl==="Circle_A:visible","state rerender retains focused control");
check(control("Circle_A","select").getAttribute("aria-label").includes("A long object name"),"full name remains accessible");
check(control("Circle_A","visible").getAttribute("aria-pressed")==="false","hidden state remains explicit");
'''), "PASS")

    def test_custom_opacity_is_independent_and_keeps_selection_at_zero(self) -> None:
        """Authored opacity changes only interiors while selection and original controls stay truthful."""
        self.assertEqual(run_editor_harness(STATE + r'''
await load(value(mixed()));control("Circle_A","select").click();
const slider=document.getElementById("map-custom-opacity"),original=document.getElementById("map-tiers-opacity");
const before=JSON.stringify(model.snapshot()),history=model.status().history.length;
check(slider.value==="100"&&slider.min==="0"&&slider.max==="100"&&slider.step==="10","custom slider exposes actual supported values");
for(const percent of [0,50,100]) {
  slider.value=percent;slider.dispatchEvent(new Event("input"));
  const wash=document.querySelector(".map-zone-wash"),hatch=document.querySelector(".map-zone-hatch");
  check(Math.abs(Number(getComputedStyle(wash).fillOpacity)-.12*percent/100)<.001&&Number(getComputedStyle(hatch).fillOpacity)===percent/100,"only authored interior alpha changes");
  check(original.value==="100"&&document.getElementById("map-custom-opacity-value").textContent===`${percent}%`,"original control is independent and custom output stays synchronized");
}
original.value=0;original.dispatchEvent(new Event("input"));
check(window.ServerManMapLayerDisplay.fraction()===1,"original opacity cannot change authored state");
slider.value=0;slider.dispatchEvent(new Event("input"));
// Bypass native range rounding to exercise the display guard with an unsupported step.
slider.step=1;slider.value=15;slider.dispatchEvent(new Event("input"));slider.step=10;
check(slider.value==="0"&&window.ServerManMapLayerDisplay.fraction()===0,"invalid step restores actual display value");
const selection=document.querySelector(".map-zone-selection"),backing=document.querySelector(".map-zone-selection-outer");
check(getComputedStyle(selection).stroke==="rgb(198, 40, 40)"&&getComputedStyle(selection).strokeWidth==="3px"&&getComputedStyle(backing).strokeWidth==="5px","full red3px selection and white backing remain at zero opacity");
check(getComputedStyle(document.querySelector(".map-layer-selected")).borderLeftColor==="rgb(198, 40, 40)","selected row uses the same red");
check(JSON.stringify(model.snapshot())===before&&model.status().history.length===history&&saved.length===0,"display changes do not edit or save the plan");
'''), "PASS")

    def test_modifier_cancel_and_changed_edit_gates_drop_pending_geometry(self) -> None:
        """Blur, cancelled presses and changed locks cannot commit a later stray release."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + r'''
const start=plan();start.objects=[circle("C",1000,500,100)];await load(value(start));pick("C");
const original=geometry("C");
for(const signal of ["pointercancel","lostpointercapture"]) {
  gesture("pointerdown",at(1100,500),77,1,region,true);
  gesture(signal,at(1100,500),77,0);gesture("pointermove",at(1200,500),77,1,region,true);release(at(1200,500),77);
  check(!window.ServerManMapDrag.active()&&geometry("C")===original&&position()===0,"cancelled pending handle press never resurrects");
}
const end=drag([1100,500],[1200,500],{ctrl:true,hold:true});window.dispatchEvent(new Event("blur"));
check(handles()===0&&!window.ServerManMapDrag.active(),"blur clears modifier and active preview");release(end,7);
check(geometry("C")===original&&position()===0,"blurred geometry never commits");
drag([1000,500],[1100,500],{hold:true});model.lock("C");const locked=position();release(at(1100,500),7);
check(find("C").centre.x===1000&&position()===locked&&!window.ServerManMapDrag.active(),"locking during movement cancels before commit");
model.lock("C");drag([1000,500],[1100,500],{hold:true});model.visible("C");const hidden=position();release(at(1100,500),7);
check(find("C").centre.x===1000&&position()===hidden&&!window.ServerManMapDrag.active(),"hiding during movement cancels before commit");
'''), "PASS")
