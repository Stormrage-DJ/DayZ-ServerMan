"""Verify the Circle and Polygon tools, the outline draft and the Finish save in composed Edge."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_bounds_msedge import COMMON
from tests.ui_harness_support import EDGE, run_editor_harness


# Clicks go through the real viewport listeners at native points; pointer capture is recorded only.
TOOLS = COMMON + r'''
const model=mapEditorState.model,view=window.ServerManMapViewportUi.view(),region=document.getElementById("map-viewport");
const captured=new Set();region.setPointerCapture=id=>captured.add(id);region.hasPointerCapture=id=>captured.has(id);
region.releasePointerCapture=id=>captured.delete(id);
const at=(x,z)=>{const point=view.toScreen({x,z}),rectangle=region.getBoundingClientRect();
  return {clientX:rectangle.left+region.clientLeft+point.x,clientY:rectangle.top+region.clientTop+point.y}};
const pointer=(type,place,button=0,id=1,shiftKey=false)=>region.dispatchEvent(new PointerEvent(type,
  {...place,button,pointerId:id,shiftKey,bubbles:true,cancelable:true}));
const click=(x,z,shiftKey=false)=>{const place=at(x,z);pointer("pointerdown",place,0,1,shiftKey);
  region.dispatchEvent(new MouseEvent("click",{...place,button:0,shiftKey,bubbles:true,cancelable:true}))};
const tool=(name)=>document.getElementById(`map-tool-${name}`).click();
const text=(id)=>document.getElementById(id).textContent;
const draft=()=>JSON.stringify(window.ServerManMapTools.draft());
const press=(target,key)=>{target.focus();target.dispatchEvent(new KeyboardEvent("keydown",{key,bubbles:true,cancelable:true}))};
const refuse=(id,message="the polygon outline crosses or touches itself")=>({success:false,error:{code:"INVALID_REQUEST",message,
  details:{reason:"invalid_document",problems:[{object_id:id,rule:"invalid_document",message}]}}});
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorToolsEdgeTests(unittest.TestCase):
    """Use synthetic plans, fake save answers and isolated headless profiles."""

    def test_circle_click_last_radius_drag_and_outside_bounds(self) -> None:
        """One click is one top circle with the last radius; a drag and an outside click place nothing."""
        self.assertEqual(run_editor_harness(TOOLS + r'''
await load(value(plan()));const radius=document.getElementById("map-circle-radius");
check(!document.getElementById("map-tool-circle").disabled&&text("map-tool-status")==="No tool active","tools usable, none active");
tool("circle");check(text("map-tool-status")==="Circle"&&document.getElementById("map-tool-circle").getAttribute("aria-pressed")==="true","circle tool chosen");
click(500,400);let objects=model.snapshot().objects;
check(text("map-tool-notice")===""&&getComputedStyle(document.getElementById("map-tool-notice")).display==="none","the empty map notice is hidden");
check(objects.length===1&&objects[0].kind==="loot_circle"&&objects[0].radius===100&&objects[0].name==="Loot circle 1","one click, one circle");
check(objects[0].tier===null&&objects[0].abundance==="rich"&&objects[0].visible&&!objects[0].locked,"Rich, Inherit, visible, unlocked");
const pixel=1/view.snapshot().scale;check(Math.abs(objects[0].centre.x-500)<=pixel&&Math.abs(objects[0].centre.z-400)<=pixel,"native centre within one CSS pixel");
radius.value="250";radius.dispatchEvent(new Event("change"));click(800,600);click(1100,600);objects=model.snapshot().objects;
check(objects.length===3&&objects[0].name==="Loot circle 3"&&objects[0].radius===250&&objects[1].radius===250&&objects[2].name==="Loot circle 1","new circles at the top with the last radius");
radius.value="0";radius.dispatchEvent(new Event("change"));
check(radius.getAttribute("aria-invalid")==="true"&&text("map-tool-message").includes("radius"),"invalid radius says why");
click(1300,600);check(model.snapshot().objects[0].radius===250,"invalid radius keeps the last radius");
const count=model.snapshot().objects.length,history=model.status().history.length,from=at(1500,500),to=at(1600,550);
pointer("pointerdown",from,0,2);
for(let step=1;step<=10;step++)pointer("pointermove",{clientX:from.clientX+(to.clientX-from.clientX)*step/10,clientY:from.clientY+(to.clientY-from.clientY)*step/10},0,2);
pointer("pointerup",to,0,2);region.dispatchEvent(new MouseEvent("click",{...to,button:0,bubbles:true,cancelable:true}));
check(model.snapshot().objects.length===count&&model.status().history.length===history,"a drag paints nothing");
click(-50,500);click(1000,1000.5);
check(model.snapshot().objects.length===count&&text("map-tool-message").includes("outside the terrain bounds")&&text("map-announcement").includes("outside"),"outside click places nothing and says why");
const panel=document.getElementById("map-panel");panel.open=false;await wait();const size=[region.clientWidth,region.clientHeight].join();
click(-50,500);const notice=document.getElementById("map-tool-notice");
check(notice.textContent.includes("outside the terrain bounds")&&getComputedStyle(notice).display!=="none"&&[region.clientWidth,region.clientHeight].join()===size,"the reason shows on the map with the panel collapsed; the map keeps its size");
panel.open=true;await wait();
check(window.ServerManMapOutline.inside(bounds(),{x:2000,z:1000})&&!window.ServerManMapOutline.inside(bounds(),{x:2000.001,z:0}),"the closed bounds include their edge");
click(1900,900);check(document.querySelectorAll("#map-zones .map-zone-inherit").length===count+1,"Inherit zones drawn");
await model.undo();check(model.snapshot().objects.length===count&&saved.length===0,"one undo per circle; placement does not save at once");
press(region,"p");check(text("map-tool-status")==="Polygon","P on the map chooses Polygon");
click(-50,500);click(1000,1000.5);
check(draft()==="[]"&&text("map-tool-message").includes("add an outline point")&&text("map-tool-notice")===text("map-tool-message"),"a polygon click outside the bounds adds no vertex and says why");
press(radius,"c");check(text("map-tool-status")==="Polygon","a letter in a text field is not a tool key");
tool("polygon");check(text("map-tool-status")==="No tool active","second press ends the tool");
const broken=plan();broken.objects=[{id:"Bad",kind:"loot_circle",name:"Loot circle 9",visible:true,locked:false,centre:null,radius:50,tier:null,abundance:"rich"},
  {id:"Good",kind:"loot_circle",name:"Loot circle 8",visible:true,locked:false,centre:{x:300,z:300},radius:50,tier:null,abundance:"rich"}];
await load({...value(broken),read_only:true,read_only_reason:"invalid_saved_data"});
check(text("map-status").startsWith("Read only: the saved map plan is not valid. Set aside invalid plan keeps it under a dated name and starts a new, empty plan.")&&document.body.dataset.mapReady==="true","invalid saved data loads read only");
check(document.querySelectorAll("#map-list li").length===2&&document.querySelectorAll("#map-zones .map-zone").length===1&&document.querySelector('[data-map-zone="Good"]'),"every row listed; only the usable zone drawn");
check(document.getElementById("map-tool-circle").disabled&&radius.disabled,"read-only plan has no tools");
'''), "PASS")

    def test_outline_draft_survives_selection_edit_and_pan_then_finish_saves(self) -> None:
        """Shift selection, list selection, other edits, undo and panning keep the draft; Finish saves at once."""
        self.assertEqual(run_editor_harness(TOOLS + r'''
const start=plan();start.objects=[{id:"Circle_A",kind:"loot_circle",name:"Loot circle 1",visible:true,locked:false,
  centre:{x:1500,z:500},radius:200,tier:"Tier2",abundance:"normal"}];
await load(value(start));tool("polygon");click(100,100);click(400,100);const outline=draft();
check(text("map-outline-status")==="Unfinished outline: 2 points"&&text("map-tool-status")==="Polygon · 2 pt"&&document.getElementById("map-tool-status").title==="Polygon; outline 2 points","outline status; compact footer with the full title");
click(1500,500,true);
check(window.ServerManMapLayers.selected()==="Circle_A"&&draft()===outline&&model.snapshot().objects.length===1,"Shift+click selects without placement");
check(document.querySelector('[data-map-zone="Circle_A"]').classList.contains("map-zone-selected"),"selection drawn");
document.querySelector('[data-map-control="Circle_A:select"]').click();model.edit("Circle_A",{name:"Renamed"});model.visible("Circle_A");
await model.undo();check(draft()===outline,"list selection, other edits and undo keep the draft");
const before=view.snapshot(),place=at(1000,500),moved={clientX:place.clientX+60,clientY:place.clientY+40};
pointer("pointerdown",place,1,5);pointer("pointermove",moved,1,5);pointer("pointerup",moved,1,5);
region.dispatchEvent(new MouseEvent("auxclick",{...moved,button:1,bubbles:true,cancelable:true}));
check(view.snapshot().offsetX!==before.offsetX&&draft()===outline,"panning keeps the draft");
press(region,"c");press(region,"Escape");check(draft()===outline,"tool letters and Escape never discard the outline");
press(region,"p");press(region,"Enter");check(text("map-tool-message")==="An outline needs 3 points"&&saved.length===0,"Finish needs 3 points");
click(400,400);press(region,"Backspace");check(draft()===outline,"Backspace removes the last point");
click(400,400);const history=model.status().history.position,finishButton=document.getElementById("map-outline-finish");
finishButton.focus();finishButton.click();await wait();check(document.activeElement.id==="map-viewport","focus moves to the map after Finish");
const objects=model.snapshot().objects;
check(objects.length===2&&objects[0].kind==="loot_polygon"&&objects[0].name==="Loot polygon 1"&&objects[0].vertices.length===3,"Finish adds the outline at the top");
check(objects[0].tier===null&&objects[0].abundance==="rich"&&draft()==="[]"&&model.status().history.position===history+1,"Rich, Inherit, one undo step, draft closed");
check(saved.length===1&&saved[0].plan.objects[0].id===objects[0].id&&saved[0].revision===4,"Finish saves at once with the loaded revision");
check(model.status().revision===5&&text("map-status")==="Saved"&&model.status().draft_changes===false,"actual save metadata adopted");
check(document.getElementById("map-outline-finish").disabled&&document.getElementById("map-outline-discard").disabled,"no draft controls without a draft");
'''), "PASS")

    def test_crossing_outline_refused_locally_and_backend_refusal_returns_it(self) -> None:
        """Crossing edges, the closing edge too, refuse Finish; a backend refusal returns the outline once."""
        self.assertEqual(run_editor_harness(TOOLS + r'''
await load(value(plan()));tool("circle");click(1500,500);tool("polygon");
for(const [x,z] of [[100,100],[500,100],[300,100]])click(x,z);
check(document.querySelectorAll(".map-outline-crossing").length===3&&text("map-outline-status")==="Unfinished outline: 3 points. 3 edges cross or touch.","folded neighbouring edges are crossing edges, with their count");
check(getComputedStyle(document.querySelector(".map-outline-crossing")).strokeWidth==="4px"&&document.querySelectorAll(".map-outline-halo-crossing").length===3,"a crossing edge is wider, with a wider halo");
const discard=document.getElementById("map-outline-discard");discard.focus();discard.click();
check(draft()==="[]"&&[text("map-tool-message"),text("map-tool-notice"),text("map-announcement")].every(value=>value==="Outline discarded.")&&document.activeElement.id==="map-viewport","Discard says so in the panel, on the map and in the live region; focus on the map");
check(document.getElementById("map-toolbar").getAttribute("role")==="group","the tools are a plain group");
for(const [x,z] of [[100,100],[500,100],[100,500]])click(x,z);
check(!document.querySelector(".map-outline-crossing")&&document.querySelectorAll(".map-outline-edge").length===3,"a valid triangle has no crossing");
click(500,500);const crossing=[...document.querySelectorAll(".map-outline-crossing")].map(edge=>edge.dataset.mapEdge).sort().join();
check(crossing==="1,3"&&document.querySelector('[data-map-edge="3"]').classList.contains("map-outline-closing"),"crossing edges, the closing edge too, in the error colour");
check(getComputedStyle(document.querySelector('[data-map-edge="3"]')).stroke!==getComputedStyle(document.querySelector('[data-map-edge="0"]')).stroke,"error colour differs");
const finishButton=document.getElementById("map-outline-finish");finishButton.focus();finishButton.click();
check(text("map-tool-message").includes("crosses or touches itself")&&saved.length===0&&model.snapshot().objects.length===1,"Finish refused locally");
check(document.activeElement===finishButton&&text("map-outline-status").endsWith("2 edges cross or touch."),"a refused Finish keeps the focus; the status counts the crossing edges");
press(region,"Backspace");const outline=draft(),history=model.status().history.length;
const answers=[];window.pywebview.api.save_plan=async(document,revision,selection)=>{saved.push({plan:structuredClone(document),revision,selection});
  return answers.shift()?.(document)??success(revision+1)};
answers.push((document)=>refuse(document.objects[0].id));press(region,"Enter");await wait();await wait();
check(saved.length===2&&saved[0].plan.objects.length===2&&saved[1].plan.objects.length===1,"refusal returns the outline and the save runs again");
check(draft()===outline&&model.snapshot().objects.length===1&&model.status().history.length===history&&!model.status().history.redo,"outline back in the draft; the Finish step left the history");
check(text("map-tool-message").includes("back in the drawing")&&text("map-announcement").includes("crosses or touches itself"),"returned outline announced with the reason");
check(text("map-status")==="Saved"&&model.status().revision===5,"the second save adopted");
let finish;window.pywebview.api.save_plan=(document,revision)=>{saved.push({plan:structuredClone(document),revision});return new Promise(resolve=>finish=resolve)};
press(region,"Enter");await wait();const id=model.snapshot().objects[0].id;click(1200,200);finish(refuse(id));await wait();
check(model.snapshot().objects[0].id===id&&draft().length>2&&document.querySelector(`[data-map-zone="${id}"]`).classList.contains("map-zone-problem"),"a newer outline keeps the refused one marked in the plan");
check(text("map-status").startsWith("Not saved: Loot polygon 1: ")&&saved.length===3,"general refusal path names the object and does not save again");
discard.click();const circle=model.snapshot().objects.find(item=>item.kind==="loot_circle").id;
const triangle=()=>{for(const [x,z] of [[700,700],[900,700],[700,900]])click(x,z)};
triangle();press(region,"Enter");await wait();const renamed=model.snapshot().objects[0].id;model.edit(renamed,{name:"Renamed outline"});
finish(refuse(renamed));await wait();
check(model.snapshot().objects[0].id===renamed&&draft()==="[]"&&document.querySelector(`[data-map-zone="${renamed}"]`).classList.contains("map-zone-problem"),"a later edit of the outline keeps it in the plan, marked");
check(text("map-status").startsWith("Not saved: Renamed outline: ")&&saved.length===4,"no return and no second save at once after a later edit; the status names the current name (QA-4.4-F6)");
triangle();press(region,"Enter");await wait();const other=model.snapshot().objects[0].id;model.edit(circle,{name:"Edited circle"});
finish(refuse(other));await wait();
check(!model.snapshot().objects.some(item=>item.id===other)&&JSON.parse(draft()).length===3&&saved.length===6,"an edit of another object still returns the outline, and the save runs again");
check(!saved[5].plan.objects.some(item=>item.id===other)&&saved[5].plan.objects.some(item=>item.name==="Edited circle"),"the second save keeps the other edit without the outline");
finish(success(6));await wait();press(region,"Enter");await wait();const moved=model.snapshot().objects[0].id;
model.move(model.snapshot().objects[1].id,-1);check(model.snapshot().objects[1].id===moved,"another object moved across the outline");
finish(refuse(moved));await wait();
check(model.snapshot().objects.some(item=>item.id===moved)&&draft()==="[]"&&document.querySelector(`[data-map-zone="${moved}"]`).classList.contains("map-zone-problem")&&saved.length===7,"a reorder across the outline keeps it in the plan, marked, without a second save");
'''), "PASS")

    def test_map_size_stays_while_drawing_at_minimum_window(self) -> None:
        """The footer stays one line high, so the map keeps its size from the first point to Finish (QA-4.4-F8)."""
        self.assertEqual(run_editor_harness(TOOLS + r'''
await load(value(plan()));tool("polygon");const size=()=>[region.clientWidth,region.clientHeight].join(),start=size(),sizes=[];
for(const [x,z] of [[100,100],[500,100],[500,500],[300,700]]){click(x,z);sizes.push(size())}
press(region,"Enter");await wait();sizes.push(size());
check(sizes.every(value=>value===start),`map size stays ${start}: ${sizes.join(" ")}`);
const footer=document.getElementById("map-tool-status");
check(footer.title==="Polygon"&&getComputedStyle(footer).whiteSpace==="nowrap"&&saved.length===1,"one-line footer; Finish saved");
''', window_size="936,508"), "PASS")
