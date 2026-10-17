"""Verify focus, Escape, the stale state and the narrow window of the preview dialog in composed Edge (8.10).

Rules of UI-UX 2026-10-15 15:58:12:
- the first focus is the title, and Tab wraps over every element that Tab reaches;
- the focus returns to the opener;
- a stale dialog locks its choices and offers Preview again;
- the footer stays visible at the minimum inner window of 936 x 508;
- at that inner size the first focus, the Tab wrap, the focus return, the collapsed lists and the stale
  state hold (QA-8.16-F2; the restore case is in the restore tests).
"""

from __future__ import annotations

import copy
import unittest

from tests.test_mission_map_preview_dialog_msedge import APPLY, NARROW, NARROW_VIEW, harness
from tests.ui_harness_support import EDGE


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class PreviewFocusEdgeTests(unittest.TestCase):
    """Fake answers, the real dialog modules and isolated headless profiles."""

    def test_tab_wrap_escape_and_the_focus_return(self) -> None:
        """Tab wraps from the confirm button to the body and back; Escape resolves null; a gone opener gives the map."""
        self.assertEqual(harness(r'''
opener.focus();let pending=apply.open(answer,{opener});await settle();
const body=shown().querySelector(".map-preview-body"),title=shown().querySelector("h2");
check(body.getAttribute("role")==="region"&&body.tabIndex===0&&body.getAttribute("aria-labelledby")===title.id,"the body is a named, focusable region");
check(shown().getAttribute("aria-labelledby")===title.id&&!shown().hasAttribute("aria-describedby")&&title.tabIndex===-1,"the dialog is named by its title");
check(key(title,"Tab",true)&&document.activeElement===action("Apply"),"Shift+Tab on the title goes to the last button");
check(key(action("Apply"),"Tab")&&document.activeElement===body,"Tab on the last button wraps to the body");
check(key(body,"Tab",true)&&document.activeElement===action("Apply"),"Shift+Tab on the body wraps to the last button");
radios()[0].focus();check(!key(radios()[0],"Tab"),"Tab inside the dialog stays native");
check(!section("Unavailable encounters").querySelector(".link-button"),"no toggle among the choices");
shown().dispatchEvent(new Event("cancel",{cancelable:true}));
check(await pending===null&&!shown()&&document.activeElement===opener,"Escape resolves null and returns the focus");
pending=apply.open(answer,{opener});await settle();opener.remove();action("Cancel").click();
check(await pending===null&&document.activeElement===document.getElementById("map-viewport"),"a gone opener gives the focus to the map");
const order=[...shown()?.querySelectorAll("*")??[]];check(order.length===0,"nothing is left open");
'''), "PASS")

    def test_the_stale_state_locks_the_choices_and_offers_preview_again(self) -> None:
        """stale() writes the line, disables every choice and replaces the confirm button in its place."""
        self.assertEqual(harness(r'''
let pending=apply.open(answer,{opener});await settle();
radios()[1].click();radios()[3].focus();frame.stale();
const status=shown().querySelector('[role="status"]');
check(status.textContent===words.stale.apply&&status.closest(".map-preview-footer"),"the stale line in the footer status");
check(getComputedStyle(status).color===getComputedStyle(document.getElementById("map-family-reason")).color,"the warning colour");
check([...shown().querySelectorAll("fieldset")].every(set=>set.disabled)&&boxes().every(box=>box.disabled),"every choice is disabled");
check(actions().map(button=>button.textContent).join()==="Cancel,Preview again"&&action("Preview again").classList.contains("button-primary"),"Preview again in place of Apply");
check(document.activeElement===shown().querySelector(".map-preview-body"),"a locked choice gives the focus to the body region");
frame.stale();check(actions().length===2,"a second call changes nothing");
action("Preview again").click();
check(JSON.stringify(await pending)==='{"again":true}'&&!shown(),"Preview again closes the dialog and asks for a new answer");
pending=apply.open(answer,{opener});await settle();const toggle=section("Map plan changes").querySelector(".link-button");
toggle.focus();frame.stale();check(document.activeElement===toggle,"the focus does not move from another control");
action("Cancel").click();await pending;
pending=apply.open(answer,{opener});await settle();action("Apply").focus();frame.stale();
check(document.activeElement===action("Preview again"),"the focus moves from the confirm button to Preview again");
action("Cancel").click();check(await pending===null,"Cancel still resolves null");
frame.stale();check(!frame.isOpen(),"stale() without an open preview does nothing");
'''), "PASS")

    def test_notices_and_the_changes_without_a_previous_plan(self) -> None:
        """Bounds and outline lines; no order text when it is null; the first-Apply line; no global last value."""
        answer = copy.deepcopy(APPLY)
        answer["capabilities"]["tier_writes"] = {"available": False, "reason": "bounds_not_confirmed"}
        answer["objects"].update(previous=False, order_changed=False, order_text=None, removed=[],
                                 **{"global": {"current": 0, "applied": None}})
        answer["other_profiles"] = []
        self.assertEqual(harness(r'''
apply.open(answer,{outline:true});await settle();
check(texts(section("Check before you apply")).join("|")===[words.bounds,words.outline].join("|"),"bounds line, then the outline line");
const changes=texts(section("Map plan changes"));
check(changes[0]==="Global container capacity: 0%"&&changes[1]===words.noPrevious&&changes[2]==="Added","no order text; the first-Apply line");
action("Cancel").click();await settle();
apply.open({...answer,capabilities:{...answer.capabilities,tier_writes:{available:false,reason:"tier_header_invalid"}}},{});await settle();
check(!headings().includes("Check before you apply"),"no bounds line for another reason; an empty section is not drawn");
''', answer), "PASS")

    def test_the_footer_stays_visible_at_the_minimum_window(self) -> None:
        """At an inner 936 x 508 the dialog fits, the body scrolls and the footer with its buttons is visible.

        The outer 936 x 508 stays as the stricter inner 912 x 416 that the test used first; 1280 x 800 is a wide window.
        """
        stricter = "check(innerWidth===912&&innerHeight===416,`inner ${innerWidth} x ${innerHeight}`);\n"
        for size, measured in ((NARROW, NARROW_VIEW), ("936,508", stricter), ("1280,800", "")):
            with self.subTest(size=size):
                self.assertEqual(harness(measured + r'''
apply.open(answer,{opener});await settle();
const box=shown().getBoundingClientRect(),body=shown().querySelector(".map-preview-body"),footer=shown().querySelector(".map-preview-footer");
check(box.top>=0&&box.left>=0&&box.bottom<=innerHeight&&box.right<=innerWidth,`the dialog fits: ${JSON.stringify(box)}`);
check(footer.getBoundingClientRect().bottom<=box.bottom+1&&actions().every(button=>{const r=button.getBoundingClientRect();return r.bottom<=innerHeight&&r.height>0}),"the footer and its buttons are visible");
check(body.scrollHeight>body.clientHeight&&body.clientHeight>=120,`the body scrolls: ${body.clientHeight}`);
check(document.documentElement.scrollWidth<=innerWidth,"no page overflow");
const legend=shown().querySelector("legend").getBoundingClientRect();check(legend.right<=box.right,"legends wrap inside the dialog");
''', window_size=size), "PASS")

    def test_apply_focus_wrap_return_lists_and_stale_state_at_the_minimum_window(self) -> None:
        """At an inner 936 x 508: first focus, Tab wrap, collapsed lists, stale state in view and the focus return."""
        self.assertEqual(harness(NARROW_VIEW + r'''
opener.focus();let pending=apply.open(answer,{opener});await settle();
const title=shown().querySelector("h2"),body=shown().querySelector(".map-preview-body");
check(document.activeElement===title&&inview(title),"the first focus is on the title, in view");
check(key(title,"Tab",true)&&document.activeElement===action("Apply"),"Shift+Tab on the title goes to the last button");
check(key(action("Apply"),"Tab")&&document.activeElement===body,"Tab on the last button wraps to the body");
check(key(body,"Tab",true)&&document.activeElement===action("Apply"),"Shift+Tab on the body wraps to the last button");
const toggles=[...shown().querySelectorAll(".link-button[aria-controls]")],listOf=(toggle)=>document.getElementById(toggle.getAttribute("aria-controls"));
const added=section("Map plan changes").querySelector(".link-button");
check(added.textContent==="7 entries"&&toggles.length>=3&&toggles.every(toggle=>listOf(toggle).hidden),"the lists start collapsed");
toggles.forEach(toggle=>toggle.click());
check(toggles.every(toggle=>!listOf(toggle).hidden&&toggle.getAttribute("aria-expanded")==="true"),"each toggle opens its list");
check(body.scrollHeight>body.clientHeight&&actions().every(inview),"with every list open the body scrolls and the buttons stay in view");
action("Apply").focus();frame.stale();
const status=shown().querySelector('[role="status"]'),again=action("Preview again");
check(status.textContent===words.stale.apply&&inview(status),`the stale line is in view: ${JSON.stringify(status.getBoundingClientRect())}`);
check(again&&inview(again)&&document.activeElement===again,"Preview again is in view and has the focus");
check(inview(shown().querySelector(".map-preview-footer p")),"the stop sentence stays in view");
check(key(again,"Tab")&&document.activeElement===body,"Tab on Preview again wraps to the body");
check(key(body,"Tab",true)&&document.activeElement===again,"Shift+Tab on the body wraps to Preview again");
shown().dispatchEvent(new Event("cancel",{cancelable:true}));
check(await pending===null&&!shown()&&document.activeElement===opener,"Escape on a stale dialog resolves null and returns the focus");
const refused={...answer,refused:true,refusals:[{reason:"records_stale",message:"Records text."}]};
pending=apply.open(refused,{opener});await settle();frame.stale();
check(actions().map(button=>button.textContent).join()==="Close,Preview again"&&actions().every(inview),"refused and stale: Close and Preview again in view");
action("Preview again").focus();check(key(action("Preview again"),"Tab")&&document.activeElement===shown().querySelector(".map-preview-body"),"refused and stale: Tab wraps");
action("Preview again").click();
check(JSON.stringify(await pending)==='{"again":true}'&&document.activeElement===opener,"Preview again returns the focus to the opener");
pending=apply.open(answer,{opener});await settle();opener.remove();action("Cancel").click();
check(await pending===null&&document.activeElement===document.getElementById("map-viewport"),"a gone opener gives the focus to the map");
''', window_size=NARROW), "PASS")

    def test_the_bridge_reads_both_previews_without_a_profile(self) -> None:
        """previewApply and previewRestore call the editor API with no argument and the mode only."""
        self.assertEqual(harness(r'''
const calls=[];window.pywebview.api.preview_plan=async(...args)=>{calls.push(["plan",...args]);return {success:true,value:answer}};
window.pywebview.api.preview_restore=async(...args)=>{calls.push(["restore",...args]);return {success:true,value:{mode:args[0]}}};
const value=await window.ServerManMapBridge.previewApply(),restore=await window.ServerManMapBridge.previewRestore("previous");
check(value.fingerprint===answer.fingerprint&&restore.mode==="previous","answers pass through");
check(JSON.stringify(calls)==='[["plan"],["restore","previous"]]',`calls: ${JSON.stringify(calls)}`);
window.pywebview.api.preview_plan=async()=>({success:false,error:{code:"INVALID_REQUEST",message:"No plan.",details:{reason:"no_saved_plan"}}});
let failed=null;try{await window.ServerManMapBridge.previewApply()}catch(error){failed=error}
check(failed&&failed.result.error.details.reason==="no_saved_plan","a refusal keeps its envelope for 8.18");
'''), "PASS")


if __name__ == "__main__":
    unittest.main()
