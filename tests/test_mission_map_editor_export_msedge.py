"""Verify Export plan and the "Plan file" section in composed Edge, with a fake host (9.1).

Rules:
- UI-UX Designer 2026-10-17 17:08:16 in evidence 9.1, sections 1 and 2: the section, the footer link, the states.
- Architect 17:10:42, section 3: the window exports the draft that it shows, also while autosave is stopped.
- Product Owner 17:14:17: the texts, and XFER-13 in the three conflict texts.
"""

from __future__ import annotations

import unittest

from tests.mission_map_transfer_support import transfer_page
from tests.ui_harness_support import EDGE


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class ExportEdgeTests(unittest.TestCase):
    """Fake host answers, the real editor modules and isolated headless profiles."""

    def test_the_plan_file_section_and_its_place(self) -> None:
        """After Map image and before Original tiers; a named group of three buttons; no shortcut."""
        self.assertEqual(transfer_page(r'''
await start();
const section=element("map-plan-file-title").closest("section"),headings=[...document.querySelectorAll("#map-panel-content h2")].map(item=>item.textContent);
check(headings.indexOf(W.heading)===headings.indexOf("Map image")+1&&headings[headings.indexOf(W.heading)+1]==="Original tiers",`order: ${headings}`);
const group=element("map-plan-file");
check(group.getAttribute("role")==="group"&&group.getAttribute("aria-labelledby")==="map-plan-file-title"&&section.contains(group),"a group named by the heading");
check([...group.querySelectorAll("button")].map(item=>item.textContent).join("|")===`${W.export}|${W.import}|${W.copy}`,"the three labels");
check([...group.querySelectorAll("button")].every(item=>!item.hasAttribute("aria-keyshortcuts")&&item.className==="button"),"no shortcut");
check(!element("map-publication").contains(group),"not in the header group");
const footer=element("map-plan-export");
check(footer.hidden&&footer.textContent===W.export&&footer.nextElementSibling===element("map-save-reload"),"the footer link before Reload plan, hidden");
check(!element("map-export").hasAttribute("aria-disabled")&&!element("map-import").hasAttribute("aria-disabled")&&!element("map-copy").hasAttribute("aria-disabled"),"all available");
'''), "PASS")

    def test_export_sends_the_draft_and_changes_nothing(self) -> None:
        """The draft, not the saved plan; the slot, the map notice and the focus; model, history and saves stay."""
        self.assertEqual(transfer_page(r'''
await start();model.add("Add circle",zone("draft",600,300));
const before=JSON.stringify(model.status()),saves=saved.length,opener=element("map-export");
let during="";window.pywebview.api.export_plan=async(document)=>{during=slot();host.calls.push(["export",structuredClone(document)]);return host.export};
opener.focus();opener.click();await wait();await wait();
check(during===W.exporting,`the exporting line: ${during}`);
const sent=host.calls.find(call=>call[0]==="export")[1];
check(sent.objects[0].id==="draft"&&JSON.stringify(sent)===JSON.stringify(model.snapshot()),"the shown draft is sent");
check(slot()===W.written&&notice()===W.written&&text("map-announcement")===W.written,`success: ${slot()} / ${notice()}`);
check(element("map-operation-notice").classList.contains("map-operation-success"),"the success look on the map");
check(document.activeElement===opener,"the focus is on the opener");
check(JSON.stringify(model.status())===before,"model, history and save state unchanged");
await sleep(900);check(saved.length===saves+1,"autosave still saves the draft as before");
'''), "PASS")

    def test_export_while_autosave_is_stopped(self) -> None:
        """The footer link shows before Reload plan; it exports the unsaved draft and adds XFER-12; Import waits."""
        self.assertEqual(transfer_page(r'''
await start();await block();
const footer=element("map-plan-export"),reload=element("map-save-reload");
check(!footer.hidden&&!reload.hidden&&footer.compareDocumentPosition(reload)&Node.DOCUMENT_POSITION_FOLLOWING,"Export plan, then Reload plan");
check(status().endsWith(W.keep)&&status().startsWith("Not saved: The saved map plan changed. Reload plan loads it and discards your unsaved changes."),`XFER-13: ${status()}`);
check(document.activeElement!==footer,"the refusal path keeps its focus rule");
check(element("map-import").getAttribute("aria-disabled")==="true"&&reasons("map-import")===W.conflict,`Import waits: ${reasons("map-import")}`);
check(element("map-import").getAttribute("aria-describedby")==="map-import-reason","the reason describes Import");
check(!footer.hasAttribute("aria-disabled")&&footer.getAttribute("aria-describedby")==="map-save-notice","the link keeps the save notice");
footer.click();await wait();await wait();
const sent=host.calls.find(call=>call[0]==="export")[1];
check(sent.objects.some(item=>item.id==="unsaved"),"the unsaved draft is exported, not the saved plan");
check(slot()===`${W.written} ${W.still_unsaved}`,`XFER-12: ${slot()}`);
check(window.ServerManMapAutosave.blocked()&&!reload.hidden,"autosave stays stopped; Reload plan stays");
element("map-import").click();await wait();check(!host.calls.some(call=>call[0]==="select"),"Import does nothing while blocked");
'''), "PASS")

    def test_cancel_and_refusals(self) -> None:
        """A cancel shows no text; the D1 refusal lists each object; the path refusals name the base file name."""
        self.assertEqual(transfer_page(r'''
await start();const opener=element("map-export");
host.export={success:true,value:{cancelled:true}};opener.focus();opener.click();await wait();await wait();
check(slot()===""&&notice()===""&&document.activeElement===opener,"cancel: no text, the focus on the opener");
const deny=(details,code="INVALID_REQUEST")=>({success:false,error:{code,message:"host",retryable:false,details}});
host.export=deny({reason:"invalid_document",problems:[{object_id:"observer",rule:"invalid_document",message:"radius is too large"},
  {object_id:null,rule:"invalid_document",message:"the plan has more than 50000 polygon vertices"}]});
model.add("Add circle",{...zone("observer"),name:"North zone"});opener.click();await wait();await wait();
check(notice()===`${W.export_invalid}\nNorth zone: radius is too large\nthe plan has more than 50000 polygon vertices`,`D1: ${notice()}`);
check(looks()==="map-operation-error"&&getComputedStyle(element("map-operation-notice")).whiteSpace==="pre-line","one row for each object");
host.export=deny({reason:"protected",file_name:"chosen.json"},"PATH_OUTSIDE_ALLOWED_ROOT");opener.click();await wait();await wait();
check(slot()===W.protected,`protected: ${slot()}`);
host.export=deny({reason:"not_written",file_name:"chosen.json"},"STORAGE_FAILURE");opener.click();await wait();await wait();
check(slot()===W.not_written&&!slot().includes("\\"),`not written: ${slot()}`);
'''), "PASS")

    def test_export_needs_a_loaded_plan_and_the_conflict_texts_name_it(self) -> None:
        """No plan: aria-disabled with XFER-5, and a click does nothing; the three conflict texts end with XFER-13."""
        self.assertEqual(transfer_page(r'''
await load(value(null));const opener=element("map-export");
check(opener.getAttribute("aria-disabled")==="true"&&reasons("map-export")===W.export_loading,`no plan: ${reasons("map-export")}`);
opener.click();await wait();check(!host.calls.length,"a click does nothing");
const words=window.ServerManMapWording;
check([words.reloadStale,words.reloadInvalid,words.reloadReason("The profile changed.")].every(item=>item.endsWith(` ${W.keep}`)),"XFER-13 ends each text");
check(words.reloadInvalid==="Not saved: The saved map plan is not valid. Reload plan shows it read only and discards your unsaved changes. "+W.keep,"reloadInvalid keeps its text");
await load(value(plan()));check(!opener.hasAttribute("aria-disabled")&&reasons("map-export")==="","available after the load");
'''), "PASS")


if __name__ == "__main__":
    unittest.main()
