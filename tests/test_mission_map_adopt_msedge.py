"""Verify the adoption action, its preview flow and its operation from the editor window in composed Edge (10.16).

Rules:
- UI-UX Designer 2026-10-16 14:21:59 in evidence 10.1: the control, its states, the focus and 936 x 508.
- Architect 14:33:32: the load field `adoption`.
- Product Owner: the texts ADOPT-1 to ADOPT-29.

The inner window of every test is 936 x 508.
"""

from __future__ import annotations

import unittest

from tests.mission_map_adopt_support import page
from tests.test_mission_map_preview_dialog_msedge import NARROW
from tests.ui_harness_support import EDGE


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class AdoptControlEdgeTests(unittest.TestCase):
    """Fake answers, the real editor modules and isolated headless profiles."""

    def test_the_control_shows_only_for_a_load_answer_with_adoption(self) -> None:
        """Hidden without the field, shown for each case with its status text, hidden while loading or closing."""
        self.assertEqual(page(r'''
check(innerWidth===936&&innerHeight===508,`inner window ${innerWidth} x ${innerHeight}`);
await start(value(plan()));
check(control().hidden,"no adoption field: hidden");
const area=element("map-status-area").getBoundingClientRect().height;
check(control().id==="map-adopt"&&control().type==="button"&&control().className==="link-button"&&control().dataset.busyLock==="control",
  "a link button marked for the busy lock");
check(control().closest("#map-plan-status")===element("map-set-aside").closest("#map-plan-status")&&control().previousElementSibling===element("map-set-aside"),
  "in the plan state cell, after Set aside invalid plan");
check(control().getAttribute("aria-describedby")==="map-save-notice"&&!control().hasAttribute("aria-keyshortcuts"),"described by the map notice; no shortcut");
for(const [kind,wanted,writable] of [["move",X.adopt4,false],["copy",X.adopt4,false],["retired",X.adopt5,false],["restore",X.adopt6,true]]){
  await load(offered(kind));
  check(!control().hidden&&text("map-adopt")===X.label&&control().getAttribute("aria-disabled")!=="true",`${kind}: shown and available`);
  check(status()===wanted&&text("map-save-notice")===wanted&&element("map-save-notice").textContent===wanted,`${kind}: the status text and the map notice: ${status()}`);
  check(model.status().writable===writable,`${kind}: the plan is ${writable?"editable":"read only"}`);
  check(element("map-status-area").getBoundingClientRect().height===area&&getComputedStyle(control()).whiteSpace==="nowrap",`${kind}: one line`);
  check(control().getBoundingClientRect().right<=innerWidth&&element("map-set-aside").hidden,`${kind}: inside the window; Set aside is hidden`);
}
// An invalid plan offers Set aside and never adoption
await load({...value(plan()),read_only:true,read_only_reason:"invalid_saved_data",state:"CORRUPT"});
check(control().hidden&&!element("map-set-aside").hidden,"invalid saved data: Set aside, no adoption");
editorAnswer={success:true,value:{...offered("move"),adoption:null}};await window.ServerManMapEditor.load();await wait();
check(control().hidden,"a read-only reason alone does not offer adoption (UX-2)");
await load(offered("move"));check(!control().hidden,"offered again");
editorAnswer={success:false,error:{code:"STORAGE_FAILURE",message:"Load failed.",retryable:true}};await window.ServerManMapEditor.load();await wait();
check(control().hidden,"a failed load hides it");
await load(offered("move"));await window.ServerManMapEditor.settleForClose();await wait();
check(control().hidden,"closing hides it");
control().click();await wait();check(!shown(),"no dialog while closing");
window.ServerManMapEditor.releaseClose();await wait();check(!control().hidden,"a cancelled close offers it again");
// Reload plan and the control share the one line cell without leaving the window
element("map-save-reload").hidden=false;const both=[element("map-save-reload"),control()].map(item=>item.getBoundingClientRect());
check(both.every(box=>box.right<=innerWidth&&box.bottom<=innerHeight)&&element("map-status-area").getBoundingClientRect().height<=area+24,
  `both link buttons fit: ${JSON.stringify(both)}`);
''', window_size=NARROW), "PASS")

    def test_the_refresh_adopts_the_adoption_field(self) -> None:
        """An operation that ends from another origin refreshes the field: the control hides, or it appears."""
        self.assertEqual(page(r'''
await start(offered("move"));check(!control().hidden,"offered");
editorAnswer={success:true,value:{...offered("move"),adoption:null,read_only:false,read_only_reason:null,path_change:null}};
emit(record("other1","APPLY_MEDICAL_FEATURE","RUNNING"));await poll();
emit(record("other1","APPLY_MEDICAL_FEATURE","SUCCEEDED"));await poll();await wait();
check(control().hidden&&element("map-editor").dataset.readOnly==="false","the refresh took the new field and the new state");
editorAnswer={success:true,value:offered("restore")};
emit(record("other2","RESTORE_BACKUP","RUNNING"));await poll();emit(record("other2","RESTORE_BACKUP","SUCCEEDED"));await poll();await wait();
check(!control().hidden&&status()===X.adopt6,"a restore from another origin offers adoption, with ADOPT-6");
''', window_size=NARROW), "PASS")

    def test_the_preview_read_the_dialog_and_the_focus(self) -> None:
        """Busy while the preview reads, the dialog sections, no stop line, Tab wrap, Escape, Cancel and the focus return."""
        self.assertEqual(page(r'''
await start(offered("move"));
lane.hold=true;control().focus();control().click();await wait();
check(text("map-operation-status")===X.reading&&text("map-announcement")===X.reading,"ADOPT-3 in the status area, announced once");
check(control().getAttribute("aria-disabled")==="true"&&control().getAttribute("aria-describedby").split(" ").includes("map-adopt-reason")
  &&text("map-adopt-reason")===X.busy,"busy: aria-disabled with ADOPT-2 as its description");
control().click();await wait();check(lane.calls.filter(call=>call[0]==="preview").length===1,"a second activation does nothing");
lane.release();await opened();
check(control().getAttribute("aria-disabled")!=="true"&&!control().getAttribute("aria-describedby").includes("map-adopt-reason"),"available again");
check(text("map-operation-status")==="","the reading text ends when the preview opens");
const title=shown().querySelector("h2");
check(title.textContent===X.title&&document.activeElement===title,"ADOPT-9 and the first focus on the heading");
check(JSON.stringify(headings())===JSON.stringify(["What happened","What adoption writes","What stays unchanged"]),`sections: ${headings()}`);
check(lines(section("What happened")).join("|")===A.move.happened,"the ADOPT-12 text of the case");
check(lines(section("What adoption writes"))[0]===A.move.intro&&section("What adoption writes").querySelector("[id^=map-preview-label]").textContent==="Records that adoption writes","the intro and the list label");
check(lines(section("What adoption writes")).slice(2).join("|")===A.move.records.join("|"),"the record rows, 4 of them, open");
check(lines(section("What stays unchanged")).join("|")===A.move.unchanged.join("|")&&!section("What stays unchanged").querySelector(".link-button"),"the unchanged lines, never collapsed");
check(footer().querySelectorAll("p").length===1&&footer().querySelector("p").getAttribute("role")==="status"&&!footer().querySelector("[id^=map-preview-remaining]"),
  "no stop paragraph, no remaining line: only the status line");
check(buttons().join()==="Cancel,Adopt"&&!shown().querySelector(".action-row .button-primary").hasAttribute("aria-disabled")
  &&shown().querySelector(".action-row .button-primary").textContent===X.confirm&&!shown().querySelector(".button-danger"),"Cancel and a primary confirm, enabled at once");
check(shown().querySelectorAll("input, fieldset").length===0,"no choice");
const body=shown().querySelector(".map-preview-body"),adopt=[...shown().querySelectorAll(".action-row button")].at(-1);
const r=shown().querySelector(".map-preview-footer").getBoundingClientRect();
check(r.bottom<=innerHeight&&r.top>=0&&adopt.getBoundingClientRect().bottom<=innerHeight,"the footer is visible at 936 x 508");
check(key(adopt,"Tab")&&document.activeElement===body,"Tab on the last button wraps to the body");
check(key(title,"Tab",true)&&document.activeElement===adopt,"Shift+Tab on the heading wraps to the last button");
shown().dispatchEvent(new Event("cancel",{cancelable:true}));await wait();
check(!shown()&&document.activeElement===control()&&!lane.calls.some(call=>call[0]==="adopt"),"Escape is Cancel: nothing is sent; the focus is on the control");
control().click();await opened();document.querySelector(".action-row button").click();await wait();
check(!shown()&&document.activeElement===control(),"Cancel returns the focus to the control");
lane.hold=true;control().click();await wait();editorAnswer={success:true,value:{...offered("move"),adoption:null}};
lane.release();await opened();await window.ServerManMapEditor.load();await wait();check(control().hidden,"the control hid while the dialog was open");
action("Cancel").click();await wait();check(document.activeElement===element("map-viewport"),"a hidden control gives the focus to the map");
''', window_size=NARROW), "PASS")

    def test_confirm_submits_the_reviewed_fingerprint_and_follows_the_operation(self) -> None:
        """The payload, the slot texts, the success line with ADOPT-8, the refresh and the focus on the map."""
        self.assertEqual(page(r'''
await start(offered("move"));control().click();await opened();
document.querySelector(".action-row .button-primary").click();await wait();await wait();
check(JSON.stringify(lane.calls.find(call=>call[0]==="adopt"))===JSON.stringify(["adopt","d".repeat(64)]),"the reviewed fingerprint only");
const queued=own(),name=window.ServerManOperationLabels.kind("ADOPT_MISSION_MAP").name;
check(queued.kind==="ADOPT_MISSION_MAP"&&text("map-operation-status")===`${name} · Waiting`&&text("map-announcement")===text("map-operation-status"),
  `queued: ${text("map-operation-status")}`);
check(control().getAttribute("aria-disabled")==="true","the lane lock holds the control");
element("map-announcement").textContent="";
emit(record(queued.operation_id,queued.kind,"RUNNING"));await poll();
check(text("map-operation-status")===name&&text("map-announcement")==="","running: the kind label alone, not announced");
editorAnswer={success:true,value:{...offered("move"),adoption:null,read_only:false,read_only_reason:null,path_change:null}};
emit(record(queued.operation_id,queued.kind,"SUCCEEDED",{result:{result:"ADOPTED",case:"move"}}));await poll();await wait();await wait();
const wanted=`Mission map records adopted. ${A.move.success}`;
check(text("map-operation-status")===wanted&&text("map-announcement")===wanted,`the end line with ADOPT-8: ${text("map-operation-status")}`);
check(control().hidden&&control().getAttribute("aria-disabled")!=="true","the new load hid the control");
check(document.activeElement===element("map-viewport"),"the focus is on the map");
check(status()==="Not saved yet","the status shows the new state, not the adoption text");
''', window_size=NARROW), "PASS")

    def test_failures_of_the_read_the_call_and_the_operation(self) -> None:
        """A failed read opens no dialog; a stale adoption, a late failure and a recovery block show their texts."""
        self.assertEqual(page(r'''
await start(offered("move"));
adoption.answer={success:false,error:{code:"PROFILE_CONTEXT_MISMATCH",message:"x",retryable:false,details:{reason:"association_mismatch"}}};
control().click();await wait();await wait();
check(!shown()&&status()===X.mismatch&&!element("map-save-reload").hidden&&document.activeElement===element("map-save-reload"),
  `a failed read that needs a reload: ${status()}`);
await load(offered("move"));reply("move");
const run=async(state,extra)=>{control().click();await opened();document.querySelector(".action-row .button-primary").click();await wait();await wait();
  await finish(state,extra);await poll();await wait()};
await run("FAILED",{terminal_error:{code:"REVISION_CONFLICT",message:"stale",retryable:true},progress_phase:"failed"});
check(text("map-operation-status")===`${X.kind_failure} ${X.stale_commit}`||text("map-operation-status").includes(X.stale_commit),`ADOPT-28: ${text("map-operation-status")}`);
check(element("map-operation-status").classList.contains("map-operation-error")&&!control().hidden,"a failure shows as an error; the control stays");
await run("FAILED",{terminal_error:{code:"PUBLICATION_FAILED",message:"late",retryable:false},progress_phase:"failed"});
check(text("map-operation-status").includes(X.committed),`the commit-point text: ${text("map-operation-status")}`);
await run("RECOVERY_REQUIRED",{terminal_error:{code:"RECOVERY_REQUIRED",message:"recovery",retryable:false},progress_phase:"failed"});
check(!element("map-check-again").hidden&&element("map-operation-status").classList.contains("map-operation-recovery"),"a recovery block offers Check again");
lane.refusal={success:false,error:{code:"MUTATION_CONFLICT",message:"busy",retryable:true,details:{reason:"RECOVERY_BLOCK"}}};
control().click();await opened();document.querySelector(".action-row .button-primary").click();await wait();await wait();
check(text("map-operation-status").length>0&&lane.calls.filter(call=>call[0]==="adopt").length===4,"a refused call shows a text");
''', window_size=NARROW, budget=80000), "PASS")


if __name__ == "__main__":
    unittest.main()
