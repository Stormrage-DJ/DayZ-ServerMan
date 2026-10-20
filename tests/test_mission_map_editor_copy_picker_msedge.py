"""Verify the source picker of Copy from another profile in composed Edge, with a fake host (9.2).

Rules:
- UI-UX Designer 2026-10-17 17:08:16 in evidence 9.1, section 4: one radio for each profile, none chosen.
  A gate with the remaining line, the reasons of a disabled profile, the empty text and the stale state.
- Product Owner 17:14:17: the texts. PO-91-1 a: the picker disables only "this profile" and "no active
  mission link". It shows the mission only. A source refusal shows in its status line.
"""

from __future__ import annotations

import unittest

from tests.mission_map_copy_support import copy_page
from tests.ui_harness_support import EDGE


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class CopyPickerEdgeTests(unittest.TestCase):
    """Fake host answers, the real editor modules and isolated headless profiles."""

    def test_the_picker_lists_the_profiles_with_their_states(self) -> None:
        """The title, the intro, the legend, the descriptions, no choice, the gate and the focus."""
        self.assertEqual(copy_page(r'''
await start();const opener=copyButton();opener.focus();opener.click();await shownFor();
const body=dialog().querySelector(".map-preview-body");
check(dialog().querySelector("h2").textContent===C.picker_title&&document.activeElement===dialog().querySelector("h2"),"title with the focus");
check(body.textContent.includes(C.picker_intro)&&dialog().querySelector("legend").textContent===C.legend,"intro and legend");
const radios=inside('input[type="radio"]');
check(radios.map(item=>item.value).join()==="self,alpha,nolink"&&radios.every(item=>!item.checked),"three radios, none chosen");
const note=(id)=>element(radio(id).getAttribute("aria-describedby")).textContent;
check(radio("self").disabled&&note("self")===C.self,`this profile: ${note("self")}`);
check(radio("nolink").disabled&&note("nolink")===C.no_mission,`no mission link: ${note("nolink")}`);
check(!radio("alpha").disabled&&note("alpha")==="Mission: dayzOffline.enoch","the mission only: "+note("alpha"));
check(inside("label").map(item=>item.textContent).join("|")==="This one|Alpha PvE|No link","the display names");
check(!/terrain|object|revision/i.test(body.textContent.replace(C.picker_intro,"")),"no plan data in the list");
check(button(C.continue).getAttribute("aria-disabled")==="true"&&remaining()===C.picker_remaining,"the gate and the remaining line");
check(!dialog().querySelector(".map-preview-footer").textContent.includes("Stop the DayZ server"),"no stop sentence");
button(C.continue).click();await wait();
check(document.activeElement===radio("alpha")&&host.calls.length===1,"a missing choice takes the focus; no preview");
check(opener.getAttribute("aria-disabled")==="true"&&element("map-import").getAttribute("aria-disabled")==="true","busy while the picker is open");
button("Cancel").click();await wait();
check(!dialog()&&document.activeElement===element("map-show-plan")&&slot()===""&&JSON.stringify(host.calls)===JSON.stringify([["sources"]]),"cancel ends silently");
'''), "PASS")

    def test_no_other_profile_shows_the_empty_text_and_close_only(self) -> None:
        """A list with only this profile has the empty text and no Continue."""
        self.assertEqual(copy_page(r'''
await start();host.sources={success:true,value:{profiles:[profile("self","This one","dayzOffline.chernarusplus","self")]}};
await openPicker();
check(dialog().textContent.includes(C.empty)&&inside('input[type="radio"]').length===0,"the empty text, no radio");
check(inside(".action-row button").map(item=>item.textContent).join()==="Close","Close only");
button("Close").click();await wait();check(!dialog()&&!copies().length,"closed");
'''), "PASS")

    def test_a_source_refusal_shows_in_the_status_line_and_the_picker_stays(self) -> None:
        """The preview refusal text, no plan state before it; another profile can be chosen; nothing is written."""
        self.assertEqual(copy_page(r'''
await start();host.sources=listing([profile("bravo","Bravo PvP","dayzOffline.chernarusplus")]);
let refused=true;host.copy_preview=(id)=>refused?refusal("no_valid_plan",C.no_plan):copyPreviews();
await openPicker();radio("alpha").click();button(C.continue).click();
const line=()=>dialog().querySelector(".map-preview-status").textContent;
check(line()===C.checking,`the wait line: ${line()}`);
for(let index=0;index<40&&line()===C.checking;index++)await wait();
check(dialog()&&dialog().querySelector("h2").textContent===C.picker_title&&line()===C.no_plan,`the refusal stays in the picker: ${line()}`);
check(radio("alpha").checked&&button(C.continue).getAttribute("aria-disabled")!=="true","the choice stays; Continue works again");
refused=false;radio("bravo").click();check(line()==="","another choice clears the refusal");
button(C.continue).click();for(let index=0;index<60&&!button(C.confirm);index++)await wait();
check(dialog().querySelector("h2").textContent==="Copy map plan from Bravo PvP","the next dialog names the source");
check(JSON.stringify(host.calls.filter(call=>call[0]==="preview_copy"))===JSON.stringify([["preview_copy","alpha"],["preview_copy","bravo"]]),"only sources named");
check(!copies().length&&!saved.length,"nothing was written");
'''), "PASS")

    def test_a_stale_picker_reads_the_list_again(self) -> None:
        """An operation end locks the choices; Preview again reads the list again and starts unchosen."""
        self.assertEqual(copy_page(r'''
await start();await openPicker();radio("alpha").click();window.ServerManMapPreviewDialog.stale();
check(dialog().querySelector(".map-preview-stale").textContent===C.picker_stale&&!button(C.continue)&&button("Preview again"),"stale");
check(radio("alpha").disabled,"the choice locks");
button("Preview again").click();await wait();await shownFor();await wait();
check(host.calls.filter(call=>call[0]==="sources").length===2&&dialog().querySelector("legend").textContent===C.legend,"the list was read again");
check(!radio("alpha").checked&&button(C.continue).getAttribute("aria-disabled")==="true","the choice starts again");
'''), "PASS")

    def test_the_list_read_failure_shows_in_the_slot(self) -> None:
        """A failed list read opens no picker and shows the error wording."""
        self.assertEqual(copy_page(r'''
await start();host.sources=refusal("storage","Profiles are not readable.","STORAGE_FAILURE");
copyButton().focus();copyButton().click();await sleep(300);
check(!dialog()&&slot()!==""&&notice()===slot(),`list failure: ${slot()}`);
check(!copyButton().hasAttribute("aria-disabled")&&document.activeElement===element("map-show-plan"),"the opener is free and has the focus");
'''), "PASS")


if __name__ == "__main__":
    unittest.main()
