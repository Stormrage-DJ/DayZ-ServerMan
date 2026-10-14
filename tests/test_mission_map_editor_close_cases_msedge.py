"""Close dialog cases of the 5.4 QA round in composed Edge: outline discard, reasons, waits, join and reload guard."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_close_msedge import CLOSE
from tests.ui_harness_support import EDGE, run_editor_harness


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorCloseCaseEdgeTests(unittest.TestCase):
    """Use synthetic plans, fake save answers and isolated headless profiles."""

    def test_outline_only_discard_keeps_the_draft_and_its_history(self) -> None:
        """QA-5.4-F1 (N13): a cancelled main close after Discard outline drops only the outline."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
await load(value(plan()));circle();click(500,400);await sleep(900);await answer(success(5));click(600,400);await sleep(900);
await answer(success(6));tool("polygon");click(100,100);click(500,100);const history=model.status().history.length,calls=loads();
const closing=settle("application");await sleep(100);await choose("Discard outline");check(await closing==="close","the outline discard closes");
document.getElementById("map-announcement").textContent="";release();await sleep(100);
check(draft()==="[]"&&model.snapshot().objects.length===2&&model.status().history.length===history&&loads()===calls,"only the outline is dropped; no load");
check(text("map-tool-notice")==="Outline discarded."&&text("map-announcement")==="Outline discarded."&&status()==="Saved","the text names the outline");
''', budget=20000), "PASS")

    def test_dialog_reasons_end_with_a_full_stop(self) -> None:
        """N8 and N11: an object refusal and invalid saved data name their bare reasons."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
await load(value(plan()));circle();click(500,400);await sleep(900);const id=model.snapshot().objects[0].id;
await answer(refuse(id,"the circle is outside the bounds"));let closing=settle("editor");await sleep(100);
check(message().startsWith("Your latest changes to this map plan are not saved. Reason: Loot circle 1: the circle is outside the bounds. If"),`object reason: ${message()}`);
await choose("Keep editing");await closing;click(600,400);await sleep(900);await answer(failure("REVISION_CONFLICT","invalid_saved_data","bad"));
closing=settle("editor");await sleep(100);
check(message()==="Your latest changes to this map plan are not saved. Reason: The saved map plan is not valid. They cannot be saved, because the plan must be reloaded first. If you discard them, the saved plan does not change.",`invalid data: ${message()}`);
''', budget=20000), "PASS")

    def test_close_waits_for_a_running_bounds_save_then_saves_the_draft(self) -> None:
        """N16b: an edit during a bounds save; the close asks nothing, waits, then saves with the new revision."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
await load(value(plan()));fill(bounds(0,0,3000,1000));manual();await wait();circle();click(500,400);await sleep(100);
const closing=settle("editor");await sleep(1200);check(!dialog()&&saved.length===1&&status()==="Closing","the close waits for the bounds save");
await answer(success(5,true));await sleep(300);
check(saved.length===2&&saved[1].revision===5&&saved[1].plan.objects.length===1&&saved[1].plan.terrain.bounds.width===3000,"then the draft saves at once");
await answer(success(6));check(await closing==="close"&&!dialog(),"and closes");
''', budget=20000), "PASS")

    def test_a_repeated_close_request_keeps_the_given_answer(self) -> None:
        """Q7: after "close" for an unsaved draft, a new request answers "close" at once and saves nothing."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
await failed();const count=saved.length;let closing=settle("editor");await sleep(100);await choose("Discard changes");
check(await closing==="close","discarded");closing=settle("application");await sleep(100);
check(await closing==="close"&&!dialog()&&saved.length===count,"the repeated request answers close without a dialog or a save");
''', budget=20000), "PASS")

    def test_a_real_discard_keeps_the_committed_plan(self) -> None:
        """A discard after a committed save: the fake backend keeps that plan, and the release shows it."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
await load(value(plan()));circle();click(500,400);await sleep(900);await answer(success(5));
const committed=structuredClone(saved.at(-1).plan);
click(600,400);await sleep(900);await answer(storage());const count=saved.length;click(700,400);await sleep(100);
const closing=settle("application");await sleep(100);check(saved.length===count+1,"the waiting edit saves at once");
await answer(storage());await sleep(100);await choose("Discard changes");check(await closing==="close"&&saved.length===count+1,"the discard sends nothing");
editorAnswer={success:true,value:{...value(committed),revision:5,state:"VALID"}};release();await sleep(100);
check(JSON.stringify(model.snapshot())===JSON.stringify(committed)&&model.status().revision===5&&!model.status().dirty,"the release shows the committed plan");
await sleep(1500);check(saved.length===count+1,"no save follows");
''', budget=20000), "PASS")

    def test_main_close_replaces_an_open_editor_dialog(self) -> None:
        """QA-5.4-F6 (UI-UX 00:53:00): one dialog in the application form; one answer ends both settles."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
await failed();const own=settle("editor");await sleep(100);check(text("map-dialog-title")==="Close the map editor?","the editor form");
const main=settle("application");await sleep(50);
check(main===own&&document.querySelectorAll("dialog").length===1&&text("map-dialog-title")==="Close DayZ-ServerMan?","one dialog in the application form");
check(message()===`${unsaved} ${application}`&&labels()==="Keep editing|Save changes|Discard changes"&&document.activeElement.textContent==="Keep editing","same content, the application sentence, Keep editing focused");
dialog().dispatchEvent(new Event("cancel",{cancelable:true}));check(await own==="cancel"&&await main==="cancel"&&!dialog(),"Escape keeps editing for both");
const again=settle("editor");await sleep(100);const joined=settle("application");await sleep(50);await choose("Discard changes");
check(await again==="close"&&await joined==="close"&&saved.length===1,"one discard ends both settles");
''', budget=20000), "PASS")

    def test_reload_keys_and_unload_are_guarded(self) -> None:
        """QA-5.4-F4: F5, Ctrl+R and Ctrl+P are blocked; unload asks only for unsaved work outside a close."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
const key=(init)=>{const event=new KeyboardEvent("keydown",{...init,bubbles:true,cancelable:true});document.body.dispatchEvent(event);return event.defaultPrevented};
const unload=()=>{const event=new Event("beforeunload",{cancelable:true});window.dispatchEvent(event);return event.defaultPrevented};
await load(value(plan()));check(key({key:"F5"})&&key({key:"r",ctrlKey:true})&&key({key:"P",ctrlKey:true})&&!key({key:"a"}),"reload and print keys blocked");
check(!unload(),"a saved plan unloads freely");circle();click(500,400);check(unload(),"a waiting edit asks");await sleep(900);await answer(success(5));
check(!unload(),"saved again");tool("polygon");click(100,100);check(unload(),"an outline asks");tool("polygon");
const closing=settle("editor");await sleep(100);check(!unload(),"no question while the close runs");await choose("Keep editing");await closing;
document.getElementById("map-outline-discard").click();check(!unload(),"nothing unsaved");
fill(bounds(0,0,3000,1000));check(unload(),"typed bounds ask");
''', budget=20000), "PASS")

    def test_a_late_join_notice_never_starts_a_close(self) -> None:
        """QA-5.4-F7 (J4, J5): the host join form after the editor settle ended opens no dialog and freezes nothing."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
const join=()=>window.ServerManMapEditor.settleForClose("application",true);
await failed();let own=settle("editor");await sleep(100);await choose("Keep editing");await own;
check(await join()===null&&!dialog()&&status()!=="Closing"&&model.editable(),"J4: the late notice does nothing");
release();await sleep(100);check(!dialog()&&model.editable(),"J4: the release after it changes nothing");
own=settle("editor");await sleep(100);await choose("Keep editing");await own;release();
check(await join()===null,"J5: a notice after the release");await sleep(1500);
check(!dialog()&&status()!=="Closing"&&model.editable()&&saved.length===1,"J5: no dialog, no frozen editor, no save");
''', budget=20000), "PASS")

    def test_a_join_during_a_retry_asks_in_the_application_form(self) -> None:
        """R17 (J2): the main close joins while Save changes runs; the next dialog has the application form."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
await failed();const own=settle("editor");await sleep(100);await choose("Save changes");
const main=window.ServerManMapEditor.settleForClose("application",true);await sleep(50);check(main===own&&!dialog(),"the join shares the running settle");
await answer(storage());await sleep(100);
check(document.querySelectorAll("dialog").length===1&&text("map-dialog-title")==="Close DayZ-ServerMan?"&&message()===`${unsaved} ${application}`,`application form: ${message()}`);
await choose("Keep editing");check(await own==="cancel"&&await main==="cancel","one answer ends both");
''', budget=20000), "PASS")


if __name__ == "__main__":
    unittest.main()
