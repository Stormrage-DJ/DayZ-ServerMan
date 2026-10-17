"""Verify the Restore preview of both modes with fake 8.8 answers in composed Edge (8.10).

The restore preview shows the 8.8 texts unchanged: refusals, the shared
original, the kept-changes sentence and the backup lines (Product Owner
2026-10-15 07:10:16 in evidence 8.8), and the window lines of 15:57:10 and
16:03:40 in evidence 8.10. The other-profiles list shows only after a sentence
that explains it (Product Owner 2026-10-15 21:42:25 in evidence 8.16).
"""

from __future__ import annotations

import copy
import json
import unittest

from dayz_serverman.domain.mission_map_families import family_gate
from dayz_serverman.domain.mission_map_restore_texts import (
    BACKUP_TEXT, DELETED_TEXT, KEPT_CHANGES_TEXT, NO_ORIGINAL_TEXT, NO_PREVIOUS_STATE_TEXT, RESTORED_COPY_TEXT,
    SHARED_ORIGINAL_TEXT,
)
from tests.test_mission_map_preview_dialog_msedge import NARROW, NARROW_VIEW, PREVIEW
from tests.ui_harness_support import EDGE, run_editor_harness

# Restore previous state after a medical toggle: six overwritten external changes, one kept, two other profiles
PREVIOUS = {
    "version": 1, "mode": "previous", "refused": False, "refusals": [], "family": None, "original": None,
    "fingerprint": "e" * 64,
    "replaces": {"operation_id": "op1", "operation_kind": "medical", "applied_at": "2026-10-15T08:30:00Z"},
    "affected_files": [{"path": "mapgroupproto.xml"}, {"path": "mapgrouppos.xml"}],
    "external_changes": [
        *[{"path": f"file{n}.xml", "overwritten": True, "backup": True, "text": BACKUP_TEXT.format(path=f"file{n}.xml")}
          for n in range(5)],
        {"path": "mapgroupdirt.xml", "overwritten": True, "backup": False,
         "text": DELETED_TEXT.format(path="mapgroupdirt.xml")},
        {"path": "env/zombie_territories.xml", "overwritten": False, "backup": False, "text": None}],
    "coordinated_changes": [{"path": "mapgroupproto.xml", "feature": "medical_loot_zones", "overwritten": True}],
    "other_profiles": [{"profile_id": "bravo", "display_name": "Bravo"}, {"profile_id": "gone", "display_name": None}],
}
# Restore original on a shared folder with kept changes
ORIGINAL = dict(copy.deepcopy(PREVIOUS), mode="original", family=family_gate("chernarusplus").to_dict(),
                replaces=None, external_changes=[], coordinated_changes=[],
                original={"captured_at": "2026-10-10T12:00:00Z", "shared": True, "text": SHARED_ORIGINAL_TEXT,
                          "kept_changes": KEPT_CHANGES_TEXT})


def harness(body: str, answer: dict, **options: object) -> str:
    """Run one restore harness with the answer as `answer`."""
    script = (PREVIEW + "const restore=window.ServerManMapPreviewRestore,answer=" + json.dumps(answer) + ";\n"
              "const at=(value)=>new Date(value).toLocaleString(undefined,{dateStyle:\"medium\",timeStyle:\"short\"});\n"
              + body)
    return run_editor_harness(script, budget=6000, **options)


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class RestorePreviewEdgeTests(unittest.TestCase):
    """Fake answers, the real dialog modules and isolated headless profiles."""

    def test_restore_previous_state_sections_and_lines(self) -> None:
        """The title, the section order, the change line, the backup lines open, the kept line, and the value."""
        self.assertEqual(harness(r'''
opener.focus();const pending=restore.open("previous",answer,{opener});await settle();
check(shown().querySelector("h2").textContent==="Restore previous state"&&document.activeElement===shown().querySelector("h2"),"title and first focus");
check(JSON.stringify(headings())===JSON.stringify(["Check before you restore","What the restore writes",
  "Outside changes that the restore overwrites","Outside changes that the restore keeps"]),`order: ${headings()}`);
const notices=texts(section("Check before you restore"));
check(notices.join("|")===[words.others.previous,"Bravo","gone (deleted profile)"].join("|"),"the intro line and the profiles");
check(texts(section("What the restore writes"))[0]==="The restore writes the mission files as they were before the last recorded change: "+
  "medical loot zones change, "+at(answer.replaces.applied_at)+".","the change and its time");
const overwritten=section("Outside changes that the restore overwrites"),lines=answer.external_changes.filter(item=>item.overwritten).map(item=>item.text);
check(lines.every(line=>[...overwritten.querySelectorAll("p")].some(p=>p.textContent===line&&visible(p))),"six backup lines, unchanged and never collapsed");
check(texts(overwritten).includes(words.restoreCoordinated)&&texts(overwritten).includes("mapgroupproto.xml"),"the overwritten coordinated change");
check(texts(section("Outside changes that the restore keeps"))[0]==="env/zombie_territories.xml changed outside the map editor. The restore does not change this file.","the kept line");
check(texts(shown().querySelector(".map-preview-footer"))[0]===words.stop.restore,"the restore stop sentence");
check(actions().map(button=>button.textContent).join()==="Cancel,Restore previous state"&&!action("Restore previous state").hasAttribute("aria-disabled"),"no choice to make");
check(radios().length===0&&boxes().length===0,"no choice control");
action("Restore previous state").click();
check(JSON.stringify(await pending)===JSON.stringify({fingerprint:answer.fingerprint,mode:"previous"})&&document.activeElement===opener,"fingerprint and mode");
''', PREVIOUS), "PASS")

    def test_restore_original_on_a_shared_folder_with_kept_changes(self) -> None:
        """The shared-original text without the intro line, the profiles, the kept-changes sentence and the date."""
        self.assertEqual(harness(r'''
const pending=restore.open("original",answer,{});await settle();
check(shown().querySelector("h2").textContent==="Restore original"&&action("Restore original"),"title and confirm label");
const notices=texts(section("Check before you restore"));
check(notices.join("|")===[answer.original.text,"Bravo","gone (deleted profile)",answer.original.kept_changes].join("|"),`shared text, profiles, kept changes: ${notices.join("|")}`);
check(!notices.includes(words.others.previous),"no second profile line");
check(texts(section("What the restore writes"))[0]==="The restore writes the original that DayZ-ServerMan saved on "+at(answer.original.captured_at)+".","the original date");
check(!headings().includes("Outside changes that the restore keeps")&&!headings().includes("Outside changes that the restore overwrites"),"empty sections are not drawn");
shown().dispatchEvent(new Event("cancel",{cancelable:true}));check(await pending===null,"Escape resolves null");
''', ORIGINAL), "PASS")

    def test_restore_original_shows_the_restored_copy_sentence_once_after_the_shared_text(self) -> None:
        """The host text of a profile that keeps a restored copy: the shared text, then the 10.37 sentence."""
        answer = copy.deepcopy(ORIGINAL)
        answer["original"]["text"] = f"{SHARED_ORIGINAL_TEXT} {RESTORED_COPY_TEXT}"
        self.assertEqual(harness(r'''
const pending=restore.open("original",answer,{});await settle();
const notices=texts(section("Check before you restore"));
check(notices[0]===answer.original.text,`shared text and restored copy: ${notices[0]}`);
check(notices.join("|").split(%s).length===2,"the restored-copy sentence shows once");
shown().dispatchEvent(new Event("cancel",{cancelable:true}));check(await pending===null,"Escape resolves null");
''' % json.dumps(RESTORED_COPY_TEXT), answer), "PASS")

    def test_a_refused_restore_offers_only_close_and_shows_the_family_text_once(self) -> None:
        """No original and an unverified family: both refusals, the family text once, only Close."""
        answer = dict(copy.deepcopy(ORIGINAL), refused=True, original=None, family=family_gate("deerisle").to_dict())
        answer["refusals"] = [{"reason": "no_original", "message": NO_ORIGINAL_TEXT},
                              {"reason": "family_unverified", "message": answer["family"]["text"]}]
        self.assertEqual(harness(r'''
const pending=restore.open("original",answer,{});await settle();
check(headings()[0]==="Restore is not possible","refusals first");
check(texts(section("Restore is not possible")).join("|")===answer.refusals.map(item=>item.message).join("|"),"both refusals unchanged, the family text once");
check(actions().map(button=>button.textContent).join()==="Close","only Close");
check([...shown().querySelectorAll(".map-preview-list ul")].every(list=>list.hidden),"every list is collapsed");
action("Close").focus();frame.stale();check(shown().querySelector('[role="status"]').textContent===words.stale.restore,"the stale line");
check(actions().map(button=>button.textContent).join()==="Close,Preview again"&&document.activeElement===action("Close"),"Close, then Preview again; the focus stays");
action("Close").click();check(await pending===null,"Close resolves null");
const again=restore.open("original",answer,{});await settle();frame.stale();action("Preview again").click();
check(JSON.stringify(await again)==='{"again":true}',"Preview again asks for a new answer");
''', answer), "PASS")

    def test_a_refused_restore_original_without_an_original_shows_no_other_profiles(self) -> None:
        """No original on a folder that two other profiles use: only the refusal and Close (QA-8.16-F1).

        Without an original the backend has no target, so the answer has no affected file. A second answer keeps
        the two affected files of the fixture: the profiles stay hidden also then.
        """
        answer = dict(copy.deepcopy(ORIGINAL), refused=True, original=None, affected_files=[],
                      refusals=[{"reason": "no_original", "message": NO_ORIGINAL_TEXT}])
        self.assertEqual(len(answer["other_profiles"]), 2)
        self.assertEqual(harness(r'''
let pending=restore.open("original",answer,{});await settle();
check(JSON.stringify(headings())==='["Restore is not possible"]',`the only heading: ${headings()}`);
check(texts(section("Restore is not possible")).join("|")===answer.refusals[0].message,"the refusal unchanged");
check(!shown().querySelector(".link-button")&&!shown().querySelector(".map-preview-list"),"no collapsed-list toggle and no list");
for(const name of ["Check before you restore","Bravo","(deleted profile)","gone"])check(!shown().textContent.includes(name),`no ${name}`);
check(actions().map(button=>button.textContent).join()==="Close","only Close");
action("Close").click();check(await pending===null,"Close resolves null");
pending=restore.open("original",{...answer,affected_files:[{path:"mapgroupproto.xml"},{path:"mapgrouppos.xml"}]},{});await settle();
check(!headings().includes("Check before you restore")&&!shown().textContent.includes("Bravo")&&!shown().textContent.includes("(deleted profile)"),"no profiles beside an affected-files list");
action("Close").click();await pending;
''', answer), "PASS")

    def test_a_refused_restore_previous_state_keeps_its_intro_line_and_the_profiles(self) -> None:
        """No previous state on a folder that two other profiles use: the intro line, then the collapsed list."""
        answer = dict(copy.deepcopy(PREVIOUS), refused=True, replaces=None, affected_files=[], external_changes=[],
                      coordinated_changes=[],
                      refusals=[{"reason": "no_previous_state", "message": NO_PREVIOUS_STATE_TEXT}])
        self.assertEqual(harness(r'''
const pending=restore.open("previous",answer,{});await settle();
check(JSON.stringify(headings())==='["Restore is not possible","Check before you restore"]',`headings: ${headings()}`);
const notices=section("Check before you restore"),toggle=notices.querySelector(".link-button");
check(texts(notices).join("|")===[words.others.previous,"Bravo","gone (deleted profile)"].join("|"),`intro line, then the profiles: ${texts(notices)}`);
check(notices.querySelector("p").nextElementSibling.contains(toggle)&&toggle.textContent==="2 entries","the list follows the intro line, collapsed");
check(actions().map(button=>button.textContent).join()==="Close","only Close");
action("Close").click();check(await pending===null,"Close resolves null");
''', answer), "PASS")

    def test_the_stale_restore_preview_offers_preview_again(self) -> None:
        """The restore stale line and Preview again in place of the confirm button, in view at an inner 936 x 508."""
        self.assertEqual(harness(NARROW_VIEW + r'''
const pending=restore.open("previous",answer,{});await settle();action("Restore previous state").focus();frame.stale();
check(shown().querySelector('[role="status"]').textContent===words.stale.restore,"the restore stale line");
check(actions().map(button=>button.textContent).join()==="Cancel,Preview again"&&document.activeElement===action("Preview again"),"Preview again has the focus");
check(inview(shown().querySelector('[role="status"]'))&&inview(action("Preview again")),"the stale line and Preview again are in view");
action("Preview again").click();check(JSON.stringify(await pending)==='{"again":true}',"asks for a new answer");
''', PREVIOUS, window_size=NARROW), "PASS")

    def test_restore_focus_wrap_return_and_lists_at_the_minimum_window(self) -> None:
        """At an inner 936 x 508: first focus, Tab wrap, a collapsed list, open backup lines and the focus return."""
        answer = copy.deepcopy(PREVIOUS)
        answer["other_profiles"] = [{"profile_id": f"p{n}", "display_name": f"Profile {n}"} for n in range(7)]
        self.assertEqual(harness(NARROW_VIEW + r'''
opener.focus();let pending=restore.open("previous",answer,{opener});await settle();
const title=shown().querySelector("h2"),body=shown().querySelector(".map-preview-body"),confirm=action("Restore previous state");
check(document.activeElement===title&&inview(title),"the first focus is on the title, in view");
check(key(title,"Tab",true)&&document.activeElement===confirm,"Shift+Tab on the title goes to the confirm button");
check(key(confirm,"Tab")&&document.activeElement===body,"Tab on the confirm button wraps to the body");
check(key(body,"Tab",true)&&document.activeElement===confirm,"Shift+Tab on the body wraps to the confirm button");
const toggle=section("Check before you restore").querySelector(".link-button"),list=document.getElementById(toggle.getAttribute("aria-controls"));
check(toggle.textContent==="7 entries"&&list.hidden,"seven profiles start collapsed");
const lines=[...section("Outside changes that the restore overwrites").querySelectorAll(":scope > p")];
check(lines.length===6&&lines.every(line=>!line.closest("[hidden]")),"six backup lines, never collapsed");
toggle.click();check(!list.hidden&&body.scrollHeight>body.clientHeight&&actions().every(inview),"the list opens; the body scrolls; the buttons stay in view");
confirm.focus();frame.stale();
const again=action("Preview again");
check(inview(shown().querySelector('[role="status"]'))&&inview(again)&&document.activeElement===again,"stale: the line and Preview again in view, with the focus");
check(key(again,"Tab")&&document.activeElement===body&&key(body,"Tab",true)&&document.activeElement===again,"stale: Tab wraps over Preview again");
action("Cancel").click();check(await pending===null&&document.activeElement===opener,"Cancel returns the focus to the opener");
pending=restore.open("previous",answer,{opener});await settle();opener.remove();
shown().dispatchEvent(new Event("cancel",{cancelable:true}));
check(await pending===null&&document.activeElement===document.getElementById("map-viewport"),"Escape with a gone opener gives the focus to the map");
''', answer, window_size=NARROW), "PASS")


if __name__ == "__main__":
    unittest.main()
