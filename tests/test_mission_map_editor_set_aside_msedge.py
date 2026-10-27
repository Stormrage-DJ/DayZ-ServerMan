"""Verify the window action that sets aside invalid saved plans, with its confirmation, in composed Edge (5.3)."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_autosave_msedge import AUTOSAVE
from tests.test_mission_map_editor_bounds_msedge import COMMON
from tests.ui_harness_support import EDGE, run_editor_harness


# A fake set-aside answer per call, or a held one (`hold`, then `release`); dialog choices are found by their text.
SET_ASIDE = COMMON + r'''
const asides=[];let asideAnswer={success:true,value:{association:structuredClone(association),set_aside:"plan.invalid-20261013T120000Z.json"}};
let release=null;window.pywebview.api.set_aside_plan=async(key)=>{asides.push(key);return asideAnswer};
const hold=()=>{window.pywebview.api.set_aside_plan=async(key)=>{asides.push(key);await new Promise(done=>{release=done});return asideAnswer}};
const button=()=>document.getElementById("map-set-aside"),status=()=>document.getElementById("map-status").textContent;
const reload=()=>document.getElementById("map-save-reload"),notice=()=>document.getElementById("map-save-notice");
const live=()=>document.getElementById("map-announcement").textContent,focused=()=>document.activeElement?.id;
const open=async()=>{button().focus();button().click();await wait()};
const choose=async(label)=>{const dialog=document.querySelector("dialog");check(dialog&&dialog.open,"a confirmation dialog is open");
  [...dialog.querySelectorAll("button")].find(item=>item.textContent===label).click();await wait();await wait()};
const invalid=()=>({...value(null),state:"CORRUPT",read_only:true,read_only_reason:"invalid_saved_data",
  detail:"the saved plan is not valid JSON"});
const fresh=(document=plan(bounds(),false))=>({...value(document),state:"MISSING",revision:null});
const refuse=(code,reason,message="Reason text.")=>({success:false,error:{code,message,retryable:code==="STORAGE_FAILURE",details:{reason}}});
const KEPT="The invalid plan was kept as plan.invalid-20261013T120000Z.json in its folder.";
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorSetAsideEdgeTests(unittest.TestCase):
    """Use synthetic load answers, fake set-aside answers and isolated headless profiles."""

    def test_action_is_offered_only_for_invalid_saved_data(self) -> None:
        """Valid, missing, path-change and other states and a failed load hide the action; invalid data shows it."""
        self.assertEqual(run_editor_harness(SET_ASIDE + r'''
await load(value(plan()));check(button().hidden,"a valid plan hides the action");
const footer=()=>document.getElementById("map-status-area").getBoundingClientRect().height,plain=footer();
await load(value(null));check(button().hidden,"a missing plan hides the action");
for(const reason of ["mission_path_changed","returned_to_earlier_path"]){
  await load({...value(plan()),read_only:true,read_only_reason:reason,path_change:{shown_mission_key:"b".repeat(32),folders:[]}});
  check(button().hidden,`${reason}: only adoption ends this state`);
}
await load({...value(plan()),state:"VALID",read_only:true,read_only_reason:"association_mismatch",detail:"the plan names another mission root"});
check(button().hidden&&!mapEditorState.model.status().writable&&document.getElementById("map-tool-circle").disabled,"settings mismatch: no set aside, no save (QA-5.3-F6)");
check(status()==="Read only: the saved map plan does not match the mission folder or runtime profile folder of this profile. Correct the profile settings, then load the plan again. the plan names another mission root",`settings mismatch: ${status()}`);
await load({...value(plan()),read_only:true,read_only_reason:"other_reason",path_change:null});
check(button().hidden,"another read-only reason without a path change hides the action");
await load(invalid());
check(!button().hidden&&!button().disabled&&button().textContent==="Set aside invalid plan","invalid saved data offers the action");
check(footer()===plain&&getComputedStyle(button()).whiteSpace==="nowrap","the label stays on the one-line plan state line");
check(status()==="Read only: the saved map plan is not valid. Set aside invalid plan keeps it under a dated name and starts a new, empty plan. the saved plan is not valid JSON","the reason names the action");
editorAnswer=refuse("STORAGE_FAILURE","storage_failure","Load failed.");await window.ServerManMapEditor.load();await wait();
check(button().hidden,"a failed load hides the action (QA-5.3-F4)");
await load({...invalid(),state:"OTHER_ASSOCIATION",detail:"backend text"});
check(!button().hidden&&status().endsWith(" The saved plan belongs to another profile or mission."),`a plan of another association: ${status()}`);
''', budget=8000, window_size="936,508"), "PASS")

    def test_confirmation_running_state_and_load_of_the_new_empty_plan(self) -> None:
        """Cancel and Escape send nothing; the call disables the action; success loads and names the dated file."""
        self.assertEqual(run_editor_harness(SET_ASIDE + r'''
await load(invalid());await open();
const dialog=document.querySelector("dialog"),confirm=[...dialog.querySelectorAll("button")].find(item=>item.textContent==="Set aside");
check(dialog.querySelector("h2").textContent==="Set aside the invalid plan?","the dialog names the action");
check(dialog.querySelector("p").textContent==="The plan file stays in its folder under a dated name. Nothing is deleted, and mission files do not change. The editor then starts a new, empty plan. The new plan does not contain the objects of the invalid plan.","the dialog text");
check(document.activeElement.textContent==="Cancel","Cancel has the first focus");
check(confirm.className==="button-danger","Set aside uses .button-danger");
await choose("Cancel");check(asides.length===0&&!document.querySelector("dialog")&&focused()==="map-set-aside","Cancel sends nothing and returns the focus");
await open();document.querySelector("dialog").dispatchEvent(new Event("cancel",{cancelable:true}));await wait();
check(asides.length===0&&focused()==="map-set-aside","Escape sends nothing and returns the focus");
const loads=editorCalls.length;hold();editorAnswer={success:true,value:fresh()};
await open();await choose("Set aside");
check(asides.length===1&&asides[0]==="a".repeat(32),"one call with the loaded mission key");
check(button().disabled&&status()==="Setting aside the invalid plan"&&live()===status(),"disabled with an announced running status");
button().click();await wait();check(!document.querySelector("dialog")&&asides.length===1,"a second click does nothing");
release();await wait();await wait();await wait();
check(editorCalls.length===loads+1,"the window loads again after the set aside");
check(document.getElementById("map-editor").dataset.readOnly==="false"&&button().hidden,"the new plan is writable; the action is gone");
check(status()===`${KEPT} A new, empty plan is open.`&&live()===status(),`the announced status names the dated file: ${status()}`);
check(notice().textContent===status()&&notice().classList.contains("map-save-success")&&!notice().classList.contains("map-save-error"),"a success notice");
check(focused()==="map-viewport","the focus moves to the map");
const current=mapEditorState.model.status();
check(current.revision===null&&current.plan.objects.length===0,"an empty plan; its first save is revision 0");
''', budget=8000), "PASS")

    def test_the_dated_name_stays_visible_after_a_bounds_request_or_a_failed_load(self) -> None:
        """E07: the dated file is named with the bounds request and with a load failure."""
        self.assertEqual(run_editor_harness(SET_ASIDE + r'''
await load(invalid());editorAnswer={success:true,value:fresh(null)};await open();await choose("Set aside");await wait();
check(status()===`${KEPT} No map plan yet. Enter and confirm the mission bounds before editing.`,`bounds request: ${status()}`);
await load(invalid());editorAnswer=refuse("STORAGE_FAILURE","storage_failure","Load failed.");await open();await choose("Set aside");await wait();
check(status().startsWith(`${KEPT} `)&&status().length>KEPT.length+1&&notice().classList.contains("map-save-error"),`failed load: ${status()}`);
check(button().hidden&&live()===status(),"the action is gone; the result is announced");
''', budget=8000), "PASS")

    def test_refusals_show_the_reason_and_offer_the_right_next_step(self) -> None:
        """A storage failure keeps the action; a changed state offers Reload plan with editor wording (QA-5.3-F2, F4)."""
        self.assertEqual(run_editor_harness(SET_ASIDE + r'''
await load(invalid());asideAnswer=refuse("STORAGE_FAILURE","storage_failure","The map editor data could not be stored or read.");
await open();await choose("Set aside");
check(status()==="Not set aside: The map editor data could not be stored or read."&&live()===status()&&reload().hidden,`storage failure: ${status()}`);
check(!button().hidden&&!button().disabled&&focused()==="map-set-aside","the action stays for a retry and has the focus");
const cases=[["REVISION_CONFLICT","plan_not_invalid","Not set aside: The saved map plan is valid now, or it is no longer there. Reload plan shows its current state."],
  ["PROFILE_CONTEXT_MISMATCH","association_mismatch","Not set aside: This profile now uses a different mission from the one in this map editor. Reload plan shows its current state."],
  ["PROFILE_CONTEXT_MISMATCH","mission_path_changed","Not set aside: The mission path of this profile changed. Reload plan shows its current state."],
  ["NOT_FOUND","profile_not_found",null]];
for(const [code,reason,expected] of cases){
  await load(invalid());asideAnswer=refuse(code,reason,"The map editor names another mission. Reload the plan.");
  const loads=editorCalls.length;await open();await choose("Set aside");
  check(expected===null?status().startsWith("Not set aside: ")&&status().endsWith(" Reload plan shows its current state."):status()===expected,`${reason}: ${status()}`);
  check(!status().includes("backup"),`${reason}: no backup wording`);
  check(!reload().hidden&&button().hidden&&focused()==="map-save-reload"&&editorCalls.length===loads,`${reason}: Reload plan replaces the action`);
}
editorAnswer={success:true,value:{...value(plan()),state:"VALID"}};reload().click();await wait();await wait();
check(reload().hidden&&button().hidden&&status()==="Map plan loaded","the reload shows the actual state");
''', budget=10000), "PASS")

    def test_the_action_follows_the_close_sequence(self) -> None:
        """E05: no action while Closing; E06: a set aside committed during a cancelled close loads again at release."""
        self.assertEqual(run_editor_harness(SET_ASIDE + r'''
await load(invalid());await window.ServerManMapEditor.settleForClose();await wait();
check(button().hidden&&reload().hidden,"no action while Closing");
button().click();await wait();check(!document.querySelector("dialog")&&asides.length===0,"no dialog while Closing");
window.ServerManMapEditor.releaseClose();await wait();check(!button().hidden,"a cancelled close offers it again");
hold();await open();await choose("Set aside");const loads=editorCalls.length;
await window.ServerManMapEditor.settleForClose();await wait();
editorAnswer={success:true,value:fresh()};release();await wait();await wait();
check(editorCalls.length===loads&&status()==="Closing"&&button().hidden,"the committed answer waits for the release");
window.ServerManMapEditor.releaseClose();await wait();await wait();await wait();
check(editorCalls.length===loads+1&&button().hidden&&document.getElementById("map-editor").dataset.readOnly==="false","the release loads the new plan once");
check(status()===`${KEPT} A new, empty plan is open.`,`the release names the dated file: ${status()}`);
await load(invalid());hold();await open();await choose("Set aside");await window.ServerManMapEditor.settleForClose();await wait();
asideAnswer=refuse("STORAGE_FAILURE","storage_failure");release();await wait();await wait();
check(status()==="Closing","a failure during a close is not shown");
window.ServerManMapEditor.releaseClose();await wait();await wait();
check(!button().hidden&&!button().disabled,"after the release the action is offered for a retry");
await load(invalid());hold();await open();await choose("Set aside");await window.ServerManMapEditor.settleForClose();await wait();
asideAnswer=refuse("REVISION_CONFLICT","plan_not_invalid");release();await wait();await wait();
check(status()==="Closing"&&reload().hidden,"a Reload refusal during a close is not shown (QA-5.3-F7)");
window.ServerManMapEditor.releaseClose();await wait();await wait();
check(!button().hidden&&reload().hidden,"after the release the action is offered and Reload plan stays hidden");
''', budget=10000), "PASS")

    def test_a_late_answer_after_a_newer_load_is_dropped(self) -> None:
        """QA-5.3-F7: a load during the call shows the actual state; the late answer neither loads nor writes a status."""
        self.assertEqual(run_editor_harness(SET_ASIDE + r'''
await load(invalid());hold();await open();await choose("Set aside");
await load({...value(plan()),state:"VALID"});const loads=editorCalls.length,before=status();
release();await wait();await wait();await wait();
check(editorCalls.length===loads&&status()===before&&before==="Map plan loaded"&&button().hidden,`late answer: ${status()}`);
await load(invalid());hold();await open();await choose("Set aside");
await load({...value(plan()),state:"VALID"});asideAnswer=refuse("REVISION_CONFLICT","plan_not_invalid");
release();await wait();await wait();
check(status()==="Map plan loaded"&&reload().hidden&&button().hidden,`late refusal: ${status()}`);
''', budget=8000), "PASS")

    def test_plan_save_context_refusals_use_editor_wording(self) -> None:
        """QA-5.3-F2: a 5.2 plan save refused for a changed profile or mission names the map, not a backup."""
        self.assertEqual(run_editor_harness(AUTOSAVE + r'''
const expected={association_mismatch:"This profile now uses a different mission from the one in this map editor.",
  mission_path_changed:"The mission path of this profile changed.",returned_to_earlier_path:"This mission returned to an earlier path."};
for(const [reason,text] of Object.entries(expected)){
  await load(value(plan()));if(document.getElementById("map-tool-circle").getAttribute("aria-pressed")!=="true")tool("circle");
  click(500,400);await sleep(900);check(pending.length===1,`${reason}: one save waits`);
  await answer(failure("PROFILE_CONTEXT_MISMATCH",reason,"The map editor names another mission. Reload the plan."));
  check(status()===`Not saved: ${text} Reload plan discards your unsaved changes. To keep your unsaved changes, choose Export plan first.`&&!reload().hidden,`${reason}: ${status()}`);
}
''', budget=20000), "PASS")


if __name__ == "__main__":
    unittest.main()
