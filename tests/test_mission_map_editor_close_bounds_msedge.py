"""Typed terrain bounds as unsaved work at the editor close and the main close (5.4, Product Owner 2026-10-14 00:52:59)."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_close_msedge import CLOSE
from tests.ui_harness_support import EDGE, run_editor_harness


# A saved plan with one zone, so a changed rectangle needs the placement review.
BOUNDS = CLOSE + r'''
const sentence="The terrain bounds you entered are not saved. If you discard them, the saved plan does not change.";
const stored="Reason: The map editor data could not be stored or read. ";
const review="The terrain bounds you entered need a placement review, so they cannot be saved here. Choose Keep editing to review the placements. If you discard them, the saved plan does not change.";
const zoned=()=>{const document=plan();document.objects=[{id:"Circle_A",kind:"loot_circle",name:"Loot circle 1",visible:true,
  locked:false,centre:{x:500,z:500},radius:100,tier:null,abundance:"rich"}];return document};
const typed=()=>["x_min","z_min","width","height"].map(name=>document.getElementById(`map-bounds-${name}`).value).join();
const boundsFailed=async()=>{await load(value(plan()));fill(bounds(0,0,3000,1000));manual();await wait();await answer(storage());await sleep(100)};
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorCloseBoundsEdgeTests(unittest.TestCase):
    """Use synthetic plans, fake save answers and isolated headless profiles."""

    def test_failed_bounds_save_at_the_editor_close_saves_on_request(self) -> None:
        """After a failed bounds save, the editor close asks; Save changes saves the typed bounds and closes."""
        self.assertEqual(run_editor_harness(BOUNDS + r'''
await boundsFailed();check(text("map-bounds-error").startsWith("Bounds not saved: ")&&typed()==="0,0,3000,1000","the typed bounds stay");
const closing=settle("editor");await sleep(100);
check(text("map-dialog-title")==="Close the map editor?"&&message()===stored+sentence&&labels()==="Keep editing|Save changes|Discard changes",`bounds dialog: ${message()} / ${labels()}`);
await choose("Save changes");check(saved.length===2&&saved[1].plan.terrain.bounds.width===3000&&saved[1].revision===4,"Save submits the typed bounds");
await answer(success(5));check(await closing==="close"&&model.snapshot().terrain.bounds.width===3000&&!dialog(),"the saved bounds close");
''', budget=20000), "PASS")

    def test_failed_bounds_save_at_the_main_close_discards_only_the_typed_bounds(self) -> None:
        """Discard at the main close sends nothing; after a cancelled main close the fields show the saved bounds."""
        self.assertEqual(run_editor_harness(BOUNDS + r'''
await boundsFailed();const calls=loads(),history=model.status().history.length;const closing=settle("application");await sleep(100);
check(message()===`${stored}${sentence} ${application}`,`main close text: ${message()}`);await choose("Discard changes");
check(await closing==="close"&&saved.length===1,"the discard sends nothing");release();await sleep(100);
check(typed()==="0,0,2000,1000"&&model.snapshot().terrain.bounds.width===2000&&model.status().history.length===history&&loads()===calls,"only the typed bounds are dropped");
check(text("map-tool-notice")==="The terrain bounds you entered were discarded. The fields show the current bounds again."&&text("map-bounds-error")==="","the discard is named; no held failure");
check(await settle("editor")==="close"&&!dialog(),"nothing is left to ask");
''', budget=20000), "PASS")

    def test_open_review_with_changed_bounds_asks_at_both_closes(self) -> None:
        """The review is open: the close ends it and asks; without Save, because the review must come first."""
        self.assertEqual(run_editor_harness(BOUNDS + r'''
await load(value(zoned()));fill(bounds(0,0,3000,1000));manual();await wait();check(dialog()&&!saved.length,"the review is open");
let closing=settle("editor");await sleep(100);
check(document.querySelectorAll("dialog").length===1&&text("map-dialog-title")==="Close the map editor?"&&message()===review&&labels()==="Keep editing|Discard changes",`editor close: ${message()} / ${labels()}`);
await choose("Keep editing");check(await closing==="cancel"&&typed()==="0,0,3000,1000"&&!saved.length,"Keep editing keeps the typed bounds");
manual();await wait();closing=settle("application");await sleep(100);
check(text("map-dialog-title")==="Close DayZ-ServerMan?"&&message()===`${review} ${application}`&&labels()==="Keep editing|Discard changes","main close: bounds alone that need the review offer no save");
await choose("Discard changes");check(await closing==="close"&&!saved.length,"the discard sends nothing");
''', budget=20000), "PASS")

    def test_open_review_with_unchanged_values_asks_nothing(self) -> None:
        """An undo of a bounds change opens the history review; the typed values equal the draft, so the close asks nothing."""
        self.assertEqual(run_editor_harness(BOUNDS + r'''
await load(value(zoned()));fill(bounds(0,0,3000,1000));manual();await wait();[...dialog().querySelectorAll("button")][1].click();await wait();
await answer(success(5));check(model.snapshot().terrain.bounds.width===3000&&typed()==="0,0,3000,1000","bounds saved");
model.undo();await wait();check(dialog(),"the history review is open");
check(await settle("application")==="close"&&!dialog()&&saved.length===1,"no question, no save");
''', budget=20000), "PASS")

    def test_bounds_reasons_in_the_dialog(self) -> None:
        """UI-UX 02:21:57 (a), B1: the bounds reason with Save changes; the last failed save names the reason; a reload refusal blocks."""
        self.assertEqual(run_editor_harness(BOUNDS + r'''
const reason="Reason: The map editor data could not be stored or read. ";
await boundsFailed();let closing=settle("editor");await sleep(100);
check(message()===reason+sentence&&labels()==="Keep editing|Save changes|Discard changes",`B1: ${message()} / ${labels()}`);
await choose("Save changes");await answer(failure("STORAGE_FAILURE","storage_failure","The second bounds save failed."));await sleep(100);
check(message()==="Reason: The second bounds save failed. "+sentence&&labels()==="Keep editing|Save changes|Discard changes",`B1 again: ${message()}`);
await choose("Discard changes");await closing;release();await sleep(100);
await load(value(plan()));circle();click(500,400);await sleep(900);await answer(failure("STORAGE_FAILURE","storage_failure","The plan save failed."));
fill(bounds(0,0,3000,1000));manual();await wait();[...dialog().querySelectorAll("button")][1].click();await wait();
await answer(failure("STORAGE_FAILURE","storage_failure","The bounds save failed"));await sleep(100);
closing=settle("editor");await sleep(100);
check(message()==="Your latest changes to this map plan are not saved. Reason: The bounds save failed. "+review,`the last failed save: ${message()}`);
await choose("Discard changes");await closing;release();await sleep(100);
await load(value(plan()));fill(bounds(0,0,3000,1000));manual();await wait();await answer(failure("REVISION_CONFLICT","stale_revision","changed"));await sleep(100);
closing=settle("application");await sleep(100);
check(message()===`Reason: The saved map plan changed. They cannot be saved, because the plan must be reloaded first. ${sentence} ${application}`&&labels()==="Keep editing|Discard changes",`a reload refusal: ${message()} / ${labels()}`);
''', budget=20000), "PASS")

    def test_plan_save_drops_bounds_that_need_the_review(self) -> None:
        """B3: Save changes and discard bounds saves only the plan; with an outline the label names it; the release text."""
        self.assertEqual(run_editor_harness(BOUNDS + r'''
await load(value(zoned()));circle();click(1500,700);await sleep(900);await answer(storage());fill(bounds(0,0,3000,1000));
let closing=settle("editor");await sleep(100);
check(message()===`Your latest changes to this map plan are not saved. Reason: The map editor data could not be stored or read. ${review}`&&labels()==="Keep editing|Save changes and discard bounds|Discard changes",`B3: ${message()} / ${labels()}`);
await choose("Keep editing");await closing;tool("polygon");click(100,100);closing=settle("application");await sleep(100);
check(labels()==="Keep editing|Save changes, discard bounds and outline|Discard changes and outline",`with an outline: ${labels()}`);
const count=saved.length;await choose("Save changes, discard bounds and outline");
check(saved.length===count+1&&saved.at(-1).plan.terrain.bounds.width===2000&&saved.at(-1).plan.objects.length===2,"only the plan saves; no bounds save");
await answer(success(5));check(await closing==="close"&&!dialog()&&saved.length===count+1,"the close answers without a bounds save");
release();await sleep(100);
check(typed()==="0,0,2000,1000"&&draft()==="[]"&&text("map-tool-notice")==="The terrain bounds you entered were discarded. The fields show the current bounds again. Outline discarded.","the release drops the bounds and the outline");
''', budget=20000), "PASS")

    def test_close_waits_for_a_running_bounds_save_of_typed_bounds(self) -> None:
        """R5 (N24): only typed bounds are unsaved and their save runs; the close asks nothing and waits for it."""
        self.assertEqual(run_editor_harness(BOUNDS + r'''
await load(value(plan()));fill(bounds(0,0,3000,1000));manual();await wait();let answered=false;
const closing=settle("editor");closing.then(()=>{answered=true});await sleep(300);check(!answered&&!dialog(),"the close waits without a dialog");
await answer(success(5));check(await closing==="close"&&!dialog()&&model.snapshot().terrain.bounds.width===3000,"then closes");
''', budget=20000), "PASS")

    def test_a_load_or_a_saved_bounds_change_clears_the_bounds_failure(self) -> None:
        """QA-5.4-F11 (B9): after a load or a successful bounds save, the close dialog shows no old bounds failure."""
        self.assertEqual(run_editor_harness(BOUNDS + r'''
const ask=`${sentence} / Keep editing|Save changes|Discard changes`;
await load(value(plan()));fill(bounds(0,0,3000,1000));manual();await wait();await answer(failure("REVISION_CONFLICT","stale_revision","changed"));await sleep(100);
await load(value(plan()));fill(bounds(0,0,2500,1000));let closing=settle("editor");await sleep(100);
check(`${message()} / ${labels()}`===ask,`after a reload refusal and a load: ${message()} / ${labels()}`);
await choose("Keep editing");await closing;
fill(bounds(0,0,3000,1000));manual();await wait();await answer(storage());await sleep(100);manual();await wait();await answer(success(5,true));await sleep(100);
fill(bounds(0,0,3500,1000));closing=settle("editor");await sleep(100);
check(`${message()} / ${labels()}`===ask,`after a failed and then a successful bounds save: ${message()} / ${labels()}`);
await choose("Discard changes");check(await closing==="close","the discard closes");
''', budget=20000), "PASS")


if __name__ == "__main__":
    unittest.main()
