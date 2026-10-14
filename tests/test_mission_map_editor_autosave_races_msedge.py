"""Verify autosave across close, load and bounds saves, and the stable save status layout, in composed Edge (5.2 rework)."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_autosave_msedge import AUTOSAVE
from tests.ui_harness_support import EDGE, run_editor_harness


# A fake backend that commits a save only at its current revision; the first save waits for its answer.
RACES = AUTOSAVE + r'''
let current=4;const backend=()=>{window.pywebview.api.save_plan=(document,revision,selection)=>{
  saved.push({plan:structuredClone(document),revision,selection});
  if(pending.length===0&&saved.length===1)return new Promise(resolve=>pending.push(resolve));
  return Promise.resolve(revision===current?success(++current):failure("REVISION_CONFLICT","stale_revision","changed"))}};
const fresh=async()=>{saved.length=0;current=4;backend();await load(value(plan()));if(text("map-tool-status")!=="Circle")tool("circle")};
const settle=()=>window.ServerManMapEditor.settleForClose(),release=()=>window.ServerManMapEditor.releaseClose();
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorAutosaveRaceEdgeTests(unittest.TestCase):
    """Use synthetic plans, fake save answers and isolated headless profiles."""

    def test_answer_after_a_cancelled_close_is_adopted(self) -> None:
        """QA-5.2-F1: R4, R4c and R4b; a committed answer is adopted, and a queued save runs, after a cancelled close."""
        self.assertEqual(run_editor_harness(RACES + r'''
await fresh();click(500,400);await sleep(900);settle();release();
check(status()==="Saving"&&text("map-announcement")==="Saving","R4: release shows the running save, not Closing");
current=5;await answer(success(5));check(status()==="Saved"&&model.status().revision===5,"R4: the committed answer is adopted");
click(600,400);await sleep(900);check(saved.length===2&&saved[1].revision===5&&status()==="Saved"&&reload().hidden,"R4: the next save uses the committed revision; no false conflict");
await fresh();click(500,400);await sleep(900);const closing=settle();current=5;await answer(success(5));
check(status()==="Closing"&&model.status().revision===5,"R4c: an answer during the close keeps Closing and is adopted");
check(await closing==="close","R4c: the committed save closes");
release();check(status()==="Saved"&&text("map-announcement")==="Saved"&&text("map-draft-changes")==="No","R4c: release shows the current save state");
click(600,400);await sleep(900);check(saved.length===2&&saved[1].revision===5&&status()==="Saved"&&reload().hidden,"R4c: no false conflict after the release");
await fresh();click(500,400);await sleep(900);settle();release();click(600,400);await sleep(900);
check(saved.length===1&&status()==="Saving","R4b: an edit after the release queues behind the running save");
current=5;await answer(success(5));await wait();
check(saved.length===2&&saved[1].revision===5&&saved[1].plan.objects.length===2&&status()==="Saved","R4b: the queued save runs with the committed revision");
''', budget=20000), "PASS")

    def test_close_keeps_the_delay_the_conflict_and_a_bounds_answer(self) -> None:
        """QA-5.2-F5 and F6: R11, the delayed text after a release (Q35), R12 and R13."""
        self.assertEqual(run_editor_harness(RACES + r'''
const delayed="Saving takes longer than usual. An apply or restore of this mission can delay it.";
await fresh();click(500,400);await sleep(900);await sleep(1000);settle();release();check(status()==="Saving","R11: the release shows Saving");
await sleep(1100);check(status()===delayed&&text("map-save-notice")===delayed,"R11: the delayed text 2 s after the save started");
current=5;await answer(success(5));check(status()==="Saved","R11: saved");
await fresh();click(500,400);await sleep(900);await sleep(2100);settle();release();
check(status()===delayed&&text("map-announcement")===delayed,"Q35: a release keeps the delayed text");current=5;await answer(success(5));
await fresh();click(500,400);await sleep(900);settle();await answer(failure("REVISION_CONFLICT","stale_revision","changed"));await sleep(100);
check(status()==="Closing"&&reload().hidden,"R12: no Reload plan while Closing");
check(document.activeElement.textContent==="Keep editing","R12: the close asks (5.4)");document.activeElement.click();await wait();
check(status().startsWith("Not saved: The saved map plan changed")&&!reload().hidden&&text("map-announcement")===status(),"R12: the release shows the conflict and Reload plan");
saved.length=0;current=4;backend();await load(value(plan()));fill(bounds(0,0,3000,1000));manual();await wait();
const bounded=settle();current=5;await answer(success(5));await sleep(100);check(status()==="Closing"&&model.status().revision===5&&model.snapshot().terrain.bounds.width===3000,"R13: a committed bounds answer during a close is adopted");
check(await bounded==="close","R13: the close waits for the typed bounds in the running save (5.4)");
release();check(!document.getElementById("map-bounds-fields").disabled,"R13: the release enables the bounds controls");
if(text("map-tool-status")!=="Circle")tool("circle");click(500,400);await sleep(900);
check(saved.length===2&&saved[1].revision===5&&saved[1].plan.terrain.bounds.width===3000&&status()==="Saved"&&reload().hidden,"R13: no false conflict after the release");
''', budget=20000), "PASS")

    def test_bounds_failure_around_a_cancelled_close_is_shown(self) -> None:
        """QA-5.2-F9 (R16): a bounds failure after a cancelled close shows; one during the close shows at the release."""
        self.assertEqual(run_editor_harness(RACES + r'''
const storage=()=>failure("STORAGE_FAILURE","storage_failure","The map editor data could not be stored or read.");
await load(value(plan()));fill(bounds(0,0,3000,1000));manual();await wait();settle();release();await answer(storage());
check(text("map-bounds-error").startsWith("Bounds not saved: ")&&status()===text("map-bounds-error")&&text("map-announcement")===status(),"R16: the failure after the release shows and is announced");
check(model.snapshot().terrain.bounds.width===2000&&!fields().disabled&&text("map-save-notice")===status(),"R16: the plan keeps its bounds; the controls work again");
await load(value(plan()));fill(bounds(0,0,3000,1000));manual();await wait();const asked=settle();await answer(storage());await sleep(100);
check(status()==="Closing"&&text("map-bounds-error")===""&&document.querySelector("dialog"),"a failure during the close keeps Closing and asks about the typed bounds (5.4)");
document.activeElement.click();await wait();check(await asked==="cancel","Keep editing");release();check(text("map-bounds-error").startsWith("Bounds not saved: ")&&status()===text("map-bounds-error")&&!fields().disabled,"the release shows the failure");
''', budget=20000), "PASS")

    def test_load_guidance_returns_with_its_notice(self) -> None:
        """QA-5.2-F8 (R15): after an edit and an undo, the load guidance shows in full on the map again."""
        self.assertEqual(run_editor_harness(RACES + r'''
await load({...value(plan()),state:"VALID",detail:"The records changed; reload the plan before saving."});
const loaded=status();check(loaded.endsWith("reload the plan before saving.")&&text("map-save-notice")===loaded,"the load guidance on the map");
if(text("map-tool-status")!=="Circle")tool("circle");click(500,400);check(text("map-save-notice")==="","no notice while an edit waits");
await model.undo();check(status()===loaded&&text("map-save-notice")===loaded&&document.getElementById("map-status").title===loaded,"the guidance returns with its notice");
''', budget=20000), "PASS")

    def test_load_during_a_save_drops_its_answer_but_not_a_newer_save(self) -> None:
        """QA-5.2-F2 (R5) and Q06: a late answer after a load is not adopted; a save queued after the load runs."""
        self.assertEqual(run_editor_harness(RACES + r'''
await fresh();click(500,400);await sleep(900);await load(value(plan()));
check(model.status().revision===4&&!model.status().dirty,"the load replaced the state");
click(600,400);await sleep(900);check(saved.length===1,"the newer save waits for the running one");
await answer(success(5));await wait();
check(saved.length===2&&saved[1].revision===4&&saved[1].plan.objects.length===1,"the late answer is dropped; the queued save runs on the loaded revision");
check(status()==="Saved"&&model.status().revision===5&&!model.status().dirty,"the queued save is adopted");
''', budget=20000), "PASS")

    def test_unconfirmed_result_and_not_found_offer_reload(self) -> None:
        """QA-5.2-F4 (R8) and Q08: an unconfirmed answer and NOT_FOUND stop autosave and offer Reload plan."""
        self.assertEqual(run_editor_harness(RACES + r'''
await fresh();click(500,400);await sleep(900);await answer({success:true,value:{revision:"x"}});
check(status()==="Not saved: The save result could not be confirmed. Reload plan discards your unsaved changes."&&!reload().hidden,"unconfirmed result offers the reload");
click(600,400);await sleep(1500);check(saved.length===1&&text("map-draft-changes")==="Not available","no autosave with an unconfirmed revision");
await fresh();click(500,400);await sleep(900);await answer(failure("NOT_FOUND","profile_not_found","The selected profile no longer exists."));
check(status().startsWith("Not saved: ")&&status().endsWith(" Reload plan discards your unsaved changes.")&&!reload().hidden,"NOT_FOUND offers the reload");
click(600,400);await sleep(1500);check(saved.length===1,"no autosave after NOT_FOUND");
check(reload().getAttribute("aria-describedby")==="map-save-notice"&&text("map-save-notice")===status(),"the reload names the notice");
''', budget=20000), "PASS")

    def test_bounds_and_plan_saves_wait_for_each_other(self) -> None:
        """Q04 and Q05 (R7, R6): no plan save while a bounds save runs, and no bounds save while a plan save runs."""
        self.assertEqual(run_editor_harness(RACES + r'''
await load(value(plan()));fill(bounds(0,0,3000,1000));manual();await wait();check(saved.length===1,"bounds save running");
tool("circle");click(500,400);await sleep(1800);check(saved.length===1,"R7: no plan save while the bounds save runs");
await answer(success(5));await sleep(900);
check(saved.length===2&&saved[1].revision===5&&saved[1].plan.terrain.bounds.width===3000&&saved[1].plan.objects.length===1,"R7: the plan save follows with the new revision");
await answer(success(6));check(status()==="Saved","R7: saved");
click(600,400);await sleep(900);check(saved.length===3,"plan save running");
fill(bounds(0,0,2500,1000));manual();await wait();document.querySelector("dialog")?.querySelectorAll("button")[1].click();await sleep(300);
check(saved.length===3,"R6: the bounds save waits for the plan save");
await answer(success(7));await sleep(200);
check(saved.length===4&&saved[3].revision===7&&saved[3].plan.terrain.bounds.width===2500&&saved[3].plan.objects.length===2,"R6: the bounds save follows with the new revision");
''', budget=20000), "PASS")

    def test_blur_ends_the_hold(self) -> None:
        """Q13: a window blur ends a pointer hold, and the due save runs 800 ms later."""
        self.assertEqual(run_editor_harness(RACES + r'''
await fresh();click(500,400);pointer("pointerdown",at(900,500),0,6);await sleep(1500);check(saved.length===0,"held");
window.dispatchEvent(new Event("blur"));await sleep(900);check(saved.length===1,"the blur ends the hold");
''', budget=20000), "PASS")


# Sizes of the map and the footer through the save states; the panel open, then collapsed.
SIZES = RACES + r'''
const footer=document.getElementById("map-status-area"),panel=document.getElementById("map-panel");
const size=()=>[region.clientWidth,region.clientHeight,footer.offsetWidth,footer.offsetHeight].join();
const long="the circle radius must be at most the larger of the terrain width and height, and the centre must lie inside the closed bounds of the terrain";
for(const open of [true,false]){
  panel.open=open;saved.length=0;await load(value(plan()));if(text("map-tool-status")!=="Circle")tool("circle");await wait();const start=size(),seen=[];const see=(name)=>seen.push(`${name}:${size()}`);
  click(500,400);see("updating");await sleep(900);await sleep(2100);see("delayed");
  await answer(refuse(model.snapshot().objects[0].id,long));see("failure");
  // Q33: the notice sits at the bottom right of the map, clear of the tool notice and the view label
  click(-50,500);const box=(id)=>document.getElementById(id).getBoundingClientRect(),map=box("map-viewport"),note=box("map-save-notice");
  const apart=(a,b)=>a.right<=b.left||b.right<=a.left||a.bottom<=b.top||b.bottom<=a.top;
  check(note.width>0&&note.left>=map.left&&note.top>=map.top&&map.right-note.right<=16&&map.bottom-note.bottom<=16&&
    apart(note,box("map-tool-notice"))&&apart(note,box("map-view-label")),`notice at the bottom right, clear of the other map texts: ${JSON.stringify([map,note])}`);
  click(600,400);await sleep(900);await answer(failure("REVISION_CONFLICT","stale_revision","changed"));see("conflict");
  check(!reload().hidden&&text("map-draft-changes")==="Not available","conflict state reached");
  check(seen.every(item=>item.endsWith(`:${start}`)),`panel ${open?"open":"collapsed"}: sizes stay ${start}: ${seen.join(" ")}`);
  check(reload().getBoundingClientRect().top<document.getElementById("map-status").getBoundingClientRect().bottom,"Reload plan is on the plan state line");
  const line=getComputedStyle(document.getElementById("map-status")),plain=document.getElementById("map-status");
  check(line.whiteSpace==="nowrap"&&line.textOverflow==="ellipsis"&&line.overflow==="hidden"&&plain.title===plain.textContent,"one line with an ellipsis; the full text stays in the element and its title");
}
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorSaveLayoutEdgeTests(unittest.TestCase):
    """The map and the footer keep their size through Updating, the delayed text, a failure and a conflict."""

    def test_sizes_stay_through_the_save_states(self) -> None:
        """936 × 508 and 1256 × 708, the panel open and collapsed (UI-UX 2026-10-13 00:01:25)."""
        for window_size in ("936,508", "1256,708"):
            with self.subTest(window_size=window_size):
                self.assertEqual(run_editor_harness(SIZES, window_size=window_size, budget=20000), "PASS")


if __name__ == "__main__":
    unittest.main()
