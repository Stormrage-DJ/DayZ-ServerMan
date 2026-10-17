"""Verify autosave and the save status with fake save answers in composed Edge (5.2, QA-4.4-F6)."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_tools_msedge import TOOLS
from tests.ui_harness_support import EDGE, run_editor_harness


# Each save waits for its answer; virtual time runs the 800 ms wait and the 2 s delayed text.
AUTOSAVE = TOOLS + r'''
const sleep=(ms)=>new Promise(resolve=>setTimeout(resolve,ms)),pending=[];
window.pywebview.api.save_plan=(document,revision,selection)=>{saved.push({plan:structuredClone(document),revision,selection});
  return new Promise(resolve=>pending.push(resolve))};
const answer=async(response)=>{pending.shift()(response);await wait()};
const failure=(code,reason,message)=>({success:false,error:{code,message,retryable:false,details:{reason}}});
const status=()=>text("map-status"),reload=()=>document.getElementById("map-save-reload");
const quiet=(expected,message)=>{document.getElementById("map-announcement").textContent="";return ()=>
  check(status()===expected&&text("map-announcement")==="",`${message}: ${status()} / ${text("map-announcement")}`)};
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorAutosaveEdgeTests(unittest.TestCase):
    """Use synthetic plans, fake save answers and isolated headless profiles."""

    def test_autosave_waits_after_each_edit_and_never_shows_saved_early(self) -> None:
        """Not saved yet, then Saving, then Saved only for the draft that the backend accepted; Draft from the answer."""
        self.assertEqual(run_editor_harness(AUTOSAVE + r'''
await load(value(plan()));tool("circle");click(500,400);
check(status()==="Not saved yet"&&saved.length===0&&text("map-draft-changes")==="Updating","an edit waits; Draft changes shows Updating, no browser estimate");
await sleep(500);click(600,400);await sleep(500);check(saved.length===0,"each edit starts the wait again");
const saving=quiet("Saving","routine Saving is not announced");await sleep(400);
check(saved.length===1&&saved[0].plan.objects.length===2&&saved[0].revision===4,"one save of the current draft with its revision");saving();
click(700,400);check(status()==="Saving","an edit during a save keeps Saving");
await answer(success(5,true));
check(status()==="Not saved yet"&&model.status().revision===5&&text("map-draft-changes")==="Updating","never Saved while a newer edit waits");
await sleep(900);check(saved.length===2&&saved[1].plan.objects.length===3&&saved[1].revision===5,"the newer edit saves after its wait");
const done=quiet("Saved","routine Saved is not announced");await answer(success(6,true));done();
check(text("map-draft-changes")==="Yes"&&model.status().draft_changes===true,"Draft changes comes from the save answer");
await model.undo();check(status()==="Not saved yet","undo is an edit");await sleep(900);
check(saved.length===3&&saved[2].plan.objects.length===2,"undo saves after the wait");await answer(success(7,false));
check(status()==="Saved"&&text("map-draft-changes")==="No","backend says the plan equals the applied state");
click(800,400);await model.undo();check(status()==="Saved"&&text("map-draft-changes")==="No","an edit undone within the wait is saved state again");
await sleep(900);check(saved.length===3,"nothing to save");
''', budget=20000), "PASS")

    def test_delayed_save_failure_and_recovery(self) -> None:
        """After 2 s the delayed text is announced; a failure is announced and kept; Saved after a failure is announced."""
        self.assertEqual(run_editor_harness(AUTOSAVE + r'''
await load(value(plan()));tool("circle");click(500,400);await sleep(900);
await sleep(1500);check(status()==="Saving","no delayed text before 2 s");
await sleep(600);const delayed="Saving takes longer than usual. An apply or restore of this mission can delay it.";
check(status()===delayed&&text("map-announcement")===delayed,"delayed-save text shown and announced while the save waits");
check(text("map-save-notice")===delayed&&document.getElementById("map-status").title===delayed,"the full delayed text on the map and in the title");
await answer(success(5));check(status()==="Saved"&&text("map-announcement")==="Saved"&&getComputedStyle(document.getElementById("map-save-notice")).display==="none","the end of a delay is announced once; the notice hides");
click(600,400);await sleep(900);
await answer(failure("STORAGE_FAILURE","storage_failure","The map editor data could not be stored or read."));
check(status().startsWith("Not saved: ")&&text("map-announcement")===status()&&reload().hidden,"a failure shows its reason and is announced");
check(model.status().revision===5&&text("map-draft-changes")==="Not available","a failure adopts nothing");
await sleep(2500);check(saved.length===2&&status().startsWith("Not saved: "),"no save loop and no delayed text after a failure");
click(700,400);check(status()==="Not saved yet","the next edit saves again");await sleep(900);
await answer(success(6));check(status()==="Saved"&&text("map-announcement")==="Saved","Saved after a failure is announced");
click(750,400);await sleep(900);const routine=quiet("Saved","the next routine Saved after a recovered failure is not announced");
await answer(success(7));routine();
click(800,400);await sleep(900);const id=model.snapshot().objects[0].id;
await answer(refuse(id,"the circle radius is too large"));
check(status()==="Not saved: Loot circle 5: the circle radius is too large"&&document.querySelector(`[data-map-zone="${id}"]`).classList.contains("map-zone-problem"),"an object refusal names and marks the object");
model.edit(id,{name:"Big circle"});check(status()==="Not saved yet","an edit of the refused object refreshes the status");
await sleep(900);await answer(refuse(id,"the circle radius is too large"));
check(status()==="Not saved: Big circle: the circle radius is too large","the new refusal names the current name");
await model.undo();await model.undo();
check(status()==="Saved"&&!document.querySelector(".map-zone-problem"),"the refused object left the draft: no stale status or mark");
const first=model.snapshot().objects[2].id;model.edit(first,{radius:5000});await sleep(900);await answer(refuse(first,"the radius is too large"));
check(document.querySelector(`[data-map-zone="${first}"]`).classList.contains("map-zone-problem"),"a refused edit of a saved object marks it");
await model.undo();check(status()==="Saved"&&!document.querySelector(".map-zone-problem"),"undo to the saved plan clears the mark");
const count=saved.length;click(900,400);const closing=window.ServerManMapEditor.settleForClose();await wait();
check(saved.length===count+1&&status()==="Closing","a close saves the waiting edit at once (5.4)");
await answer(success(9));check(await closing==="close"&&status()==="Closing"&&!document.querySelector("dialog"),"the saved draft closes without a dialog");
window.ServerManMapEditor.releaseClose();check(status()==="Saved"&&text("map-announcement")==="Saved","a cancelled close shows and announces the state");
await sleep(900);check(saved.length===count+1,"nothing is left to save after the release");
''', budget=20000), "PASS")

    def test_refusal_after_undo_of_finish_is_not_shown(self) -> None:
        """QA-4.4-F6: an undo of Finish before the refusal leaves no "Not saved"; redo saves the outline again."""
        self.assertEqual(run_editor_harness(AUTOSAVE + r'''
await load(value(plan()));tool("circle");click(500,400);await sleep(900);await answer(success(5));
tool("polygon");for(const [x,z] of [[100,100],[500,100],[100,500]])click(x,z);press(region,"Enter");await wait();
const id=model.snapshot().objects[0].id;check(saved.length===2&&status()==="Saving","Finish saves at once");
await model.undo();check(status()==="Saving","the running save keeps Saving");
await answer(refuse(id));
check(status()==="Saved"&&!document.querySelector(".map-zone-problem")&&draft()==="[]","the refusal of an undone outline is not shown");
await sleep(900);check(saved.length===2,"the draft equals the saved plan, so nothing saves");
await model.redo();check(status()==="Not saved yet","redo is an edit");await sleep(900);
check(saved.length===3&&saved[2].plan.objects[0].id===id,"redo brings the outline back only through a save");
await answer(refuse(id));check(draft()!=="[]"&&!model.snapshot().objects.some(item=>item.id===id),"the refused outline returns to the drawing");
await sleep(900);check(saved.length===3,"nothing is left to save after the return");
''', budget=20000), "PASS")

    def test_conflict_offers_reload_and_stops_autosave(self) -> None:
        """A stale revision or a changed mission path stops autosave and offers Reload plan; reload loads the actual state."""
        self.assertEqual(run_editor_harness(AUTOSAVE + r'''
await load(value(plan()));tool("circle");click(500,400);await sleep(900);
await answer(failure("REVISION_CONFLICT","stale_revision","changed"));
check(status()==="Not saved: The saved map plan changed. Reload plan loads it and discards your unsaved changes."&&text("map-announcement")===status(),"conflict reason shown and announced");
check(text("map-save-notice")===status()&&document.getElementById("map-save-notice").classList.contains("map-save-error")&&text("map-draft-changes")==="Not available","failure on the map; no Draft value after a conflict stop");
check(!reload().hidden&&reload().textContent==="Reload plan"&&getComputedStyle(reload()).display!=="none","Reload plan offered");
click(600,400);await sleep(2500);check(saved.length===1&&status().startsWith("Not saved: The saved map plan changed"),"no autosave after a conflict");
const closing=window.ServerManMapEditor.settleForClose();await sleep(100);const dialog=document.querySelector("dialog");
check(dialog&&![...dialog.querySelectorAll("button")].some(button=>button.textContent==="Save changes"),"a conflict offers no Save changes at close (5.4)");
document.activeElement.click();check(await closing==="cancel","Keep editing has the focus and cancels");
window.ServerManMapEditor.releaseClose();await sleep(900);
check(saved.length===1&&status().startsWith("Not saved: The saved map plan changed")&&!reload().hidden,"a cancelled close keeps the conflict and its reload");
const loads=editorCalls.filter(call=>call==="load").length;editorAnswer={success:true,value:{...value(plan()),state:"VALID",revision:9}};
reload().click();await wait();await wait();
check(editorCalls.filter(call=>call==="load").length===loads+1&&reload().hidden&&model.status().revision===9&&status()==="Map plan loaded","reload loads the actual plan");
click(500,400);await sleep(900);check(saved.length===2&&saved[1].revision===9,"autosave runs again after the reload");
await answer(failure("PROFILE_CONTEXT_MISMATCH","mission_path_changed","The mission path changed. Reload the plan; adoption is required before saving."));
check(status().startsWith("Not saved: ")&&!reload().hidden,"a changed mission path offers the reload");
editorAnswer={success:true,value:{...value(plan()),read_only:true,read_only_reason:"mission_path_changed"}};
reload().click();await wait();await wait();
check(status()==="Read only: the mission path changed. The map plan shown belongs to an earlier path. Adopt the mission to use its map editor records for the new path."&&reload().hidden,"the reload shows the read-only reason (ADOPT-4)");
check(document.getElementById("map-tool-circle").disabled&&!model.add("x",{}),"the path-change state is read only");
''', budget=20000), "PASS")

    def test_read_only_states_show_the_reason_and_never_save(self) -> None:
        """Invalid saved data and the path-change states load read only with the reason; nothing saves."""
        self.assertEqual(run_editor_harness(AUTOSAVE + r'''
const start=plan();start.objects=[{id:"Circle_A",kind:"loot_circle",name:"Loot circle 1",visible:true,locked:false,
  centre:{x:500,z:500},radius:100,tier:null,abundance:"rich"}];
for(const [reason,expected] of [["invalid_saved_data","Read only: the saved map plan is not valid."],
  ["mission_path_changed","Read only: the mission path changed."],["returned_to_earlier_path","Read only: this mission returned to an earlier path."]]){
  await load({...value(start),read_only:true,read_only_reason:reason});
  check(status().startsWith(expected)&&reload().hidden,`${reason}: reason shown`);
  check(!model.visible("Circle_A")&&!model.edit("Circle_A",{name:"x"})&&document.getElementById("map-tool-circle").disabled,`${reason}: no edit`);
  click(800,800);await sleep(1000);check(saved.length===0&&status().startsWith(expected),`${reason}: nothing saves`);
}
''', budget=20000), "PASS")

    def test_no_save_during_a_drag(self) -> None:
        """A save that comes due while a pointer is held on the map waits for the end of the drag and 800 ms more."""
        self.assertEqual(run_editor_harness(AUTOSAVE + r'''
await load(value(plan()));tool("circle");click(500,400);const place=at(1000,500),moved={clientX:place.clientX+60,clientY:place.clientY+40};
pointer("pointerdown",place,1,5);pointer("pointermove",moved,1,5);await sleep(1500);
check(saved.length===0&&status()==="Not saved yet"&&region.classList.contains("map-panning"),"no save during a pan drag");
pointer("pointerup",moved,1,5);await sleep(500);check(saved.length===0,"the wait starts again at the end of the drag");
await sleep(400);check(saved.length===1,"the save runs after the drag");await answer(success(5));
click(600,400);await sleep(300);pointer("pointerdown",at(900,500),0,6);await sleep(1500);
check(saved.length===1,"no save while the primary button is held on the map");
pointer("pointerup",at(900,500),0,6);await sleep(900);check(saved.length===2&&status()==="Saving","the save runs after the release");
''', budget=20000), "PASS")


if __name__ == "__main__":
    unittest.main()
