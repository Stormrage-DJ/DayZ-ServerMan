"""Verify the Restore preview of both modes with fake 8.8 answers in composed Edge (8.10).

The restore preview shows the 8.8 texts unchanged: refusals, the shared
original, the kept-changes sentence and the backup lines (Product Owner
2026-10-15 07:10:16 in evidence 8.8), and the window lines of 15:57:10 and
16:03:40 in evidence 8.10.
"""

from __future__ import annotations

import copy
import json
import unittest

from dayz_serverman.domain.mission_map_families import family_gate
from dayz_serverman.domain.mission_map_restore_texts import (
    BACKUP_TEXT, DELETED_TEXT, KEPT_CHANGES_TEXT, NO_ORIGINAL_TEXT, SHARED_ORIGINAL_TEXT,
)
from tests.test_mission_map_preview_dialog_msedge import PREVIEW
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

    def test_a_refused_restore_offers_only_close_and_shows_the_family_text_once(self) -> None:
        """No original and an unverified family: both refusals, the family text once, only Close."""
        answer = dict(copy.deepcopy(ORIGINAL), refused=True, original=None, family=family_gate("enoch").to_dict())
        answer["refusals"] = [{"reason": "no_original", "message": NO_ORIGINAL_TEXT},
                              {"reason": "family_unverified", "message": answer["family"]["text"]}]
        self.assertEqual(harness(r'''
const pending=restore.open("original",answer,{});await settle();
check(headings()[0]==="Restore is not possible","refusals first");
check(texts(section("Restore is not possible")).join("|")===answer.refusals.map(item=>item.message).join("|"),"both refusals unchanged, the family text once");
check(actions().map(button=>button.textContent).join()==="Close","only Close");
check([...shown().querySelectorAll(".map-preview-list ul")].every(list=>list.hidden),"every list is collapsed");
frame.stale();check(shown().querySelector('[role="status"]').textContent===words.stale.restore&&actions().length===1,"a stale refused preview keeps Close");
action("Close").click();check(await pending===null,"Close resolves null");
''', answer), "PASS")

    def test_the_stale_restore_preview_offers_preview_again(self) -> None:
        """The restore stale line and Preview again in place of the confirm button."""
        self.assertEqual(harness(r'''
const pending=restore.open("previous",answer,{});await settle();action("Restore previous state").focus();frame.stale();
check(shown().querySelector('[role="status"]').textContent===words.stale.restore,"the restore stale line");
check(actions().map(button=>button.textContent).join()==="Cancel,Preview again"&&document.activeElement===action("Preview again"),"Preview again has the focus");
action("Preview again").click();check(JSON.stringify(await pending)==='{"again":true}',"asks for a new answer");
''', PREVIOUS, window_size="936,508"), "PASS")


if __name__ == "__main__":
    unittest.main()
