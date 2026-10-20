"""Verify selection, geometry drags, the press lifecycle, the radius field and Zoom to selection in composed Edge (4.5)."""

from __future__ import annotations

import unittest

from tests.mission_map_display_harness import common
from tests.mission_map_gesture_harness import SELECT
from tests.test_mission_map_editor_autosave_msedge import AUTOSAVE
from tests.test_mission_map_editor_background_msedge import STEPS
from tests.test_mission_map_editor_tools_msedge import TOOLS
from tests.ui_harness_support import EDGE, run_editor_harness


# Drags go through the real viewport listeners as pointer events at native points.



@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorSelectEdgeTests(unittest.TestCase):
    """Use synthetic plans, fake save answers and isolated headless profiles."""

    def test_overlapping_hidden_and_locked_items(self) -> None:
        """The highest visible item wins, the list targets an item below, and a locked item refuses edits."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + r'''
const start=plan();start.objects=[circle("Hidden",1000,500,300,{visible:false}),circle("Top",1000,500,150),circle("Under",1000,500,300),
  circle("Locked",1600,500,100,{locked:true}),polygon("Poly",[[100,100],[500,100],[500,400],[100,400]])];
await load(value(start));tool("circle");const count=objects().length;
check(text("map-properties-empty").startsWith("No item selected")&&document.getElementById("map-properties-fields").hidden,"no selection, no fields");
click(1000,500,true);
check(selected()==="Top"&&objects().length===count&&entries()===0,"Shift+click: the highest visible item wins over a hidden item above; nothing placed");
check(zone("Top").classList.contains("map-zone-selected")&&!zone("Hidden")&&handles()===0,"selection is drawn without handles until Control; hidden item is not drawn");
click(1220,500,true);check(selected()==="Under","only the lower item covers this point");
click(1900,900,true);check(selected()===null&&objects().length===count,"a selecting click on an empty place clears the selection");
check(document.querySelectorAll("#map-list li").length===5,"hidden and locked items stay in the list");
pick("Under");check(selected()==="Under"&&document.querySelector('[data-map-control="Under:select"]').getAttribute("aria-pressed")==="true","the list targets the item underneath");
const top=geometry("Top");drag([1000,500],[1050,520]);
check(near(find("Under").centre.x,1050)&&near(find("Under").centre.z,520)&&geometry("Top")===top,"a drag inside the item above moves the selected item below");
check(entries()===1&&objects().length===count,"one undo action; the click after the drag places nothing");
await model.undo();check(find("Under").centre.x===1000&&find("Under").centre.z===500,"undo restores it");
key(region,"v");check(text("map-tool-status")==="Select"&&document.getElementById("map-tool-select").getAttribute("aria-pressed")==="true","V chooses Select-only mode");
check(!region.classList.contains("map-drawing"),"Select-only mode has no drawing cursor");
click(1000,500);check(selected()==="Top"&&objects().length===count,"Select-only mode: a click selects and never places");
pick("Hidden");
check(text("map-properties-name")==="Loot circle Hidden (Loot circle)"&&text("map-properties-state").includes("Hidden on the map")&&handles()===0,"a hidden item selected in the list shows its properties and no handles");
key(region,"f");const middle=view.toScreen({x:1000,z:500}),snapshot=view.snapshot();
check(Math.abs(middle.x-snapshot.width/2)<1&&Math.abs(middle.y-snapshot.height/2)<1,"Zoom to selection also reaches a hidden item");
document.getElementById("map-fit").click();tool("circle");click(1600,500,true);const locked=geometry("Locked"),field=document.getElementById("map-properties-radius");
check(selected()==="Locked"&&handles()===0&&field.disabled&&text("map-properties-state").startsWith("Locked."),"a locked item selects, has no handles and no radius edit");
drag([1600,500],[1700,500]);
check(geometry("Locked")===locked&&entries()===1&&objects().length===count,"a drag of a locked item changes nothing and places nothing");
check(text("map-tool-notice")===window.ServerManMapWording.objectLocked,"the locked item says why");
model.lock("Locked");holdControl(true);check(handles()===1&&!field.disabled,"unlocked with Control, it has its handle and radius field");
'''), "PASS")

    def test_polygon_edges_and_vertex_drags(self) -> None:
        """Edges hit their zone; a vertex drag that breaks the outline is refused without an undo action."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + r'''
const start=plan();start.objects=[polygon("Upper",[[200,200],[600,200],[600,600],[200,600]]),
  polygon("Lower",[[600,200],[1000,200],[1000,600],[600,600]]),polygon("Notch",[[1200,200],[1800,200],[1800,800],[1500,400],[1200,800]])];
await load(value(start));const hit=(x,z)=>window.ServerManMapTools.hit({x,z});
check(hit(600,400)==="Upper"&&hit(1000,400)==="Lower"&&hit(800,600)==="Lower","on a shared edge the higher polygon wins; an own edge hits the lower one");
check(hit(1350,600)==="Notch"&&hit(1351,600)===null&&hit(1500,700)===null&&hit(1500,300)==="Notch","a concave edge counts as inside, its notch does not");
pick("Upper");holdControl(true);check(handles()===4&&document.querySelector('[data-map-handle="vertex:2"]'),"a polygon has one handle for each vertex");
const upper=geometry("Upper"),points=zone("Upper").getAttribute("points");
drag([600,600],[700,700],{ctrl:true});const moved=find("Upper").vertices,valid=geometry("Upper"),shape=zone("Upper").getAttribute("points");
check(near(moved[2].x,700)&&near(moved[2].z,700)&&moved[0].x===200&&moved[3].z===600&&entries()===1&&position()===1,"a valid vertex drag is one action");
drag([700,700],[400,100],{hold:true,ctrl:true});
check(zone("Upper").getAttribute("points")!==shape&&zone("Upper").classList.contains("map-zone-problem"),"the preview shows the crossing in the error colour");
check(text("map-tool-notice").startsWith("The outline would cross or touch itself.")&&geometry("Upper")===valid&&entries()===1,"the reason shows; the draft and the history do not change during the drag");
key(region,"z",{ctrlKey:true});await wait();check(position()===1&&geometry("Upper")===valid,"during a drag only Escape acts");
release(at(400,100),7);
check(geometry("Upper")===valid&&entries()===1&&text("map-tool-notice")==="The outline would cross or touch itself. The item did not change.","a crossing outline is refused at release");
drag([200,200],[700,400],{ctrl:true});check(geometry("Upper")===valid&&entries()===1,"a crossing closing edge is refused");
drag([200,200],[-50,200],{ctrl:true});check(geometry("Upper")===valid&&text("map-tool-notice").startsWith("The item would leave the terrain bounds."),"a vertex outside the bounds is refused");
drag([700,700],[650,650],{ctrl:true});check(text("map-tool-notice")===""&&!zone("Upper").classList.contains("map-zone-problem")&&entries()===2,"a valid drag clears the reason");
await model.undo();await model.undo();check(geometry("Upper")===upper&&zone("Upper").getAttribute("points")===points,"undo restores the outline");
drag([600,600],[650,650],{hold:true,ctrl:true});key(region,"Escape");
check(zone("Upper").getAttribute("points")===points&&!region.classList.contains("map-dragging")&&selected()==="Upper","Escape cancels the drag and keeps the selection");
release(at(650,650),7);check(geometry("Upper")===upper&&position()===0&&entries()===2,"a cancelled drag adds no action");
'''), "PASS")

    def test_one_undo_action_per_drag_and_the_radius_field(self) -> None:
        """Move, resize and outline moves are one action each; a drag back to its start adds none."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + r'''
const start=plan();start.objects=[circle("C",1000,500,200),polygon("P",[[100,100],[400,100],[400,400]])];
await load(value(start));tool("circle");pick("C");const before=geometry("C");
drag([1000,500],[1200,600],{steps:20,hold:true});
check(geometry("C")===before&&entries()===0&&region.classList.contains("map-dragging"),"20 moves change neither the draft nor the history");
check(Math.abs(Number(zone("C").getAttribute("cx"))-view.toScreen({x:1200,z:600}).x)<1,"the preview follows the pointer");
release(at(1200,600),7);
check(entries()===1&&near(find("C").centre.x,1200)&&near(find("C").centre.z,600)&&objects().length===2,"one action; the click after the drag places nothing");
await model.undo();check(geometry("C")===before,"one undo restores the geometry from before the drag");
await model.redo();const after=geometry("C"),a=at(1200,600),b=at(1350,650);
gesture("pointerdown",a,8);gesture("pointermove",b,8);gesture("pointermove",a,8);release(a,8);
check(geometry("C")===after&&entries()===1&&position()===1&&objects().length===2,"a drag that ends at its start adds no action and places nothing");
// A press 3 pixels inside the edge still takes the handle; the radius grows by the distance moved
const edge=find("C").centre.x+find("C").radius-3/view.snapshot().scale,z=find("C").centre.z;drag([edge,z],[edge+100,z],{ctrl:true});
check(near(find("C").radius,300)&&near(find("C").centre.x,1200)&&entries()===2,"the edge handle resizes the circle as one action");
drag([find("C").centre.x+find("C").radius,z],[5000,z],{ctrl:true});check(find("C").radius===2000&&entries()===3,"a resize stops at the larger terrain extent");
const field=document.getElementById("map-properties-radius");
check(field.value==="2000"&&!field.disabled&&text("map-properties-name")==="Loot circle C (Loot circle)","the radius field shows the selected circle");
field.value="250";field.dispatchEvent(new Event("change"));check(find("C").radius===250&&entries()===4,"the metre field resizes as one action");
field.value="0";field.dispatchEvent(new Event("change"));
check(find("C").radius===250&&entries()===4&&field.getAttribute("aria-invalid")==="true"&&text("map-properties-message").startsWith("Enter a radius from 1 to 2000 m"),"an invalid radius changes nothing and says why");
const moved=geometry("C");drag([find("C").centre.x,z],[2100,z],{hold:true});
check(zone("C").classList.contains("map-zone-problem")&&text("map-tool-notice").startsWith("The item would leave the terrain bounds."),"a centre outside the bounds shows the reason during the drag");
release(at(2100,z),7);check(geometry("C")===moved&&entries()===4,"and is refused at release");
pick("P");const triangle=find("P").vertices;drag([300,200],[350,250]);
check(find("P").vertices.every((vertex,index)=>near(vertex.x,triangle[index].x+50)&&near(vertex.z,triangle[index].z+50))&&entries()===5,"an outline moves as one action");
window.ServerManMapLayers.clear();key(region,"f");check(text("map-tool-notice")==="Select an item first","F without a selection says so");
pick("C");const scale=view.snapshot().scale;document.getElementById("map-zoom-selection").click();
const centre=view.toScreen(find("C").centre),snapshot=view.snapshot();
check(snapshot.scale>scale&&Math.abs(centre.x-snapshot.width/2)<1&&Math.abs(centre.y-snapshot.height/2)<1,"Zoom to selection centres the item and zooms in");
check(2*find("C").radius*snapshot.scale<=0.8*Math.min(snapshot.width,snapshot.height)+0.001,"the whole item is in view");
key(region,"Escape");check(selected()===null&&!document.getElementById("map-properties-empty").hidden,"Escape clears the selection");
'''), "PASS")

    def test_no_autosave_during_a_geometry_drag(self) -> None:
        """A due save waits while a geometry drag holds the pointer; one save after the release has the moved zone."""
        self.assertEqual(run_editor_harness(AUTOSAVE + SELECT + r'''
const start=plan();start.objects=[circle("C",1000,500,200)];
await load(value(start));pick("C");model.edit("C",{name:"Renamed"});
const end=drag([1000,500],[1100,500],{steps:10,id:9,hold:true});await sleep(1500);
check(saved.length===0&&status()==="Not saved yet"&&find("C").centre.x===1000,"no save during a geometry drag; the draft holds no preview");
release(end,9);await sleep(500);check(saved.length===0&&entries()===2,"the wait starts again at the release");
await sleep(400);const sent=saved[0]?.plan.objects[0];
check(saved.length===1&&near(sent.centre.x,1100)&&sent.name==="Renamed","one save after the drag holds the moved geometry");
await answer(success(5));const centre=document.getElementById("map-properties-centre-x");centre.value="1500";centre.dispatchEvent(new Event("change"));
await sleep(500);check(saved.length===1&&entries()===3,"a centre edit is one action and waits 800 ms");
await sleep(400);check(saved.length===2&&saved[1].plan.objects[0].centre.x===1500&&saved[1].plan.objects[0].centre.z===500,"autosave sends the new centre");
''', budget=20000), "PASS")

    def test_press_and_drag_end_without_an_action(self) -> None:
        """A release away from the map, an up button, Escape, blur, pointercancel, a zone change and a load end a press or drag."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + r'''
const start=plan();start.objects=[circle("C",1000,500,200)];await load(value(start));tool("circle");pick("C");
const before=geometry("C"),count=objects().length,panel=document.getElementById("map-panel"),a=at(1000,500);
const away={clientX:a.clientX+60,clientY:a.clientY},back={clientX:a.clientX+120,clientY:a.clientY+40};
gesture("pointerdown",a,1);gesture("pointermove",away,1,1,panel);gesture("pointerup",away,1,0,panel);
gesture("pointermove",back,1,0);check(!window.ServerManMapDrag.active()&&!region.classList.contains("map-dragging"),"a release over the panel ends the press (QA Q01)");
gesture("pointerdown",back,1);gesture("pointerup",back,1);region.dispatchEvent(new MouseEvent("click",{...back,button:0,bubbles:true,cancelable:true}));
check(geometry("C")===before&&entries()===1&&objects().length===count+1,"the next click only places by the tool; the circle did not move");
await model.undo();pick("C");
gesture("pointerdown",a,2);gesture("pointermove",back,2,0);release(back,2);
check(geometry("C")===before&&entries()===1&&position()===0,"a move with the button up ends the press without an action");
gesture("pointerdown",a,5);gesture("pointermove",away,5,1,panel);gesture("pointerup",away,5,0,panel);gesture("pointerdown",away,5,1,panel);
gesture("pointermove",back,5);release(back,5);check(geometry("C")===before&&position()===0,"a press that ends over the panel does not drag after a new press there");
const end=drag([1000,500],[1100,500],{hold:true});gesture("pointermove",back,7,0);
check(!window.ServerManMapDrag.active()&&Math.abs(Number(zone("C").getAttribute("cx"))-view.toScreen({x:1000,z:500}).x)<0.001,"a move with the button up ends a drag and drops its preview");
release(back,7);check(geometry("C")===before&&position()===0,"no action after it");
gesture("pointerdown",a,3);key(region,"Escape");check(selected()===null,"Escape during a press clears the selection");
gesture("pointermove",away,3);release(away,3);check(geometry("C")===before&&position()===0&&!window.ServerManMapDrag.active(),"and ends the press (QA Q12)");
pick("C");drag([1000,500],[1100,500],{hold:true});window.dispatchEvent(new Event("blur"));
check(!window.ServerManMapDrag.active(),"a window blur ends the drag");release(end,7);check(geometry("C")===before&&position()===0,"without an action");
drag([1000,500],[1100,500],{hold:true});gesture("pointercancel",end,7,0);
check(!window.ServerManMapDrag.active(),"pointercancel ends the drag");release(end,7);check(geometry("C")===before&&position()===0,"without an action");
drag([1000,500],[1100,500],{hold:true});model.edit("C",{radius:50});
check(!window.ServerManMapDrag.active()&&Math.abs(Number(zone("C").getAttribute("r"))-50*view.snapshot().scale)<0.001,"a change of the zone ends the drag and drops the preview");
release(end,7);check(find("C").radius===50&&find("C").centre.x===1000&&position()===1,"the release adds no action");
const same=plan();same.objects=[find("C")];drag([1000,500],[1100,500],{hold:true});await load(value(same));
check(!window.ServerManMapDrag.active()&&Math.abs(Number(zone("C").getAttribute("cx"))-view.toScreen({x:1000,z:500}).x)<0.001,"a load of the same zone also ends the drag");
release(end,7);check(find("C").centre.x===1000&&entries()===0,"without an action");
drag([1000,500],[1100,500],{hold:true});const next=plan();next.objects=[circle("C",400,300,100)];await load(value(next));
check(!window.ServerManMapDrag.active()&&Math.abs(Number(zone("C").getAttribute("cx"))-view.toScreen({x:400,z:300}).x)<0.001,"a load ends the drag and drops the preview (QA Q04)");
release(end,7);check(find("C").centre.x===400&&entries()===0,"the release after a load adds no action");
'''), "PASS")

    def test_refusals_focus_rules_and_handle_reach(self) -> None:
        """Too-close vertices, the 1 m floor, the 24 px handle area, key focus rules and refused clicks."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + r'''
const start=plan();start.objects=[circle("C",1000,500,200),polygon("P",[[100,100],[400,100],[400,400]]),circle("L",1600,500,100,{locked:true})];
await load(value(start));pick("P");drag([400,100],[400,400],{ctrl:true});
check(entries()===0&&text("map-tool-notice")==="Two points of the outline would be too close. The item did not change.","a vertex onto its neighbour is refused");
pick("C");drag([1200,500],[1000,500],{ctrl:true});check(find("C").radius===1&&entries()===1,"a resize stops at 1 m");await model.undo();
key(region,"v");const pixel=1/view.snapshot().scale;
drag([1200+11*pixel,500],[1300+11*pixel,500],{ctrl:true});check(near(find("C").radius,300)&&entries()===1,"a press 11 px from the handle centre takes it");
const edge=find("C").centre.x+find("C").radius;drag([edge+13*pixel,500],[edge+100+13*pixel,500],{ctrl:true});check(near(find("C").radius,300)&&entries()===1&&selected()==="C","a press 13 px away does not");
const scale=view.snapshot().scale,row=document.querySelector('[data-map-control="C:visible"]');key(row,"f");
check(view.snapshot().scale===scale,"F on a list button does not zoom");
const field=document.getElementById("map-properties-radius");key(field,"Escape");check(selected()==="C","Escape in a text field keeps the selection");
field.value="2000.001";field.dispatchEvent(new Event("change"));
check(near(find("C").radius,300)&&entries()===1&&field.getAttribute("aria-invalid")==="true","a radius above the larger extent is refused");
key(region,"v");click(1900,900);check(selected()===null,"an empty click in Select-only mode clears the selection");
tool("circle");click(1600,500,true);const count=objects().length,b=at(1600,500),c={clientX:b.clientX+60,clientY:b.clientY};
gesture("pointerdown",b,4);gesture("pointermove",c,4);gesture("pointermove",b,4);release(b,4);
check(selected()==="L"&&objects().length===count&&entries()===1&&text("map-tool-notice")===window.ServerManMapWording.objectLocked,"the click after a refused locked drag places nothing");
'''), "PASS")


    def test_calibration_takes_escape_before_the_selection(self) -> None:
        """Escape first cancels a calibration and keeps the selection; the next Escape clears it."""
        self.assertEqual(run_editor_harness(common() + STEPS + r'''
const document_=plan();document_.objects=[{id:"C",kind:"loot_circle",name:"Loot circle 1",visible:true,locked:false,
  centre:{x:500,z:500},radius:100,tier:null,abundance:"rich"}];
await load(value(document_));await choose();document.querySelector('[data-map-control="C:select"]').click();
const region=document.getElementById("map-viewport"),escape=()=>{region.focus();
  region.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true,cancelable:true}))};
document.getElementById("map-calibrate").click();check(!document.getElementById("map-calibration").hidden,"calibration open");
escape();check(document.getElementById("map-calibration").hidden&&window.ServerManMapLayers.selected()==="C","Escape cancels the calibration and keeps the selection");
escape();check(window.ServerManMapLayers.selected()===null,"the next Escape clears the selection");
''', budget=8000), "PASS")

    def test_authored_interiors_survive_selection_and_original_opacity(self) -> None:
        """Regions retain interiors independently from selection and original overlays."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + r'''
const start=plan();start.objects=[polygon("P",[[200,200],[700,200],[700,700]],{tier:"Tier4"}),
  circle("I",1200,500,150),circle("H",1700,500,100,{visible:false})];
await load(value(start));
const interiors=()=>document.querySelectorAll("#map-zones .map-zone-wash");
check(interiors().length===2&&!zone("H"),"only visible completed regions have interiors");
const initial=[...interiors()].map(node=>node.getAttribute("fill")).join();
pick("P");holdControl(true);check(document.querySelector(".map-zone-selection")&&handles()===3,"selection has its own cue and handles");
window.ServerManMapLayers.clear();
check(!document.querySelector(".map-zone-selection")&&interiors().length===2,"deselection preserves interiors");
for(const opacity of [0,50,100]) {
  const slider=document.getElementById("map-tiers-opacity");slider.value=opacity;slider.dispatchEvent(new Event("input"));
  window.ServerManMapViewportUi.redraw();
  check(interiors().length===2&&[...interiors()].map(node=>node.getAttribute("fill")).join()===initial,
    "original opacity never changes authored interiors");
}
check(getComputedStyle(interiors()[0]).pointerEvents==="none","interiors never intercept geometry input");
// The first coordinate readout uses space reserved before the pointer enters the map.
const beforeCoordinates=JSON.stringify([region.getBoundingClientRect().toJSON(),view.snapshot()]);
pointer("pointermove",at(1000,500));await wait(30);
check(JSON.stringify([region.getBoundingClientRect().toJSON(),view.snapshot()])===beforeCoordinates,
  "first coordinate readout preserves canvas rectangle and camera transform");
// A visible tool warning cannot move the canvas or change an in-progress gesture's transform.
tool("circle");
const beforeWarning=JSON.stringify([region.getBoundingClientRect().toJSON(),view.snapshot()]);
click(-50,500);await wait(30);
const warning=document.getElementById("map-tool-notice");
check(warning.textContent.includes("outside")&&getComputedStyle(warning).display!=="none","tool warning remains visible on the map");
check(JSON.stringify([region.getBoundingClientRect().toJSON(),view.snapshot()])===beforeWarning,
  "tool warning preserves the canvas rectangle and camera transform");
tool("polygon");click(50,50);click(300,50);click(300,300);
check(document.querySelector(".map-outline-wash"),"valid draft has an interior preview");
click(50,300);click(400,100);check(!document.querySelector(".map-outline-wash")&&document.querySelector(".map-outline-crossing"),
  "invalid crossing retains error edges without a valid-area preview");
'''), "PASS")

    def test_control_latches_handle_editing_and_plain_near_handle_moves(self) -> None:
        """Tiny shapes move normally; modifier changes never convert an active gesture."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + r'''
const start=plan();start.objects=[circle("Tiny",1000,500,5),polygon("Small",[[100,100],[110,100],[110,110]])];
await load(value(start));pick("Tiny");check(handles()===0,"handles start hidden");
drag([1005,500],[1105,550]);
check(near(find("Tiny").centre.x,1100)&&near(find("Tiny").centre.z,550)&&find("Tiny").radius===5,"ordinary tiny-circle edge drag moves the whole object");
await model.undo();pick("Small");const vertices=structuredClone(find("Small").vertices);
drag([110,100],[160,150]);
check(find("Small").vertices.every((point,index)=>near(point.x,vertices[index].x+50)&&near(point.z,vertices[index].z+50)),"ordinary close-vertex drag moves every polygon point");
await model.undo();pick("Tiny");holdControl(true);check(handles()===1,"Control exposes the editable handle");
const resize=drag([1005,500],[1050,500],{ctrl:true,hold:true});holdControl(false);
check(handles()===0&&window.ServerManMapDrag.active(),"Control release hides handles without cancelling the latched resize");
release(resize,7);check(near(find("Tiny").radius,50)&&find("Tiny").centre.x===1000,"release still commits the original resize kind");
await model.undo();const move=drag([1000,500],[1100,550],{hold:true});holdControl(true);release(move,7,true);
check(find("Tiny").radius===5&&near(find("Tiny").centre.x,1100),"Control pressed mid-move cannot convert movement to resizing");
await model.undo();holdControl(false);region.dispatchEvent(new KeyboardEvent("keydown",{key:" ",code:"Space",bubbles:true,cancelable:true}));
holdControl(true);const panStart=at(1000,500),panEnd={clientX:panStart.clientX+40,clientY:panStart.clientY+20};
const beforePan=geometry("Tiny");gesture("pointerdown",panStart,19,1,region,true);gesture("pointermove",panEnd,19,1,region,true);
check(handles()===0&&!window.ServerManMapDrag.active(),"Space pan takes Control input before shape editing");
gesture("pointerup",panEnd,19,0,region,true);
check(handles()===1&&geometry("Tiny")===beforePan,"pan release restores Control handles without changing geometry");
region.dispatchEvent(new KeyboardEvent("keyup",{key:" ",code:"Space",ctrlKey:true,bubbles:true}));await wait(0);
check(handles()===1,"Space key release retains Control handle display");
holdControl(true);document.getElementById("map-properties-name-field").focus();
check(handles()===0,"text input retains Control without map handles");
const undoKey=new KeyboardEvent("keydown",{key:"z",ctrlKey:true,bubbles:true,cancelable:true});document.activeElement.dispatchEvent(undoKey);
check(!undoKey.defaultPrevented,"native text undo is not consumed by modifier observation");
'''), "PASS")

if __name__ == "__main__":
    unittest.main()
