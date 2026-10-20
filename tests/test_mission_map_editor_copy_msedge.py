"""Verify the flow of Copy from another profile in composed Edge, with a fake host (9.2).

Rules:
- Architect 2026-10-17 17:10:42 in evidence 9.1, sections 5 and 6: one revision-guarded save and one undo step.
  The pause holds through the reload to the model replace. The page names only the source profile.
- UI-UX Designer 17:08:16, sections 4 to 6: the compatibility dialog, the placement review, the gates.
- Product Owner 17:14:17: the texts; the CLI copy forms without their CLI-only lines (PO-91-2).
"""

from __future__ import annotations

import unittest

from tests.mission_map_copy_support import copy_page
from tests.ui_harness_support import EDGE

# The shared-scope helper names of the harness: `lines` reads the dialog body
BODY = r'''
const lines=()=>dialog().querySelector(".map-preview-body").textContent;
'''


def run(body: str) -> str:
    """Run the copy helpers with the fake host."""
    return copy_page(BODY + body)


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class CopyFlowEdgeTests(unittest.TestCase):
    """Fake previews, the real editor modules and isolated headless profiles."""

    def test_same_terrain_is_one_undo_step_and_the_page_names_only_the_source(self) -> None:
        """Compatibility with the source first, no box, Merge saves; undo and redo save new revisions."""
        self.assertEqual(run(r'''
await start();const opener=copyButton(),before=model.snapshot();
opener.focus();opener.click();await shownFor();await pick();
check(dialog().querySelector("h2").textContent===C.title&&document.activeElement===dialog().querySelector("h2"),"title names the source");
check(lines().includes(C.intro)&&lines().indexOf(C.source)>=0&&lines().indexOf(C.source)<lines().indexOf("Terrain: same as this mission"),"intro; the source first");
check(lines().includes("Bounds: same")&&lines().includes("Objects in the source plan: 1"),"compatibility");
check(boxes().length===0&&!radio("replace").checked&&!radio("merge").checked,"no box, no preselected mode");
check(lines().includes("Replace: the objects of the source plan replace the objects of this plan.")&&lines().includes(
  "Merge: the objects of the source plan go above all objects of this plan, in their order."),"copy forms of both effects");
check(!lines().includes("the file")&&!dialog().querySelector(".map-preview-footer").textContent.includes("Stop the DayZ server"),"no file words, no stop sentence");
check(remaining()==="1 choice is still needed before you can copy."&&button(C.confirm).getAttribute("aria-disabled")==="true","the gate");
button(C.confirm).click();check(document.activeElement===radio("replace")&&!copies().length,"a missing choice takes the focus");
await copyFlow("merge");
check(JSON.stringify(copies())===JSON.stringify([["copy","alpha","merge",4,false]]),`payload: ${JSON.stringify(copies())}`);
check(JSON.stringify(host.calls.map(call=>call[0]))==='["sources","preview_copy","copy"]',"one source read, one preview, one copy");
check(slot()==="Map plan copied as revision 5."&&notice()===slot(),`success: ${slot()}`);
const after=model.status();
check(after.revision===5&&!after.dirty&&after.history.length===1&&after.history.undo,"one undo step at the saved revision");
check(window.ServerManMapLayers.selected()==="imp"&&document.activeElement===element("map-show-plan")&&!opener.hasAttribute("aria-disabled"),"selected; focus back");
check(text("map-tool-notice")===""&&after.writable,"the pause ended");
const saves=saved.length;element("map-undo").click();await sleep(1000);
check(JSON.stringify(model.snapshot())===JSON.stringify(before)&&saved.length===saves+1&&saved.at(-1).revision===5,"undo saves the old plan at the next revision");
element("map-redo").click();await sleep(1000);
check(model.snapshot().objects[0].id==="imp"&&saved.at(-1).revision===6,"redo saves the copied plan");
check(host.calls.every(call=>!JSON.stringify(call).includes('"self"')),"no call names the destination profile");
'''), "PASS")

    def test_a_different_terrain_needs_the_placement_review_and_each_box(self) -> None:
        """The warning, the placement box, the left-out list with its box; confirmed only with all boxes."""
        self.assertEqual(run(r'''
await start();
const other={terrain_match:false,imported_terrain_id:"other.terrain",bounds_equal:false,placement_review:true,confirmation_required:true,
  bounds_difference:{x_min:0,z_min:0,width:9000,height:9000},excluded_count:1,
  excluded:[{id:"far",name:"Far zone",kind:"loot_circle"}]};
host.copy_preview=()=>copyPreviews({},{},other);
host.copy_commit=(mode)=>{const result=committed(mode);result.value.report={...report(mode),excluded_count:1,placement_review:true};return result};
await openPicker();await pick();
check(lines().includes("Terrain: other.terrain in the source profile, synthetic in this mission")&&
  lines().includes("Bounds: the source plan differs by X 0, Z 0, width 9000 and height 9000 metres"),"the copy forms of terrain and bounds");
check(lines().includes(W.placement_review)&&lines().includes("Far zone"),"warning and the left-out list");
check(boxes().map(item=>item.parentElement.textContent).join("|")===`${W.placement_box}|Copy without the listed object`,"two boxes, copy words");
check(boxes().every(item=>!item.checked)&&remaining()==="3 choices are still needed before you can copy.","none checked; three choices");
check(inside("h3").map(item=>item.textContent).join().includes("Objects left out because they do not fit the bounds"),"the left-out heading");
radio("replace").click();boxes()[0].click();check(remaining()==="1 choice is still needed before you can copy.","one choice left");
button(C.confirm).click();check(document.activeElement===boxes()[1]&&!copies().length,"the open box takes the focus");
boxes()[1].click();check(remaining()===""&&button(C.confirm).getAttribute("aria-disabled")!=="true","all choices made");
button(C.confirm).click();for(let index=0;index<60&&!copies().length;index++)await wait();await sleep(300);
check(JSON.stringify(copies())===JSON.stringify([["copy","alpha","replace",4,true]]),`confirmed with all boxes: ${JSON.stringify(copies())}`);
check(slot()==="Map plan copied as revision 5. 1 object was left out.",`left out: ${slot()}`);
check(text("map-tool-notice")===W.placement,"the placement reminder stays on the map");
'''), "PASS")

    def test_a_refusal_keeps_the_draft_and_a_stale_plan_takes_the_reload_path(self) -> None:
        """Not copied: with the window text once; the pause ends; a stale revision shows the copy stale text."""
        self.assertEqual(run(r'''
await start();const kept=JSON.stringify(model.snapshot());
host.copy_commit=()=>refusal("no_valid_plan","This profile has no valid saved map plan. Nothing was changed. Run map show for the state of the plan.");
await openPicker();await pick();await copyFlow("merge");
check(slot()==="Not copied: This profile has no valid saved map plan. Nothing was changed."&&notice()===slot(),`destination text once: ${slot()}`);
check(JSON.stringify(model.snapshot())===kept&&!model.status().paused&&text("map-tool-notice")==="","draft kept; the pause ended");
host.copy_commit=()=>refusal("no_valid_plan",C.no_plan);
await openPicker();await pick();await copyFlow("merge");
check(slot()===`Not copied: ${C.no_plan}`,`source text: ${slot()}`);
host.copy_commit=()=>refusal("stale_revision","stale","REVISION_CONFLICT");
await openPicker();await pick();await copyFlow("replace");
check(line()===C.stale&&document.activeElement===element("map-save-reload"),`stale commit: ${line()}`);
await load(value(plan()));
host.copy_commit=null;host.copy_preview=()=>copyPreviews({destination_revision:3},{destination_revision:3});
await openPicker();radio("alpha").click();button(C.continue).click();await sleep(400);
check(!button(C.confirm)&&line()===C.stale,"previews of another revision open no dialog");
'''.replace("line()", "text('map-status')")), "PASS")

    def test_the_pause_holds_until_the_model_replace(self) -> None:
        """No edit during the commit or the reload; the copy slot and the pause text show."""
        self.assertEqual(run(r'''
await start();const reads=[],original=window.pywebview.api.load_plan;
host.during=async()=>{reads.push(["commit",model.status().paused,model.add("Add circle",zone("late"))]);
  check(text("map-tool-notice")===C.paused&&slot()===C.copying,"the pause text and the copying line")};
window.pywebview.api.load_plan=async()=>{reads.push(["load",model.status().paused,model.add("Add circle",zone("late2"))]);return original()};
await openPicker();await pick();await copyFlow("replace");
check(JSON.stringify(reads)===JSON.stringify([["commit",true,false],["load",true,false]]),`paused through the reload: ${JSON.stringify(reads)}`);
check(!model.status().paused&&model.status().history.length===1,"one step, no late edit");
'''), "PASS")

    def test_the_gates_of_copy_and_preview_again(self) -> None:
        """Copy follows the Import gates with the shared reason; a stale dialog reads the same source again."""
        self.assertEqual(run(r'''
await start();const opener=copyButton();
await load({...value(plan()),read_only:true,read_only_reason:"mission_path_changed"});
check(opener.getAttribute("aria-disabled")==="true"&&text("map-import-reason")===W.read_only&&opener.getAttribute("aria-describedby")==="map-import-reason","read only");
await load(value(plan(bounds(),false)));check(text("map-import-reason")===W.bounds,"bounds not confirmed");
await load(value(plan()));check(!opener.hasAttribute("aria-disabled"),"available");
window.ServerManBusy.publish(true,"Apply map plan");opener.click();await wait();
check(opener.getAttribute("aria-disabled")==="true"&&!host.calls.length,"lane busy; a gated click does nothing");
window.ServerManBusy.publish(false);
window.pywebview.api.save_plan=async()=>({success:false,error:{code:"INVALID_REQUEST",message:"radius is too large",retryable:false,
  details:{reason:"invalid_document",problems:[{object_id:"bad",rule:"invalid_document",message:"radius is too large"}]}}});
model.add("Add circle",zone("bad"));opener.click();await sleep(300);
check(!host.calls.length&&!dialog()&&slot()===`${C.not_opened} Zone bad: radius is too large`,`failed settle: ${slot()}`);
await load(value(plan()));await openPicker();await pick();
radio("merge").click();window.ServerManMapPreviewDialog.stale();
check(dialog().querySelector(".map-preview-stale").textContent===C.preview_stale&&!button(C.confirm),"stale text of the copy");
button("Preview again").click();await wait();await sleep(300);
check(JSON.stringify(host.calls.at(-1))===JSON.stringify(["preview_copy","alpha"]),"the same source");
check(dialog().querySelector("h2").textContent===C.title&&!radio("merge").checked&&!radio("replace").checked,"the choices start again");
'''), "PASS")


if __name__ == "__main__":
    unittest.main()
