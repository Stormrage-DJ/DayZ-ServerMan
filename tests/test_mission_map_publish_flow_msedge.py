"""Verify Apply and each restore from the editor window to each end state, with a fake lane in composed Edge (8.18).

Rules:
- Architect 2026-10-15 15:46:42, ruling 6: the submission, the pause, the own operation and the refresh.
- UI-UX Designer 2026-10-15 15:58:12, sections 2 and 7: the slot, the map notice and the paused editor.
- Product Owner 2026-10-15 15:57:10 and 16:03:40 in evidence 8.10: the texts.
"""

from __future__ import annotations

import json
import unittest

from dayz_serverman.domain.mission_map_families import family_gate
from tests.mission_map_publish_support import APPLY_LATE, RESTORE_LATE, TEXTS, page
from tests.ui_harness_support import EDGE

# One run of a mode to an end state: reading, the dialog, the submission, queued, running and the end
CYCLE = r'''
const cycle=async(mode,end,expected,look,board)=>{
  lane.hold=true;activate(mode);await wait();
  check(slot()===T.reading&&looks()===""&&!element("map-operation-item").hidden&&text("map-announcement")!==T.reading,
    `${mode}: the reading text, muted and not announced: ${slot()}`);
  lane.release();await opened();check(slot()==="","the reading text ends when the preview opens");
  element("map-announcement").textContent="";
  action({apply:"Apply",previous:"Restore previous state",original:"Restore original"}[mode]).click();await wait();await wait();
  const queued=own(),name=window.ServerManOperationLabels.kind(kinds[mode]).name;
  check(queued?.kind===kinds[mode]&&queued.state==="QUEUED","submitted under the kind of the mode");
  check(slot()===`${name} · Waiting`&&text("map-announcement")===slot()&&looks()==="",`queued: ${slot()}`);
  check(text("map-tool-notice")===(mode==="apply"?T.paused_apply:T.paused_restore)&&!model.status().writable,"paused, with the pause text");
  check(element("map-apply").getAttribute("aria-disabled")==="true"&&element("map-restore").getAttribute("aria-disabled")==="true",
    "the own operation locks the openers");
  element("map-announcement").textContent="";
  emit(record(queued.operation_id,queued.kind,"RUNNING"));await poll();
  check(slot()===name&&text("map-announcement")==="","running: the kind label alone, not announced");
  const before=loads();
  const last=emit(record(queued.operation_id,queued.kind,end.state,end.extra??{}));await poll();
  const wanted=expected??window.ServerManOperationMessages.result(last).text;
  check(slot()===wanted&&element("map-operation-status").title===wanted,`${mode} ${end.state}: ${slot()} / ${wanted}`);
  check(looks()===look&&notice()===(board===null?"":wanted)&&noticeLook()===(board==="recovery"?"map-operation-recovery":""),
    `looks: ${looks()} / ${noticeLook()} / ${notice()}`);
  check(text("map-announcement")===wanted,"the end is announced");
  check(text("map-tool-notice")===""&&model.status().writable,"the pause ended");
  check(loads()===before+1,"the editor loaded again");
  check(element("map-check-again").hidden===(end.state!=="RECOVERY_REQUIRED"),"Check again shows only for a recovery block");
  check(element("map-apply").getAttribute("aria-disabled")!=="true","the end unlocks the openers");
  return wanted;
};
const failed=(code,message)=>({terminal_error:{code,message,retryable:code==="REVISION_CONFLICT"},progress_phase:"failed"});
const recovery={terminal_error:{code:"RECOVERY_REQUIRED",message:"The mission map apply requires recovery.",retryable:false},
  progress_phase:"failed"};
'''


def flow(body: str) -> str:
    """Run the cycle helpers with the fake lane."""
    return page(CYCLE + body, budget=60000)


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class PublishFlowEdgeTests(unittest.TestCase):
    """Fake answers, the real editor modules and isolated headless profiles."""

    def test_apply_reaches_each_end_state(self) -> None:
        """Success, an unchanged success, a late file change and a recovery block, with the reviewed payload."""
        late = "The mission map could not be applied. " + TEXTS["late_apply"]
        self.assertEqual(flow(r'''
await start();
await cycle("apply",{state:"SUCCEEDED",extra:{result:{result:"APPLIED"}}},"Mission map applied.","map-operation-done",null);
check(JSON.stringify(lane.calls.find(call=>call[0]==="apply"))===JSON.stringify(["apply","f".repeat(64),
  {external_changes:{},unavailable_encounters:{}}]),"the reviewed fingerprint and the choices");
await cycle("apply",{state:"SUCCEEDED",extra:{result:{result:"UNCHANGED"}}},"Mission map applied.","map-operation-done",null);
await cycle("apply",{state:"FAILED",extra:failed("REVISION_CONFLICT",'''+json.dumps(APPLY_LATE)+''')},'''+json.dumps(late)+''',
  "map-operation-error","error");
const text3=await cycle("apply",{state:"RECOVERY_REQUIRED",extra:recovery},null,"map-operation-recovery","recovery");
check(text3.startsWith("The mission map could not be applied. Changes are blocked."),`recovery text: ${text3}`);
check(lane.calls.filter(call=>call[0]==="preview").length===4&&saved.length===0,"four previews; no save of an unchanged draft");
'''), "PASS")

    def test_restore_previous_state_reaches_each_end_state(self) -> None:
        """Success, the late file change with the host text, the stale review and a recovery block."""
        late = "The previous mission map could not be restored. " + RESTORE_LATE
        stale = "The previous mission map could not be restored. " + TEXTS["stale_restore"]
        self.assertEqual(flow(r'''
await start();
await cycle("previous",{state:"SUCCEEDED",extra:{result:{result:"UNCHANGED"}}},"Previous mission map restored.","map-operation-done",null);
check(JSON.stringify(lane.calls.find(call=>call[0]==="restore"))===JSON.stringify(["restore","previous","e".repeat(64)]),
  "the mode and the reviewed fingerprint");
await cycle("previous",{state:"FAILED",extra:failed("REVISION_CONFLICT",'''+json.dumps(RESTORE_LATE)+''')},'''+json.dumps(late)+''',
  "map-operation-error","error");
await cycle("previous",{state:"FAILED",extra:failed("REVISION_CONFLICT",'''+json.dumps(TEXTS["stale_restore"])+''')},'''
            + json.dumps(stale) + r''',"map-operation-error","error");
await cycle("previous",{state:"RECOVERY_REQUIRED",extra:recovery},null,"map-operation-recovery","recovery");
check(lane.calls.every(call=>call[0]!=="preview"||call[1]==="previous")&&!lane.calls.some(call=>call[0]==="apply"),"restore previews only");
'''), "PASS")

    def test_restore_original_reaches_each_end_state(self) -> None:
        """Success, the late file change with the host text, the family refusal and a recovery block."""
        late = "The original mission map could not be restored. " + RESTORE_LATE
        self.assertEqual(flow(r'''
await start();
await cycle("original",{state:"SUCCEEDED",extra:{result:{result:"RESTORED"}}},"Original mission map restored.","map-operation-done",null);
await cycle("original",{state:"FAILED",extra:failed("REVISION_CONFLICT",'''+json.dumps(RESTORE_LATE)+''')},'''+json.dumps(late)+''',
  "map-operation-error","error");
const gate='''+json.dumps(family_gate("deerisle").to_dict()["text"])+''';
await cycle("original",{state:"FAILED",extra:failed("UNSUPPORTED_SNAPSHOT_CONTENT",gate)},
  `The original mission map could not be restored. ${gate}`,"map-operation-error","error");
await cycle("original",{state:"RECOVERY_REQUIRED",extra:recovery},null,"map-operation-recovery","recovery");
check(JSON.stringify(lane.calls.filter(call=>call[0]==="restore").map(call=>call[1]))===JSON.stringify(Array(4).fill("original")),
  "four Restore original submissions");
'''), "PASS")

    def test_a_recovery_block_refusal_and_check_again(self) -> None:
        """A refused submission offers Check again: deferred, still blocked, a second activation, then cleared."""
        self.assertEqual(flow(r'''
await start();
lane.refusal=fail("MUTATION_CONFLICT",T.recovery_unresolved,{reason:"RECOVERY_BLOCK",owner:"APPLY_MISSION_MAP"});
activate("apply");await opened();element("map-announcement").textContent="";action("Apply").click();await wait();await wait();
const refused=window.ServerManOperationMessages.error(lane.refusal.error,undefined,"APPLY_MISSION_MAP");
check(slot()===refused&&refused.includes(window.ServerManHostSentences.blockReason(T.recovery_unresolved,"APPLY_MISSION_MAP"))
  &&refused.includes(T.unresolved_text),`the block reason: ${slot()}`);
check(looks()==="map-operation-error"&&notice()===refused&&noticeLook()==="map-operation-recovery"&&text("map-announcement")===refused,
  "error slot, recovery notice, announced");
check(!element("map-check-again").hidden&&element("map-check-again").textContent===T.check
  &&element("map-check-again").getAttribute("aria-describedby")==="map-operation-notice","Check again offered");
check(model.status().writable&&text("map-tool-notice")==="","a refused submission ends the pause");
const button=element("map-check-again");button.focus();
lane.recovery={success:true,value:{blocked:true,deferred:true,diagnostics:[]}};button.click();await wait();
check(slot()===T.deferred&&notice()===T.deferred&&!button.hidden&&document.activeElement===button&&text("map-announcement")===T.deferred,
  "deferred: the text in the slot and the notice; the action and the focus stay");
lane.recovery={success:true,value:{blocked:true,reason:T.recovery_unresolved,diagnostics:[]}};button.click();await wait();
const reason=window.ServerManHostSentences.blockReason(T.recovery_unresolved,"APPLY_MISSION_MAP");
check(reason===T.unresolved_text&&slot()===reason&&notice()===reason&&!button.hidden,
  `still blocked: the block reason: ${slot()}`);
lane.hold=true;button.click();button.click();await wait();
check(lane.calls.filter(call=>call[0]==="check").length===3,"a second activation while a check runs does nothing");
lane.recovery={success:true,value:{blocked:false,diagnostics:[]}};lane.release();await wait();
check(slot()===T.cleared&&looks()==="map-operation-done"&&notice()===""&&button.hidden,"cleared: the text, no notice, no action");
check(document.activeElement===element("map-viewport")&&text("map-announcement")===T.cleared,"the focus goes to the map; announced");
lane.refusal=null;activate("apply");await opened();
check(slot()===""&&notice()==="","a new activation clears the ended text");action("Cancel").click();await wait();
'''), "PASS")

    def test_a_known_recovery_block_keeps_its_text_through_a_cancelled_preview(self) -> None:
        """R1 of UI-UX 2026-10-16 15:49:13: the slot and the notice keep the recovery text while Check again shows."""
        self.assertEqual(flow(r'''
await start();
lane.refusal=fail("MUTATION_CONFLICT",T.recovery_unresolved,{reason:"RECOVERY_BLOCK",owner:"APPLY_MISSION_MAP"});
activate("apply");await opened();action("Apply").click();await wait();await wait();
const refused=slot(),button=element("map-check-again");check(!button.hidden&&notice()===refused,"a refusal by the block");
lane.hold=true;activate("previous");await wait();
check(slot()===T.reading&&notice()===refused&&!button.hidden,"reading: the notice keeps the text that the action names");
lane.release();await opened();
check(slot()===refused&&notice()===refused&&looks()==="map-operation-error"&&noticeLook()==="map-operation-recovery"&&!button.hidden,
  `the open preview: the recovery text returns: ${slot()}`);
action("Cancel").click();await wait();
check(slot()===refused&&notice()===refused&&!button.hidden,"after Cancel the text and the action stay");
lane.recovery={success:true,value:{blocked:true,deferred:true,diagnostics:[]}};button.click();await wait();
activate("original");await opened();action("Cancel").click();await wait();
check(slot()===T.deferred&&notice()===T.deferred,"the last recovery text is the one that stays");
lane.recovery={success:true,value:{blocked:false,diagnostics:[]}};button.click();await wait();
activate("original");await opened();check(slot()===""&&notice()===""&&button.hidden,"without the action the slot is empty");
action("Cancel").click();await wait();check(slot()==="","and stays empty after Cancel");
lane.refusal=null;window.pywebview.api.apply_plan=async()=>({success:true,value:{}});
activate("apply");await opened();action("Apply").click();await wait();await wait();
check(slot()===window.ServerManMapWording.invalidAnswer&&slot()==="The map editor received an invalid answer."
  &&model.status().writable,"an answer without a handle uses the one invalid-answer text and ends the pause");
'''), "PASS")

    def test_synchronous_refusals_are_worded_by_the_reason_first(self) -> None:
        """invalid_saved_data, stale_revision and a context reason by details.reason; the rest by the lane kind."""
        self.assertEqual(flow(r'''
await start();
const read=async(mode,answer)=>{lane.previews[mode]=answer;activate(mode);await wait();await wait();return slot()};
let shown_=await read("apply",fail("REVISION_CONFLICT","The saved map plan is not valid.",{reason:"invalid_saved_data"}));
check(shown_===window.ServerManMapWording.readOnly("invalid_saved_data")&&shown_.startsWith("Read only: the saved map plan is not valid."),
  `invalid_saved_data: ${shown_}`);
check(!shown()&&looks()==="map-operation-error"&&notice()===shown_&&element("map-check-again").hidden,"no dialog; error look");
shown_=await read("apply",fail("REVISION_CONFLICT","The plan revision changed.",{reason:"stale_revision"}));
check(shown_==="The map plan changed. Reload the plan before saving again.",`stale_revision: ${shown_}`);
shown_=await read("previous",fail("PROFILE_CONTEXT_MISMATCH","context",{reason:"mission_path_changed"}));
check(shown_==="The mission path of this profile changed.",`context reason: ${shown_}`);
shown_=await read("apply",fail("REVISION_CONFLICT","Stale.",{reason:"preview_stale"}));
check(shown_===T.late_apply,`a stale Apply by its kind: ${shown_}`);
shown_=await read("previous",fail("REVISION_CONFLICT","Stale."));
check(shown_===T.stale_restore,`a stale restore without a reason by its kind: ${shown_}`);
shown_=await read("original",fail("REVISION_CONFLICT","Stale."));
check(shown_===T.stale_restore,"the same under Restore original");
check(!lane.calls.some(call=>["apply","restore"].includes(call[0])),"nothing was submitted");
'''), "PASS")

    def test_a_failed_save_keeps_the_apply_preview_closed(self) -> None:
        """The pending save settles first; a refused save shows the 8.10 text, then the save reason."""
        self.assertEqual(flow(r'''
await start();tool("circle");click(500,400);
const id=model.snapshot().objects[0].id,message="the circle radius must be at most the larger terrain side";
window.pywebview.api.save_plan=async(document,revision)=>{saved.push({plan:structuredClone(document),revision});return refuse(id,message)};
activate("apply");await wait();await wait();
const expected=`${T.not_opened} Loot circle 1: ${message}`;
check(slot()===expected&&notice()===expected&&looks()==="map-operation-error",`the text, then the save reason: ${slot()}`);
check(saved.length===1&&!lane.calls.some(call=>call[0]==="preview")&&!shown(),"one save at once; the preview stays closed");
window.pywebview.api.save_plan=async(document,revision)=>{saved.push({plan:structuredClone(document),revision});return success(5,true)};
activate("apply");await opened();
check(saved.length===2&&saved[1].plan.objects.length===1&&lane.calls.some(call=>call[0]==="preview"),"a saved draft opens the preview");
check(model.status().revision===5&&!model.status().dirty,"the preview reviews the saved plan");
action("Cancel").click();await wait();check(slot()===""&&!lane.calls.some(call=>call[0]==="apply"),"Cancel submits nothing");
click(800,800);
window.pywebview.api.save_plan=async()=>fail("REVISION_CONFLICT","The plan revision changed.",{reason:"stale_revision"});
await sleep(1000);await wait();check(!element("map-save-reload").hidden,"a refusal that needs a reload blocks autosave");
const calls=saved.length,previews=lane.calls.filter(call=>call[0]==="preview").length;activate("apply");await wait();await wait();
check(slot()===`${T.not_opened} The saved map plan changed.`&&lane.calls.filter(call=>call[0]==="preview").length===previews,
  `a blocked save keeps the preview closed: ${slot()}`);
'''), "PASS")


if __name__ == "__main__":
    unittest.main()
