"""Verify the Apply and Restore controls, the status slot, the map notices and the paused editor in composed Edge.

Rules: UI-UX Designer 2026-10-15 15:58:12, sections 1, 2, 7 and Narrow Window (leaf 8.18). Texts: Product Owner
2026-10-15 15:57:10 and 16:03:40 in evidence 8.10. The minimum window is an inner 936 x 508.
"""

from __future__ import annotations

import unittest

from tests.mission_map_publish_support import page
from tests.test_mission_map_preview_dialog_msedge import NARROW
from tests.ui_harness_support import EDGE

# A plan with one zone, and the box helpers
ZONE = r'''
const zone={id:"z1",name:"Zone one",kind:"loot_circle",visible:true,locked:false,centre:{x:500,z:500},radius:150,tier:null,abundance:"rich"};
const zoned=()=>{const document=plan();document.objects=[structuredClone(zone)];return document};
const box=(id)=>element(id).getBoundingClientRect();
const apart=(a,b)=>a.right<=b.left||b.right<=a.left||a.bottom<=b.top||b.bottom<=a.top;
const key=(target,name,options={})=>{target.focus();const event=new KeyboardEvent("keydown",{key:name,bubbles:true,cancelable:true,...options});
  target.dispatchEvent(event);return event};
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class PublishLayoutEdgeTests(unittest.TestCase):
    """Fake answers, the real editor modules and isolated headless profiles."""

    def test_the_controls_their_texts_and_the_restore_group(self) -> None:
        """Header place on one line at 936 px, labels, the mode group by keyboard and pointer, and the focus return."""
        self.assertEqual(page(ZONE + r'''
check(innerWidth===936&&innerHeight===508,`inner window ${innerWidth} x ${innerHeight}`);
await start(value(zoned()));
const words=window.ServerManMapPublishWords;
for(const [name,wanted] of [["apply",T.apply],["restore",T.restore],["previous",T.previous],["original",T.original],["term",T.term],
  ["reading",T.reading],["checkAgain",T.check],["cleared",T.cleared],["deferred",T.deferred],["notOpened",T.not_opened]])
  check(words[name]===wanted,`the Product Owner text of ${name}`);
check(words.paused.apply===T.paused_apply&&words.paused.restore===T.paused_restore,"both paused-editor notices");
check(text("map-apply")===T.apply&&text("map-restore")===T.restore&&text("map-restore-previous")===T.previous
  &&text("map-restore-original")===T.original&&text("map-operation-term")===T.term&&text("map-check-again")===T.check,"the shown labels");
const group=element("map-restore-modes"),restore=element("map-restore"),header=element("map-identity");
check(element("map-publication").parentElement===header&&element("map-identity-fields").nextElementSibling===element("map-publication"),
  "the group follows the identity fields in the header");
for(const id of ["map-apply","map-restore","map-restore-previous","map-restore-original"]){
  check(element(id).className==="button"&&!element(id).hasAttribute("aria-keyshortcuts"),`${id}: a normal button without a shortcut`);
  check(element(id).dataset.busyLock==="control",`${id} is busy-marked`);}
check(restore.getAttribute("aria-expanded")==="false"&&restore.getAttribute("aria-controls")===group.id&&group.hidden
  &&group.getAttribute("role")==="group"&&group.getAttribute("aria-labelledby")===restore.id,"the disclosure and its group");
const title=box("map-title"),identity=box("map-identity-fields"),opener=box("map-apply");
check(opener.top<title.bottom&&title.top<opener.bottom&&opener.top<identity.bottom&&box("map-restore").right<=box("map-identity").right+1,
  `title, identity fields and the controls on one line: ${JSON.stringify([title,identity,opener])}`);
restore.click();
check(!group.hidden&&restore.getAttribute("aria-expanded")==="true","Restore opens the group");
const modes=box("map-restore-modes");
check(modes.top>=box("map-restore").bottom&&Math.abs(modes.right-box("map-restore").right)<=1&&modes.left>=0&&modes.right<=innerWidth
  &&modes.bottom>box("map-viewport").top,`under Restore, right-aligned, over the map: ${JSON.stringify(modes)}`);
const follows=(first,second)=>Boolean(first.compareDocumentPosition(second)&Node.DOCUMENT_POSITION_FOLLOWING);
check(follows(restore,element("map-restore-previous"))&&follows(element("map-restore-previous"),element("map-restore-original")),
  "Restore, previous state, original in the Tab order");
window.ServerManMapLayers.select("z1");check(window.ServerManMapLayers.selected()==="z1","a selected zone");
const escape=key(element("map-restore-previous"),"Escape");
check(group.hidden&&document.activeElement===restore&&escape.defaultPrevented&&window.ServerManMapLayers.selected()==="z1",
  "Escape closes the group, gives the focus to Restore and keeps the selection");
restore.click();element("map-restore-original").focus();element("map-viewport").focus();
check(group.hidden,"the focus leaving the group closes it");
restore.click();element("map-viewport").dispatchEvent(new PointerEvent("pointerdown",{bubbles:true}));
check(group.hidden,"a pointer down outside closes it");
restore.click();element("map-restore-previous").click();await opened();
check(group.hidden&&shown().querySelector("h2").textContent==="Restore previous state","a mode opens its preview and closes the group");
action("Cancel").click();await wait();check(document.activeElement===restore,"the focus returns to Restore");
element("map-apply").click();await opened();action("Cancel").click();await wait();
check(document.activeElement===element("map-apply"),"the focus returns to Apply");
''', window_size=NARROW), "PASS")

    def test_the_status_slot_and_the_notices_keep_their_place(self) -> None:
        """The fifth slot shows at the first activation without changing the footer height; the notices never overlap."""
        self.assertEqual(page(ZONE + r'''
await start(value(zoned()));
const columns=()=>getComputedStyle(element("map-status-area").querySelector("dl")).gridTemplateColumns.split(" ").length;
const height=box("map-status-area").height;
check(element("map-operation-item").hidden&&columns()===4,"no slot before the first activation");
lane.hold=true;activate("apply");await wait();
check(!element("map-operation-item").hidden&&columns()===5&&box("map-status-area").height===height,
  `the slot shows; the footer keeps its height: ${box("map-status-area").height} / ${height}`);
const slotStyle=getComputedStyle(element("map-operation-status"));
check(slotStyle.whiteSpace==="nowrap"&&slotStyle.textOverflow==="ellipsis","one line with an ellipsis");
check(slotStyle.color===getComputedStyle(element("map-coordinates").closest("div").querySelector("dt")).color,"reading uses the muted colour");
lane.previews.apply=fail("REVISION_CONFLICT","The saved map plan is not valid.",{reason:"invalid_saved_data"});lane.release();
await wait();await wait();
check(box("map-status-area").height===height&&element("map-operation-status").scrollWidth>element("map-operation-status").clientWidth
  &&element("map-operation-status").title===slot(),"a long refusal keeps the footer height; the title holds the full text");
check(getComputedStyle(element("map-operation-status")).color===getComputedStyle(element("map-operation-notice")).borderColor,
  "the refusal has the error colour");
tool("select");tool("circle");click(800,800);
window.pywebview.api.save_plan=async()=>refuse(model.snapshot().objects[0].id,"the circle is not valid in a very long reason text that wraps");
await sleep(1000);await wait();
const operation=box("map-operation-notice"),save=box("map-save-notice"),map=box("map-viewport");
check(operation.height>0&&save.height>0&&apart(operation,save)&&operation.bottom<=save.top,
  `the operation notice above the save notice, apart: ${JSON.stringify([operation,save])}`);
check(save.right<=map.right&&map.right-save.right<=16&&map.bottom-save.bottom<=16&&apart(save,box("map-tool-notice"))
  &&apart(operation,box("map-view-label")),"both at the bottom right of the map");
''', window_size=NARROW), "PASS")

    def test_the_paused_editor_refuses_edits_and_still_moves_zooms_and_selects(self) -> None:
        """From the submission to the end: edits refused, Select, pan, zoom and reading stay; the end gives edits back."""
        self.assertEqual(page(ZONE + r'''
await start(value(zoned()));
for(const mode of ["apply","previous"]){
  activate(mode);await opened();element("map-announcement").textContent="";
  action(mode==="apply"?"Apply":"Restore previous state").click();await wait();await wait();
  check(text("map-tool-notice")===(mode==="apply"?T.paused_apply:T.paused_restore)&&text("map-announcement")!==text("map-tool-notice"),
    `${mode}: the pause text on the map, not announced alone`);
  for(const id of ["map-tool-circle","map-tool-polygon","map-tool-encounter","map-tool-inspect","map-undo","map-redo",
    "map-global-capacity","map-background-choose","map-calibrate","map-bounds-detected"])check(element(id).disabled,`${id} is paused`);
  check(element("map-bounds-fields").disabled,"the bounds fields are paused");
  for(const id of ["map-fit","map-coordinate-mode","map-tool-select","map-tiers-toggle"])check(!element(id).disabled,`${id} stays`);
  check(!model.status().writable&&model.status().paused,"the model refuses edits");
  const count=model.snapshot().objects.length;click(200,200);click(500,500,true);
  check(model.snapshot().objects.length===count&&window.ServerManMapLayers.selected()==="z1","Shift+click selects; nothing is placed");
  check(element("map-properties-name-field").disabled&&element("map-properties-delete").disabled,"Properties are read only");
  key(element("map-viewport"),"z",{ctrlKey:true});await wait();check(model.snapshot().objects.length===count,"Ctrl+Z changes nothing");
  key(element("map-viewport"),"Delete");check(model.snapshot().objects.length===count,"Delete changes nothing");
  const scale=window.ServerManMapViewportUi.view().snapshot().scale;
  element("map-viewport").dispatchEvent(new WheelEvent("wheel",{deltaY:-200,clientX:300,clientY:300,bubbles:true,cancelable:true}));await wait();
  check(window.ServerManMapViewportUi.view().snapshot().scale!==scale,"the wheel still zooms");
  element("map-fit").click();await wait();
  key(element("map-viewport"),"Escape");check(window.ServerManMapLayers.selected()===null,"Escape still clears the selection");
  const ownRecord=own();emit(record(ownRecord.operation_id,ownRecord.kind,"SUCCEEDED",{result:{result:"APPLIED"}}));await poll();
  check(text("map-tool-notice")===""&&model.status().writable&&!element("map-tool-circle").disabled,"the end gives the edits back");
  check(!element("map-bounds-fields").disabled&&!element("map-global-capacity").disabled,"the bounds and the slider are back");
  tool("circle");click(800,200);check(model.snapshot().objects.length===count+1,"a circle can be placed again");
  await model.undo();tool("circle");
}
'''), "PASS")


if __name__ == "__main__":
    unittest.main()
