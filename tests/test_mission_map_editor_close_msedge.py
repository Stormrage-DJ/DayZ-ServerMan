"""Verify the close dialogs that protect unsaved work in composed Edge (5.4, R15, Save Status and Close Dialogs)."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_autosave_msedge import AUTOSAVE
from tests.ui_harness_support import EDGE, run_editor_harness


# The host calls settleForClose with its reason and releaseClose after a cancelled application close.
CLOSE = AUTOSAVE + r'''
const settle=(reason)=>window.ServerManMapEditor.settleForClose(reason),release=()=>window.ServerManMapEditor.releaseClose();
const storage=()=>failure("STORAGE_FAILURE","storage_failure","The map editor data could not be stored or read.");
const dialog=()=>document.querySelector("dialog"),message=()=>dialog().querySelector("p").textContent;
const labels=()=>[...dialog().querySelectorAll("button")].map(button=>button.textContent).join("|");
const choose=async(label)=>{[...dialog().querySelectorAll("button")].find(button=>button.textContent===label).click();await sleep(100)};
const circle=()=>{if(text("map-tool-status")!=="Circle")tool("circle")};
const loads=()=>editorCalls.filter(call=>call==="load").length;
const failed=async()=>{await load(value(plan()));circle();click(500,400);await sleep(900);await answer(storage())};
const unsaved="Your latest changes to this map plan are not saved. Reason: The map editor data could not be stored or read. If you discard them, the saved plan does not change.";
const application="Keep editing keeps DayZ-ServerMan open.",notice=()=>text("map-save-notice");
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorCloseEdgeTests(unittest.TestCase):
    """Use synthetic plans, fake save answers and isolated headless profiles."""

    def test_saved_and_pending_drafts_close_without_a_dialog(self) -> None:
        """A clean draft closes at once; a waiting edit and a running save end before the close answers."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
await load(value(plan()));const first=settle("editor");
check(first===settle("application")&&await first==="close"&&!dialog()&&status()==="Closing","a clean draft closes at once; a second request shares it");
check(await settle("editor")==="close","a given close answer stays until the release");
release();circle();click(500,400);const closing=settle("application");await wait();
check(saved.length===1&&saved[0].plan.objects.length===1&&!dialog(),"a waiting edit saves at once, without the 800 ms wait");
await answer(success(5));check(await closing==="close"&&model.status().revision===5&&!dialog(),"the saved draft closes");
release();click(600,400);await sleep(900);check(saved.length===2,"a save runs");
const running=settle("editor");await sleep(500);check(!dialog()&&status()==="Closing"&&saved.length===2,"the close waits for the running save");
await answer(success(6));check(await running==="close"&&model.status().revision===6,"the running save ends the close");
''', budget=20000), "PASS")

    def test_failed_save_offers_save_discard_and_keep_editing(self) -> None:
        """Keep editing cancels, Save changes tries again and asks again after a failure, a saved retry closes."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
await failed();let closing=settle("editor");await sleep(100);
check(dialog()&&labels()==="Keep editing|Save changes|Discard changes"&&saved.length===1,`three choices; no automatic retry: ${labels()}`);
check(text("map-dialog-title")==="Close the map editor?"&&message()===unsaved,`the text names the changes and the reason: ${message()}`);
check(document.activeElement.textContent==="Keep editing"&&dialog().querySelector(".button-danger").textContent==="Discard changes","Keep editing first; discard in the danger style");
const failedText="Not saved: The map editor data could not be stored or read.";
await choose("Keep editing");check(await closing==="cancel"&&!dialog()&&model.editable(),"Keep editing keeps the editor open and editable");
check(status()===failedText&&notice()===failedText&&document.getElementById("map-save-notice").classList.contains("map-save-error"),"Keep editing keeps the failure and its notice");
const calls=loads();release();check(loads()===calls&&status()===failedText,"the host release after Keep editing changes nothing");
await sleep(1500);check(saved.length===1&&status()===failedText,"no save is sent after Keep editing");
click(600,400);await sleep(900);check(saved.length===2,"the next edit saves");await answer(storage());
closing=settle("editor");await sleep(100);dialog().dispatchEvent(new Event("cancel",{cancelable:true}));
check(await closing==="cancel"&&!dialog()&&status()===failedText,"Escape keeps editing and the failure");await sleep(1500);check(saved.length===2,"no save after Escape");
click(700,400);await sleep(900);await answer(storage());
closing=settle("editor");await sleep(100);await choose("Save changes");check(saved.length===4&&!dialog(),"Save changes tries again");
await answer(storage());await sleep(100);check(dialog()&&message()===unsaved,"a failed retry asks again");
await choose("Save changes");await answer(success(5));
check(await closing==="close"&&!dialog()&&model.status().revision===5&&status()==="Closing","a saved retry closes");
''', budget=20000), "PASS")

    def test_discard_sends_nothing_and_keeps_the_saved_plan(self) -> None:
        """Discard closes without a save; the application close names the application; a conflict cannot save."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
await failed();const count=saved.length,calls=loads();let closing=settle("application");await sleep(100);
check(text("map-dialog-title")==="Close DayZ-ServerMan?"&&message()===`${unsaved} ${application}`,`the application close names the application: ${message()}`);
await choose("Discard changes");
check(await closing==="close"&&saved.length===count&&loads()===calls&&status()==="Closing","Discard sends nothing and closes");
check(model.status().dirty&&model.status().revision===4,"the saved plan stays at its revision");
release();await sleep(100);check(saved.length===count&&!model.status().dirty,"the release shows the saved plan and sends nothing");
circle();click(800,400);await sleep(900);await answer(failure("REVISION_CONFLICT","stale_revision","changed"));
const before=saved.length;closing=settle("editor");await sleep(100);
check(labels()==="Keep editing|Discard changes","a reload refusal offers no Save changes");
check(message()==="Your latest changes to this map plan are not saved. Reason: The saved map plan changed. They cannot be saved, because the plan must be reloaded first. If you discard them, the saved plan does not change.",`the blocked text without Reload plan: ${message()}`);
await choose("Discard changes");check(await closing==="close"&&saved.length===before,"discard after a conflict sends nothing");
''', budget=20000), "PASS")

    def test_unfinished_outline_asks_also_at_the_main_close(self) -> None:
        """Keep editing keeps the outline; Discard outline closes and saves nothing."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
await load(value(plan()));tool("polygon");click(100,100);click(500,100);let closing=settle("editor");await sleep(100);
check(labels()==="Keep editing|Discard outline"&&dialog().querySelector(".button-danger").textContent==="Discard outline","outline choices");
check(message()==="The outline you are drawing is not finished (2 points). It is not part of the plan yet.",`outline text: ${message()}`);
await choose("Keep editing");check(await closing==="cancel"&&JSON.parse(draft()).length===2&&!dialog(),"Keep editing keeps the outline");
closing=settle("application");await sleep(100);
check(text("map-dialog-title")==="Close DayZ-ServerMan?"&&labels()==="Keep editing|Discard outline","also at the main close");
check(message()==="The outline you are drawing is not finished (2 points). It is not part of the plan yet. "+application,`the application sentence last: ${message()}`);
await choose("Discard outline");check(await closing==="close"&&saved.length===0,"Discard outline closes and saves nothing");
''', budget=20000), "PASS")

    def test_one_dialog_names_a_failed_save_and_an_outline(self) -> None:
        """Both items in one dialog; the combined save closes on success; the combined discard sends nothing."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
await failed();tool("polygon");click(100,100);click(500,100);click(100,500);let closing=settle("application");await sleep(100);
check(labels()==="Keep editing|Save changes and discard outline|Discard changes and outline",`combined choices: ${labels()}`);
check(message()===`${unsaved} The outline you are drawing is not finished (3 points). It is not part of the plan yet. ${application}`,`combined text: ${message()}`);
await choose("Save changes and discard outline");await answer(success(5));
check(await closing==="close"&&saved.at(-1).plan.objects.length===1&&!dialog(),"the saved changes close without asking about the outline");
const history=model.status().history.length;release();await sleep(100);
check(draft()==="[]"&&model.status().history.length===history&&text("map-tool-notice")==="Outline discarded.","N18: a cancelled main close keeps the outline dropped and the history");
circle();click(700,400);await sleep(900);await answer(storage());tool("polygon");click(100,100);const count=saved.length;
closing=settle("editor");await sleep(100);await choose("Discard changes and outline");
check(await closing==="close"&&saved.length===count,"the combined discard sends nothing");
''', budget=20000), "PASS")

    def test_answers_bind_to_the_close_and_the_load_count(self) -> None:
        """A released or reloaded settle never asks; a save committed during a cancelled close is adopted."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
await load(value(plan()));circle();click(500,400);await sleep(900);const old=settle("editor");release();
await load(value(plan()));await answer(storage());
check(await old==="cancel"&&!dialog()&&model.status().revision===4,"a release and a load end the settle; the late failure is dropped");
circle();click(500,400);await sleep(900);tool("polygon");click(100,100);const closing=settle("editor");
await answer(success(5));await sleep(100);check(dialog()&&labels()==="Keep editing|Discard outline","the outline still asks");
check(message()==="The outline you are drawing is not finished (1 point). It is not part of the plan yet.",`one point: ${message()}`);
await choose("Keep editing");check(await closing==="cancel"&&model.status().revision===5&&status()==="Saved","the committed answer is adopted");
tool("circle");click(800,400);await sleep(900);check(saved.at(-1).revision===5,"the next save uses the adopted revision");
''', budget=20000), "PASS")

    def test_a_discard_stays_final_when_another_editor_keeps_editing(self) -> None:
        """A discard, then a release because another editor kept editing: the saved plan shows; no save; no outline."""
        self.assertEqual(run_editor_harness(CLOSE + r'''
await failed();tool("polygon");click(100,100);click(500,100);const count=saved.length,calls=loads();
const closing=settle("application");await sleep(100);await choose("Discard changes and outline");check(await closing==="close","discarded");
document.getElementById("map-announcement").textContent="";release();await sleep(100);
const discarded="Your changes were discarded. The saved plan is shown.";
check(loads()===calls+1&&!model.status().dirty&&model.snapshot().objects.length===0&&model.status().revision===4,"the release loads the saved plan");
check(status()===discarded&&text("map-announcement")===discarded&&notice()==="","the discard is announced without a notice");
check(draft()==="[]"&&model.editable(),"the outline is dropped; the editor works again");
await sleep(1500);check(saved.length===count,"no save follows the discard");
''', budget=20000), "PASS")


if __name__ == "__main__":
    unittest.main()
