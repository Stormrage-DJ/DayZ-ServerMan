"""Verify the sections of the adoption preview, a refusal, the stale state and the second Apply button in composed Edge (10.16).

Rules:
- UI-UX Designer 2026-10-16 14:21:59 in evidence 10.1: section 4 (the dialog) and section 3 (the second button).
- Architect 14:33:32: `capture.adoption`.
- Product Owner: the texts of evidence 10.1, 10.2 and 10.13.

The answers carry the texts of the real review builder. The inner window of every test is 936 x 508.
"""

from __future__ import annotations

import unittest

from tests.mission_map_adopt_support import page
from tests.test_mission_map_preview_dialog_msedge import NARROW
from tests.ui_harness_support import EDGE


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class AdoptDialogEdgeTests(unittest.TestCase):
    """Fake answers, the real editor modules and isolated headless profiles."""

    def test_a_copy_after_a_path_change_and_a_copy_after_a_capture_refusal(self) -> None:
        """The route texts, the other profiles with their warning, and the rows of the records."""
        self.assertEqual(page(r'''
check(innerWidth===936&&innerHeight===508,`inner window ${innerWidth} x ${innerHeight}`);
await start(offered("copy"));
for(const name of ["copy","capture"]){
  reply(name);control().click();await opened();
  check(JSON.stringify(headings())===JSON.stringify(["What happened","What adoption writes","What stays unchanged"]),`${name}: ${headings()}`);
  const happened=section("What happened"),notices=[...happened.querySelectorAll(".notice")].map(item=>item.textContent);
  check(notices[0]===A[name].happened&&notices[1]===A[name].other_profiles_text,`${name}: the case text and ADOPT-13`);
  check(happened.querySelector(".notice-warning")?.textContent===A[name].other_profiles_text,`${name}: ADOPT-13 is a warning`);
  check(lines(happened).slice(2).join("|")==="Bravo|gone (deleted profile)","the other profiles, by name");
  check(lines(section("What adoption writes")).slice(2).join("|")===A[name].records.join("|"),`${name}: the rows`);
  check(lines(section("What stays unchanged")).join("|")===A[name].unchanged.join("|"),`${name}: the unchanged lines`);
  check(name==="capture"?A.capture.happened.startsWith("The files of"):A.copy.happened.includes("still exists"),`${name}: the text of its route`);
  action("Cancel").click();await wait();
}
check(A.copy.records.some(row=>row.startsWith("Existing folder of records")),"the existing folder row is in the path-change copy");
''', window_size=NARROW), "PASS")

    def test_the_retired_case_collapses_six_rows_and_keeps_the_footer_visible(self) -> None:
        """A list above 5 rows is collapsed behind a toggle; the footer is inside the 936 x 508 window."""
        self.assertEqual(page(r'''
await start(offered("retired"));reply("retired");control().click();await opened();
const writes=section("What adoption writes"),toggle=writes.querySelector(".link-button");
check(A.retired.records.length===6&&toggle.textContent==="6 entries"&&toggle.getAttribute("aria-expanded")==="false","six rows: a collapsed list with its count");
check(writes.querySelector("ul").hidden&&toggle.getAttribute("aria-labelledby").includes(writes.querySelector("[id^=map-preview-label]").id),"hidden, and named by the label");
toggle.click();check(!writes.querySelector("ul").hidden&&lines(writes.querySelector("ul")).join("|")===A.retired.records.join("|"),"the toggle opens the rows");
const body=shown().querySelector(".map-preview-body"),box=footer().getBoundingClientRect();
check(box.bottom<=innerHeight&&box.top>=0&&shown().getBoundingClientRect().bottom<=innerHeight+1,"the footer stays in the window");
check(body.scrollHeight>=body.clientHeight&&getComputedStyle(body).overflowY!=="visible","the body scrolls");
for(const button of shown().querySelectorAll(".action-row button")){const r=button.getBoundingClientRect();check(r.bottom<=innerHeight&&r.right<=innerWidth,`${button.textContent} is visible`);}
action("Cancel").click();await wait();
''', window_size=NARROW), "PASS")

    def test_a_refused_answer_shows_the_refusal_and_close_only(self) -> None:
        """Two sections, the backend text unchanged, Close only, and the control stays for another try."""
        self.assertEqual(page(r'''
await start(offered("copy"));reply("refused");control().click();await opened();
check(JSON.stringify(headings())===JSON.stringify(["Adoption is not possible","What happened"]),`sections: ${headings()}`);
const message=A.refused.refusals[0].message,refusal=section("Adoption is not possible");
check(lines(refusal).join("|")===message&&refusal.querySelector(".notice-error")&&!refusal.querySelector("[role=alert]"),"the refusal text, unchanged, an error notice without role alert");
check(buttons().join()==="Close"&&!shown().querySelector(".button-primary"),"Close only, no confirm");
check(lines(section("What happened")).join("|")===A.refused.happened,"what happened still shows");
action("Close").click();await wait();
check(!shown()&&document.activeElement===control()&&!control().hidden&&control().getAttribute("aria-disabled")!=="true","Close returns the focus; the control stays available");
check(!lane.calls.some(call=>call[0]==="adopt"),"nothing was submitted");
''', window_size=NARROW), "PASS")

    def test_a_stale_preview_shows_preview_again(self) -> None:
        """An operation that ends while the dialog is open makes it stale: the line, Preview again, a new read."""
        self.assertEqual(page(r'''
await start(offered("move"));control().click();await opened();
const confirm=shown().querySelector(".button-primary");confirm.focus();
emit(record("other1","APPLY_MEDICAL_FEATURE","RUNNING"));await poll();emit(record("other1","APPLY_MEDICAL_FEATURE","SUCCEEDED"));await poll();await wait();
check(shown()&&footer().querySelector("[role=status]").textContent===X.stale,"the stale line");
check(buttons().join()==="Cancel,Preview again"&&shown().querySelector(".button-primary").textContent==="Preview again","Preview again in place of Adopt");
check(document.activeElement===shown().querySelector(".button-primary"),"the focus moves because the confirm button had it");
const reads=lane.calls.filter(call=>call[0]==="preview").length;
shown().querySelector(".button-primary").click();await wait();await wait();await opened();
check(lane.calls.filter(call=>call[0]==="preview").length===reads+1&&shown().querySelector(".button-primary").textContent===X.confirm,"Preview again reads again and opens a fresh dialog");
check(footer().querySelector("[role=status]").textContent==="","the fresh dialog is not stale");
action("Cancel").click();await wait();
// A preview that was read before an operation ended opens stale
lane.hold=true;control().click();await wait();emit(record("other2","APPLY_MEDICAL_FEATURE","RUNNING"));await poll();
emit(record("other2","APPLY_MEDICAL_FEATURE","SUCCEEDED"));await poll();lane.release();await opened();
check(footer().querySelector("[role=status]").textContent===X.stale&&buttons().join()==="Cancel,Preview again","an answer older than an end opens stale");
action("Cancel").click();await wait();
''', window_size=NARROW, budget=60000), "PASS")

    def test_the_refused_apply_dialog_offers_adoption_only_for_capture_adoption(self) -> None:
        """A second button before Close for `capture.adoption`; it opens the adoption preview and returns to Apply."""
        self.assertEqual(page(r'''
await start(value(plan()));
lane.previews.apply={success:true,value:A.apply_refused_plain};activate("apply");await opened();
check(buttons().join()==="Close","no capture.adoption: Close only");
action("Close").click();await wait();
lane.previews.apply={success:true,value:A.apply_refused};activate("apply");await opened();
check(buttons().join()===`${X.label},Close`,`the second button before Close: ${buttons()}`);
check(control().hidden,"the status area control stays hidden: the load had no adoption");
reply("capture");action(X.label).click();await wait();await wait();await opened();
check(shown().querySelector("h2").textContent===X.title&&document.querySelectorAll("dialog").length===1,"the Apply dialog closed; the adoption preview opened");
check(lines(section("What happened"))[0]===A.capture.happened,"the capture route text");
action("Cancel").click();await wait();
check(document.activeElement===element("map-apply"),"the focus returns to Apply, the opener");
// Confirm from this route submits the same payload
activate("apply");await opened();action(X.label).click();await wait();await wait();await opened();
shown().querySelector(".button-primary").click();await wait();await wait();
check(JSON.stringify(lane.calls.find(call=>call[0]==="adopt"))===JSON.stringify(["adopt","d".repeat(64)]),"the reviewed fingerprint");
check(own().kind==="ADOPT_MISSION_MAP","an adoption operation");
''', window_size=NARROW, budget=60000), "PASS")


if __name__ == "__main__":
    unittest.main()
