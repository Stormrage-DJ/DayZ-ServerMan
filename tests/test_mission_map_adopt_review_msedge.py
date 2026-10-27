"""Verify the UI-UX review changes of leaf 10.16 in composed Edge: R1 to R5 of the entry 2026-10-17 13:06:47.

Texts: Product Owner 2026-10-17 13:07:29 in evidence 10.16 (ADOPT-30 to ADOPT-33). The inner window is 936 x 508.
"""

from __future__ import annotations

import unittest

from tests.mission_map_adopt_support import page
from tests.test_mission_map_preview_dialog_msedge import NARROW
from tests.ui_harness_support import EDGE

# Confirm the open adoption preview and let the submission pass
CONFIRM = r'''
const confirm=async()=>{control().click();await opened();document.querySelector(".action-row .button-primary").click();await wait();await wait()};
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class AdoptReviewEdgeTests(unittest.TestCase):
    """Fake answers, the real editor modules and isolated headless profiles."""

    def test_a_failed_save_replaces_the_reading_line(self) -> None:
        """R1: ADOPT-30 alone, or with the save reason; the slot never keeps the reading line."""
        self.assertEqual(page(r'''
await start(offered("restore"));tool("circle");click(500,400);
const id=model.snapshot().objects[0].id,message="the circle radius must be at most the larger terrain side";
window.pywebview.api.save_plan=async()=>refuse(id,message);
control().click();await wait();await wait();
const shownText=text("map-operation-status");
check(shownText===`${X.not_opened} Loot circle 1: ${message}`&&text("map-operation-notice")===shownText&&text("map-announcement")===shownText,`with a reason: ${shownText}`);
check(element("map-operation-status").classList.contains("map-operation-error")&&!shown(),"an error look; no dialog");
// A blocked autosave without a reason keeps the draft unsaved and gives no reason
window.ServerManMapAutosave.failed("Blocked.",true);
window.ServerManMapAdopt.load(offered("restore"));
check(window.ServerManMapAutosave.reason()==="","no save reason");
control().click();await wait();await wait();
check(text("map-operation-status")===X.not_opened&&text("map-operation-notice")===X.not_opened&&text("map-announcement")===X.not_opened,
  `without a reason: ${text("map-operation-status")}`);
''', window_size=NARROW, budget=40000), "PASS")

    def test_an_editable_plan_pauses_while_the_adoption_runs(self) -> None:
        """R2: ADOPT-32 and no edits from the submission to the end; a read-only plan shows no pause; a refusal ends it."""
        self.assertEqual(page(CONFIRM + r'''
await start(offered("restore"));check(model.status().writable,"editable before");
await confirm();
check(text("map-tool-notice")===X.paused&&!model.status().writable,"paused with ADOPT-32");
editorAnswer={success:true,value:{...offered("restore"),adoption:null,read_only_reason:null}};
await finish("SUCCEEDED",{result:{result:"ADOPTED",case:"restore"}});await poll();await wait();await wait();
check(text("map-tool-notice")===""&&model.status().writable&&control().hidden,"the pause ended with the operation");
// A read-only plan has nothing to pause
await load(offered("move"));await confirm();
check(text("map-tool-notice")===""&&!model.status().writable,"read only: no pause text");
await finish("SUCCEEDED",{result:{result:"ADOPTED",case:"move"}});await poll();
// A refused submission ends the pause at once
await load(offered("restore"));lane.refusal={success:false,error:{code:"MUTATION_CONFLICT",message:"busy",retryable:true}};
await confirm();
check(text("map-tool-notice")===""&&model.status().writable,"a refused submission ends the pause");
''', window_size=NARROW, budget=80000), "PASS")

    def test_refusals_that_need_a_reload_offer_reload_plan(self) -> None:
        """R3: ADOPT-33 with the editor reason, Reload plan with the focus; a recovery block keeps Check again."""
        self.assertEqual(page(CONFIRM + r'''
await start(offered("restore"));
const wanted={association_mismatch:X.mismatch,
  mission_path_changed:"Not adopted: The mission path of this profile changed. Reload plan shows its current state."};
for(const [reason,text_] of Object.entries(wanted)){
  await load(offered("restore"));
  lane.refusal={success:false,error:{code:"PROFILE_CONTEXT_MISMATCH",message:"x",retryable:false,details:{reason}}};
  await confirm();
  check(status()===text_&&!element("map-save-reload").hidden&&document.activeElement===element("map-save-reload"),`${reason}: ${status()}`);
  check(control().hidden&&model.status().writable,`${reason}: the control hides; the pause ended`);
}
await load(offered("restore"));
lane.refusal={success:false,error:{code:"REVISION_CONFLICT",message:"x",retryable:true,details:{reason:"stale_revision"}}};
await confirm();
check(status()==="Not adopted: The map plan changed. Reload the plan before saving again. Reload plan shows its current state.",`a stale revision: ${status()}`);
await load(offered("restore"));
lane.refusal={success:false,error:{code:"RECOVERY_REQUIRED",message:"r",retryable:false}};
await confirm();
check(!element("map-check-again").hidden&&element("map-save-reload").hidden&&!control().hidden,"a recovery block shows Check again, not Reload plan");
''', window_size=NARROW, budget=80000), "PASS")

    def test_the_visible_control_always_has_a_notice_and_the_term_is_one_for_all(self) -> None:
        """R4: #map-save-notice has text whenever #map-adopt shows. ADOPT-31: one slot term for all three operations."""
        self.assertEqual(page(CONFIRM + r'''
await start(value(plan()));
check(text("map-operation-term")===X.term,"the slot term is ADOPT-31");
for(const kind of ["move","copy","retired","restore"]){
  await load(offered(kind));
  check(!control().hidden&&text("map-save-notice").length>0,`${kind}: the notice has text`);
}
await load(value(plan()));
activate("apply");await opened();action("Apply").click();await wait();await wait();
const apply=own();emit(record(apply.operation_id,apply.kind,"RUNNING"));await poll();
check(text("map-operation-term")===X.term,"Apply: the same term");
emit(record(apply.operation_id,apply.kind,"SUCCEEDED"));await poll();await wait();
await load(offered("move"));await confirm();check(text("map-operation-term")===X.term,"adoption: the same term");
''', window_size=NARROW, budget=80000), "PASS")

    def test_the_apply_refusal_after_a_restore_names_adoption(self) -> None:
        """QA-10.31-F2: the Apply preview refusal `adoption_required` shows the ADOPT-6 Apply refusal text."""
        self.assertEqual(page(r'''
await start(offered("restore"));
lane.previews.apply={success:false,error:{code:"PROFILE_CONTEXT_MISMATCH",message:"x",retryable:false,details:{reason:"adoption_required"}}};
activate("apply");await wait();await wait();
check(text("map-operation-status")===X.apply_refusal&&text("map-operation-notice")===X.apply_refusal,`the refusal: ${text("map-operation-status")}`);
check(status()===X.adopt6,"the status still shows ADOPT-6");
''', window_size=NARROW, budget=40000), "PASS")

    def test_a_copy_with_an_editable_plan_has_a_status_and_a_notice(self) -> None:
        """Product Owner 2026-10-17 13:41:00: the status of the case copy on an editable plan, and the map notice."""
        self.assertEqual(page(r'''
await start(value(plan()));
await load({...value(plan()),adoption:{case:"copy"}});
check(!control().hidden&&status()===X.copy_editable&&text("map-save-notice")===X.copy_editable&&model.status().writable,
  `copy, editable: ${status()}`);
''', window_size=NARROW, budget=40000), "PASS")


if __name__ == "__main__":
    unittest.main()
