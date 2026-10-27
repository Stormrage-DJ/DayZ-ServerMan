"""Verify the editor follower, the busy lock, the stale preview and the refresh in composed Edge (8.18).

Rules of the Architect (2026-10-15 15:46:42, ruling 6):
- the follower reads every 1.5 s, and the first read does not act;
- an expired cursor or a new session is one unknown change;
- the busy state goes through ServerManBusy;
- a map-affecting end refreshes the editor and keeps unsaved work, and Inspect drops old answers.
UI-UX Designer 2026-10-15 15:58:12, sections 4 and 6: the busy confirm button and the stale preview.
"""

from __future__ import annotations

import json
import unittest

from tests.mission_map_publish_support import page
from tests.test_mission_map_preview_dialog_msedge import APPLY
from tests.ui_harness_support import EDGE

# Count the outcome calls of the Inspect reads; the refresh calls the global, so a wrapper sees each call
SPY = r'''
const outcomes=[];const inspectRead=window.ServerManMapInspectRead;
window.ServerManMapInspectRead=Object.freeze({...inspectRead,outcome:()=>{outcomes.push(Date.now());inspectRead.outcome();}});
const locked=(control,name)=>check(control.getAttribute("aria-disabled")==="true"&&control.title===reason
  &&(control.getAttribute("aria-describedby")||"").split(" ").includes("busy-reason"),`${name} is locked: ${control.title}`);
const free=(control,name)=>check(control.getAttribute("aria-disabled")!=="true"
  &&!(control.getAttribute("aria-describedby")||"").includes("busy-reason"),`${name} is free`);
const reason="Not available while “Applying medical loot setting” is in progress.";
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class PublishFollowEdgeTests(unittest.TestCase):
    """Fake answers, the real editor modules and isolated headless profiles."""

    def test_a_medical_toggle_refreshes_an_editor_with_unsaved_work_and_keeps_it(self) -> None:
        """Busy lock while it runs; at its end the open preview is stale and the editor refreshes, keeping the draft."""
        self.assertEqual(page(SPY + r'''
await start();tool("circle");click(500,400);
const id=model.snapshot().objects[0].id;
window.pywebview.api.save_plan=async(document,revision)=>{saved.push({plan:structuredClone(document),revision});
  return refuse(id,"the circle is not valid")};
activate("previous");await opened();
emit(record("medical","APPLY_MEDICAL_FEATURE","RUNNING",{target_profile_id:"bravo"}));await poll();
check(element("busy-reason").textContent===reason,"the editor publishes the lane-wide busy state");
for(const id_ of ["map-apply","map-restore","map-restore-previous","map-restore-original"])locked(element(id_),id_);
const confirm=action("Restore previous state");locked(confirm,"the preview confirm button");
const line=shown().querySelector(".busy-wait-line");
check(line?.textContent==="Wait: “Applying medical loot setting” is in progress."&&line.nextElementSibling.classList.contains("action-row"),
  "the wait line above the buttons");
check(getComputedStyle(line).fontWeight==="700","the wait line has the main-window look");
confirm.click();await wait();check(shown()&&!lane.calls.some(call=>call[0]==="restore"),"a locked confirm submits nothing");
element("map-apply").click();await wait();check(lane.calls.filter(call=>call[0]==="preview").length===1,"a locked opener opens nothing");
check(slot()===""&&element("map-operation-item").hidden===false,"an operation of another origin does not show in the slot");
const before=loads(),undo=model.status().history.undo,objects=JSON.stringify(model.snapshot().objects);
const caps={...value(plan()).capabilities,family:{terrain_id:"synthetic",verified:false,reason:"family_not_verified",
  text:"Apply and Restore original are not available for this mission. Family text.",refused:["apply","restore_original"]}};
editorAnswer={success:true,value:{...value(plan()),capabilities:caps,draft_changes:true}};
emit(record("medical","APPLY_MEDICAL_FEATURE","SUCCEEDED",{target_profile_id:"bravo",result:{message:"Medical loot setting applied."}}));await poll();
check(element("busy-reason").textContent===""&&!line.isConnected,"the end of the lane operation unlocks; the wait line goes");
const status=shown().querySelector('[role="status"].map-preview-status');
check(status.textContent===window.ServerManMapPreviewWords.stale.restore&&action("Preview again")&&!action("Restore previous state"),
  "the open preview is stale and offers Preview again");
free(action("Preview again"),"Preview again");free(element("map-apply"),"Apply");
check(loads()===before+1&&saved.length>=2,"the refresh settled the pending save first, then loaded again");
check(JSON.stringify(model.snapshot().objects)===objects&&model.status().dirty&&model.status().history.undo===undo,
  "the unsaved draft and its undo history stay");
check(text("map-family-reason")===caps.family.text&&!element("map-family-reason").hidden,"the loaded capabilities are adopted");
check(element("map-apply").getAttribute("aria-describedby")==="map-family-reason"
  &&element("map-restore-original").getAttribute("aria-describedby")==="map-family-reason"
  &&!element("map-restore-previous").hasAttribute("aria-describedby"),"the family reason describes Apply and Restore original");
check(outcomes.length===1,`Inspect drops answers of earlier jobs once: ${outcomes.length}`);
action("Preview again").click();await opened();
check(lane.calls.filter(call=>call[0]==="preview").length===2&&action("Restore previous state"),"Preview again reads again");
action("Cancel").click();await wait();
'''), "PASS")

    def test_a_clean_draft_refreshes_after_another_end_and_a_new_revision_with_unsaved_work_guides(self) -> None:
        """A map-affecting end refreshes; another kind does not. A changed revision with unsaved work shows Reload plan."""
        self.assertEqual(page(SPY + r'''
await start();const before=loads();
emit(record("backup","CREATE_BACKUP","RUNNING"));await poll();
check(element("busy-reason").textContent==="Not available while “Creating backup” is in progress.","the backup holds the lane");
emit(record("backup","CREATE_BACKUP","SUCCEEDED",{result:{}}));await poll();
check(loads()===before&&outcomes.length===0&&element("busy-reason").textContent==="","another kind refreshes nothing");
editorAnswer={success:true,value:{...value(plan()),draft_changes:true}};
emit(record("other","APPLY_MISSION_MAP","SUCCEEDED",{target_profile_id:"bravo",result:{result:"APPLIED"}}));await poll();
check(loads()===before+1&&model.status().draft_changes===true&&text("map-draft-changes")==="Yes","a clean draft adopts the new comparison");
check(outcomes.length===1&&slot()===""&&element("map-operation-item").hidden,"Inspect reads again; the slot stays hidden");
check(text("map-save-notice")!==T.reloaded&&text("map-announcement")!==T.reloaded,"the same saved revision shows no reload notice");
tool("circle");click(500,400);
window.pywebview.api.save_plan=async()=>refuse(model.snapshot().objects[0].id,"not valid");
editorAnswer={success:true,value:{...value(plan()),revision:9}};
emit(record("other2","RESTORE_MISSION_MAP_ORIGINAL","SUCCEEDED",{result:{result:"RESTORED"}}));await poll();
check(text("map-status")===window.ServerManMapWording.reloadStale&&!element("map-save-reload").hidden,
  "another saved revision with unsaved work shows the stale-revision guidance");
check(model.snapshot().objects.length===1&&model.status().revision===4,"the draft stays; nothing is loaded over it");
'''), "PASS")

    def test_another_saved_revision_without_unsaved_work_loads_again_and_says_so(self) -> None:
        """The Product Owner notice of 2026-10-16 15:50:59: exact, on the map, announced once; the history starts again."""
        self.assertEqual(page(SPY + r'''
await start();tool("circle");click(500,400);await sleep(1000);await wait();
check(!model.status().dirty&&model.status().revision===5&&model.status().history.undo,"a saved edit with an undo step");
editorAnswer={success:true,value:{...value(plan()),revision:9}};const announced=[];
new MutationObserver(()=>announced.push(text("map-announcement"))).observe(element("map-announcement"),{childList:true,characterData:true,subtree:true});
emit(record("other","RESTORE_MISSION_MAP_PREVIOUS","SUCCEEDED",{target_profile_id:"bravo",result:{result:"RESTORED"}}));await poll();
check(model.status().revision===9&&!model.status().history.undo&&model.snapshot().objects.length===0,"the newer saved plan is loaded");
check(text("map-status")===T.reloaded&&text("map-save-notice")===T.reloaded,`the notice on the map: ${text("map-save-notice")}`);
check(announced.filter(item=>item===T.reloaded).length===1&&text("map-announcement")===T.reloaded,`announced once: ${announced}`);
await poll();check(announced.filter(item=>item===T.reloaded).length===1,"no second announcement");
// Product Owner 2026-10-16 16:25:27: an editable plan with a load detail keeps the detail in the status and shows the notice
editorAnswer={success:true,value:{...value(plan()),revision:10,detail:"The saved plan has a load detail."}};
emit(record("other2","APPLY_MEDICAL_FEATURE","SUCCEEDED",{result:{}}));await poll();
check(model.status().revision===10&&text("map-status").includes("The saved plan has a load detail.")
  &&text("map-save-notice")===T.reloaded&&text("map-announcement")===T.reloaded,
  `an editable plan with a detail: ${text("map-status")} / ${text("map-save-notice")}`);
check(announced.filter(item=>item===T.reloaded).length===2,"announced once for this reload");
// A read-only plan stays silent: its status names the reason and the way out
editorAnswer={success:true,value:{...value(plan()),revision:11,read_only:true,read_only_reason:"invalid_saved_data"}};
emit(record("other3","RESTORE_MISSION_MAP_ORIGINAL","SUCCEEDED",{result:{}}));await poll();
check(model.status().revision===11&&text("map-status")===window.ServerManMapWording.readOnly("invalid_saved_data")
  &&text("map-save-notice")!==T.reloaded&&announced.filter(item=>item===T.reloaded).length===2,
  `a read-only plan has no reload notice: ${text("map-save-notice")}`);
'''), "PASS")

    def test_an_operation_inside_the_kept_history_locks_after_a_refused_cursor(self) -> None:
        """The search returns the oldest accepted cursor, so a kept start of a running operation is read."""
        self.assertEqual(page(SPY + r'''
await start();const base=lane.sequence;
for(let index=1;index<=21;index++)emit(record(`gone${index}`,"CREATE_BACKUP","SUCCEEDED"));
emit(record("keeper","CREATE_BACKUP","RUNNING"));
for(let index=0;index<38;index++)emit(record(`late${index}`,"CREATE_BACKUP","SUCCEEDED"));
lane.floor=base+21;await poll();
check(lane.sequence===base+60&&lane.records.get("keeper").state==="RUNNING","the kept history starts after base+20");
check(lane.calls.some(call=>call[0]==="events"&&call[1]===base+31),"the doubling step accepted a cursor past the kept start");
check(lane.calls.some(call=>call[0]==="operation"&&call[1]==="keeper"),"the running operation was read");
check(element("busy-reason").textContent==="Not available while “Creating backup” is in progress."
  &&element("map-apply").getAttribute("aria-disabled")==="true","the running operation locks the editor");
check(outcomes.length===1,"one unknown change");
'''), "PASS")

    def test_the_first_read_does_not_act_and_an_expired_cursor_is_an_unknown_change(self) -> None:
        """Ends before the editor opened refresh nothing; an expired cursor and a new session each refresh once."""
        self.assertEqual(page(SPY + r'''
emit(record("old","APPLY_MISSION_MAP","SUCCEEDED",{result:{}}));emit(record("busy","CREATE_BACKUP","RUNNING"));
await start();const before=loads();
check(outcomes.length===0&&loads()===before,"the first read only learns the state");
check(element("busy-reason").textContent==="Not available while “Creating backup” is in progress.","an operation that already runs holds the lane");
emit(record("busy","CREATE_BACKUP","SUCCEEDED",{result:{}}));await poll();check(element("busy-reason").textContent==="","its end unlocks");
activate("original");await opened();
for(let index=0;index<40;index++)emit(record(`gone${index}`,"CREATE_BACKUP","SUCCEEDED"));
lane.floor=lane.sequence+1;await poll();
check(loads()===before+1&&outcomes.length===1,"an expired cursor counts as one unknown change");
check(shown().querySelector(".map-preview-stale")&&action("Preview again"),"the open preview is stale");
emit(record("next","APPLY_MEDICAL_FEATURE","SUCCEEDED",{result:{}}));lane.floor=1;await poll();
check(loads()===before+2,"the search found the end of the history, so the next end is read");
lane.session="s2";await poll();check(loads()===before+3,"a new session counts as one unknown change");
await poll();check(loads()===before+3,"no change, no refresh");
const reads=lane.calls.filter(call=>call[0]==="events").length;document.dispatchEvent(new Event("visibilitychange"));await wait();
check(lane.calls.filter(call=>call[0]==="events").length===reads+1,"a visible document reads at once");
action("Cancel").click();await wait();
'''), "PASS")

    def test_the_busy_lock_and_the_choice_gate_never_clear_each_other(self) -> None:
        """A busy lane locks the gated confirm; choices made while busy stay locked; the end gives the gate back."""
        self.assertEqual(page(SPY + r'''
await start();lane.previews.apply={success:true,value:__CHOICES__};activate("apply");await opened();
const confirm=action("Apply"),radios=()=>[...shown().querySelectorAll('input[type="radio"]')],boxes=()=>[...shown().querySelectorAll('input[type="checkbox"]')];
check(confirm.getAttribute("aria-disabled")==="true"&&confirm.getAttribute("aria-describedby")?.startsWith("map-preview-remaining"),"the choice gate");
confirm.click();check(document.activeElement===radios()[0],"a gated activation moves the focus to the first open choice");
emit(record("medical","APPLY_MEDICAL_FEATURE","RUNNING"));await poll();
check(confirm.getAttribute("aria-disabled")==="true"&&confirm.title===reason&&confirm.getAttribute("aria-describedby").endsWith("busy-reason"),
  "the busy lock holds the button with its reason");
for(const index of [1,3,5])radios()[index].click();for(const box_ of boxes())box_.click();await wait();
check(text(shown().querySelector("[id^=map-preview-remaining]").id)===""&&confirm.getAttribute("aria-disabled")==="true",
  "all choices made while busy: the lock stays");
confirm.click();await wait();check(shown()&&!lane.calls.some(call=>call[0]==="apply"),"a busy confirm submits nothing");
boxes()[0].click();await wait();
emit(record("medical","APPLY_MEDICAL_FEATURE","SUCCEEDED",{result:{}}));await poll();
check(confirm.isConnected===false||confirm.getAttribute("aria-disabled")==="true","the stale or gated button stays unavailable");
check(action("Preview again"),"the end of a map-affecting operation makes the preview stale");
action("Cancel").click();await wait();
emit(record("backup","CREATE_BACKUP","RUNNING"));await poll();
lane.previews.apply={success:true,value:__CHOICES__};element("map-apply").click();await wait();
check(!shown(),"a busy Apply opens nothing");
emit(record("backup","CREATE_BACKUP","SUCCEEDED",{result:{}}));await poll();
activate("apply");await opened();const next=action("Apply");
emit(record("backup2","CREATE_BACKUP","RUNNING"));await poll();
for(const index of [1,3,5])radios()[index].click();for(const box_ of boxes())box_.click();await wait();
emit(record("backup2","CREATE_BACKUP","SUCCEEDED",{result:{}}));await poll();
check(!next.hasAttribute("aria-disabled")&&next.getAttribute("aria-describedby")?.startsWith("map-preview-remaining")
  &&!(next.getAttribute("title")),"after the lock, a complete choice set gives the button back");
next.click();await wait();await wait();
check(lane.calls.some(call=>call[0]==="apply"),"the confirmed Apply is submitted");
'''.replace("__CHOICES__", json.dumps(APPLY))), "PASS")


if __name__ == "__main__":
    unittest.main()
