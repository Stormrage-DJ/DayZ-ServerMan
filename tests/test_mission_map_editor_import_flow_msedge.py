"""Verify the flow of Import plan in composed Edge, with a fake host (9.1): the save, the undo step and the states.

Rules:
- Architect 2026-10-17 17:10:42 in evidence 9.1, section 5: one revision-guarded save and one undo step. The
  pause holds from the commit through the reload to the model replace. It always ends.
- UI-UX Designer 17:08:16, sections 3 and 6: the gates, the slot, the refusals, the stale dialog and the focus.
- Product Owner 17:14:17: the texts.
"""

from __future__ import annotations

import unittest

from tests.mission_map_transfer_support import transfer_page
from tests.ui_harness_support import EDGE

# Choose a mode, confirm and wait for the end of the flow
FLOW = r'''
const finish=async(mode)=>{radio(mode).click();boxes().forEach(box=>box.click());confirmButton().click();
  for(let index=0;index<60&&(imports().length===0||text("map-operation-status")===W.importing);index++)await wait();await wait()};
'''


def run(body: str) -> str:
    """Run the flow helpers with the fake host."""
    return transfer_page(FLOW + body)


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class ImportFlowEdgeTests(unittest.TestCase):
    """Fake host answers, the real editor modules and isolated headless profiles."""

    def test_success_is_one_undo_step_and_undo_saves_the_old_plan(self) -> None:
        """Merge: the slot, one history entry, the selection and the view; undo and redo save new revisions."""
        self.assertEqual(run(r'''
await start();const opener=element("map-import"),before=model.snapshot(),scale=view.snapshot().scale;
let checking="";window.pywebview.api.select_import_file=async()=>{checking=slot();host.calls.push(["select"]);return previews()};
opener.focus();opener.click();await shownFor();
check(checking===W.checking,`the checking line: ${checking}`);
check(opener.getAttribute("aria-disabled")==="true"&&reasons("map-import")===W.reading,"busy while the dialog is open");
await finish("merge");
check(slot()==="Map plan imported as revision 5."&&notice()===slot()&&text("map-announcement")===slot(),`success: ${slot()}`);
const status1=model.status();
check(status1.revision===5&&!status1.dirty&&status1.history.length===1&&status1.history.undo,"one undo step at the saved revision");
check(JSON.stringify(status1.plan.objects.map(item=>item.id))===JSON.stringify(["imp"]),"the imported plan is the draft");
check(window.ServerManMapLayers.selected()==="imp"&&view.snapshot().scale>scale,"the imported object is selected and in view");
check(document.activeElement===element("map-show-plan")&&!opener.hasAttribute("aria-disabled"),"the focus is back on the opener");
check(text("map-tool-notice")===""&&status1.writable,"the pause ended");
const saves=saved.length;element("map-undo").click();await sleep(1000);
check(JSON.stringify(model.snapshot())===JSON.stringify(before),"undo returns the old plan");
check(saved.length===saves+1&&saved.at(-1).revision===5,"autosave saves the old plan at the next revision");
element("map-redo").click();await sleep(1000);
check(model.snapshot().objects[0].id==="imp"&&saved.length===saves+2&&saved.at(-1).revision===6,"redo saves the imported plan");
'''), "PASS")

    def test_left_out_objects_and_the_placement_reminder(self) -> None:
        """The left-out line follows the success; the reminder stays on the map until the next edit."""
        self.assertEqual(run(r'''
await start();
host.commit=(mode)=>{const result=committed(mode);result.value.report={...report(mode),excluded_count:2,placement_review:true};return result};
await begin(previews({},{},{placement_review:true,confirmation_required:true,terrain_match:false}));await finish("replace");
check(slot()==="Map plan imported as revision 5. 2 objects were left out.",`left out: ${slot()}`);
check(text("map-tool-notice")===W.placement&&text("map-tool-message")===W.placement,"the reminder shows on the map");
await sleep(200);check(text("map-tool-notice")===W.placement,"it stays");
model.add("Add circle",zone("edit",900,700));await wait();
check(text("map-tool-notice")===""&&text("map-tool-message")==="","the next edit ends it");
'''), "PASS")

    def test_the_pause_holds_until_the_model_replace_and_always_ends(self) -> None:
        """No edit during the commit or the reload; a refusal ends the pause and keeps the draft."""
        self.assertEqual(run(r'''
await start();const reads=[],original=window.pywebview.api.load_plan;
host.during=async()=>{reads.push(["commit",model.status().paused,model.add("Add circle",zone("late"))]);
  check(text("map-tool-notice")===W.paused&&slot()===W.importing,"the pause text and the importing line")};
window.pywebview.api.load_plan=async()=>{reads.push(["load",model.status().paused,model.add("Add circle",zone("late2"))]);return original()};
await begin();await finish("replace");
check(JSON.stringify(reads)===JSON.stringify([["commit",true,false],["load",true,false]]),`paused through the reload: ${JSON.stringify(reads)}`);
check(!model.status().paused&&model.status().history.length===1&&!model.snapshot().objects.some(item=>item.id.startsWith("late")),"one step, no late edit");
const kept=JSON.stringify(model.snapshot());window.pywebview.api.load_plan=original;host.during=null;
host.commit=()=>({success:false,error:{code:"INVALID_REQUEST",message:"This profile has no valid saved map plan. Nothing was changed. Run map show for the state of the plan.",
  retryable:false,details:{reason:"no_valid_plan"}}});
await begin(previews({destination_revision:5},{destination_revision:5}));await finish("merge");
check(slot()===`Not imported: ${W.no_valid_plan}`&&notice()===slot(),`the window text once: ${slot()}`);
check(JSON.stringify(model.snapshot())===kept&&!model.status().paused&&text("map-tool-notice")==="","the draft stays and the pause ended");
host.commit=()=>({success:false,error:{code:"INVALID_REQUEST",message:"The map editor no longer has the chosen file.",retryable:false,
  details:{reason:"import_selection_unknown"}}});
await begin(previews({destination_revision:5},{destination_revision:5}));await finish("merge");
check(slot()===W.unknown,`the unknown file: ${slot()}`);
'''), "PASS")

    def test_a_stale_revision_takes_the_reload_path(self) -> None:
        """A stale commit and old previews show XFER-14 stale with Reload plan, which has the focus."""
        self.assertEqual(run(r'''
await start();
host.commit=()=>({success:false,error:{code:"REVISION_CONFLICT",message:"stale",retryable:true,details:{reason:"stale_revision"}}});
await begin();await finish("replace");
check(status()===W.stale&&!element("map-save-reload").hidden&&document.activeElement===element("map-save-reload"),`stale commit: ${status()}`);
check(window.ServerManMapAutosave.blocked()&&!element("map-plan-export").hidden,"autosave stops; Export plan shows");
await load(value(plan()));check(!window.ServerManMapAutosave.blocked(),"a load starts again");
host.select=previews({destination_revision:3},{destination_revision:3});element("map-import").click();await sleep(300);
check(!dialog()&&status()===W.stale,"previews of another revision open no dialog");
'''), "PASS")

    def test_the_gates_of_import(self) -> None:
        """Read only, bounds not confirmed, a busy lane, a failed settle, a cancel and the file problems."""
        self.assertEqual(run(r'''
await start();const opener=element("map-import");
await load({...value(plan()),read_only:true,read_only_reason:"mission_path_changed"});
check(opener.getAttribute("aria-disabled")==="true"&&reasons("map-import")===W.read_only,`read only: ${reasons("map-import")}`);
await load(value(plan(bounds(),false)));check(reasons("map-import")===W.bounds,"bounds not confirmed");
await load(value(plan()));check(!opener.hasAttribute("aria-disabled"),"available");
window.ServerManBusy.publish(true,"Apply map plan");check(reasons("map-import")===W.busy&&!element("map-export").hasAttribute("aria-disabled"),"lane busy; Export stays available");
opener.click();await wait();check(!host.calls.length,"a gated click does nothing");
window.ServerManBusy.publish(false);check(!opener.hasAttribute("aria-disabled"),"the lane is free");
window.pywebview.api.save_plan=async()=>({success:false,error:{code:"INVALID_REQUEST",message:"radius is too large",retryable:false,
  details:{reason:"invalid_document",problems:[{object_id:"bad",rule:"invalid_document",message:"radius is too large"}]}}});
model.add("Add circle",zone("bad"));opener.click();await sleep(300);
check(!host.calls.length&&!dialog()&&slot()===`${W.not_opened} Zone bad: radius is too large`,`failed settle: ${slot()}`);
await load(value(plan()));host.select={success:true,value:{cancelled:true}};opener.focus();opener.click();await sleep(200);
check(slot()===""&&document.activeElement===element("map-show-plan")&&!dialog(),"cancel: no text, the focus on the opener");
host.select={success:false,error:{code:"INVALID_REQUEST",message:"host",retryable:false,details:{reason:"import_file_refused",kind:"too_large",file_name:"big.json"}}};
opener.click();await sleep(200);check(slot()==="big.json is larger than 8 MiB. Nothing was changed."&&notice()===slot(),`file problem: ${slot()}`);
'''), "PASS")

    def test_a_stale_dialog_reads_both_previews_again(self) -> None:
        """An operation end makes the dialog stale; Preview again reads the same file; the choices start again."""
        self.assertEqual(run(r'''
await start();await begin(previews({},{},{placement_review:true,confirmation_required:true,terrain_match:false}));
boxes()[0].click();radio("merge").click();window.ServerManMapPreviewDialog.stale();
check(dialog().querySelector(".map-preview-stale").textContent===W.preview_stale&&!confirmButton()&&button("Preview again"),"stale");
check(boxes()[0].disabled&&radio("merge").disabled,"the choices lock");
button("Preview again").click();await wait();await shownFor();
check(JSON.stringify(host.calls.at(-1))===JSON.stringify(["preview",selection]),"the same file");
check(!boxes()[0].checked&&!radio("merge").checked&&!radio("replace").checked,"the choices start unchecked");
'''), "PASS")


if __name__ == "__main__":
    unittest.main()
