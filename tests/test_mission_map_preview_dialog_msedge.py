"""Verify the Apply preview dialog of the editor with fake answers in composed Edge (8.10).

The answers have the 8.6 to 8.8 shape with the backend texts of the
catalogues. The dialog resolves the fingerprint and the choices, or null, and
submits nothing (Architect 2026-10-15 15:46:42, ruling 6; UI-UX 15:58:12).
"""

from __future__ import annotations

import copy
import json
import unittest

from dayz_serverman.domain.mission_map_families import family_gate
from dayz_serverman.domain.mission_map_preview_texts import (
    CAPTURE_RISK_TEXT, ORDER_CHANGED_TEXT, ZOMBIE_MAX_COUNT_ZERO_TEXT, ZONE_SPAWN_DIST_ZERO_TEXT,
)
from dayz_serverman.domain.mission_map_loot_report_texts import CAPACITY_EXPLANATION, MECHANISM_TEXTS
from dayz_serverman.repositories.mission_map_incorporate import OWNED_VALUE_ABSENT_TEXT, OWNED_VALUE_TEXT
from tests.ui_harness_support import EDGE, run_editor_harness

# A loot object of the answer lists
def item(identifier: str, name: str, kind: str = "loot_circle") -> dict:
    """Return one listed object of the answer."""
    return {"id": identifier, "name": name, "kind": kind, "provider": None}


# One Apply answer with every section: three external changes, two encounter choices and seven added zones
APPLY = {
    "version": 1, "refused": False, "refusals": [], "fingerprint": "f" * 64,
    "capabilities": {"terrain_id": "chernarusplus", "tier_writes": {"available": True, "reason": None},
                     "family": family_gate("chernarusplus").to_dict(),
                     "encounter_providers": {"native_infected": {"available": True, "reason": None,
                                                                 "event_names": ["InfectedVillage"]},
                                             "ai_bandits": {"available": False, "reason": "Bandit text.",
                                                            "event_names": []}}},
    "choices": {"external_changes": ["mapgroupproto.xml", "mapgrouppos.xml", "env/zombie_territories.xml"],
                "unavailable_encounters": ["e1", "e2"]},
    "external_changes": [
        {"path": "mapgroupproto.xml", "exists_now": True, "incorporate": {"available": True, "reason": None,
                                                                          "message": None}},
        {"path": "mapgrouppos.xml", "exists_now": True, "incorporate": {
            "available": False, "reason": "owned_value_changed", "message": OWNED_VALUE_TEXT.format(path="mapgrouppos.xml")}},
        {"path": "env/zombie_territories.xml", "exists_now": False, "incorporate": {
            "available": False, "reason": "owned_value_changed",
            "message": OWNED_VALUE_ABSENT_TEXT.format(path="env/zombie_territories.xml")}}],
    "encounters": {"written": ["w1"], "unavailable": [
        {**item("e1", "Village pack", "encounter"), "provider": "native_infected", "reason": "event_not_offered",
         "excluded": False, "choice_required": True, "linked_loot": True},
        {**item("e2", "Bandit camp", "encounter"), "provider": "ai_bandits", "reason": "Bandit text.",
         "excluded": False, "choice_required": True, "linked_loot": False},
        {**item("x1", "Old camp", "encounter"), "provider": "ai_bandits", "reason": "Bandit text.",
         "excluded": True, "choice_required": False, "linked_loot": False}],
        "excluded": [{**item("x1", "Old camp", "encounter"), "provider": "ai_bandits"}]},
    "objects": {"previous": True, "added": [item(f"a{n}", f"Zone {n}") for n in range(7)], "changed": [],
                "removed": [item("r", "Gone")], "order_changed": True, "order_text": ORDER_CHANGED_TEXT,
                "global": {"current": 25, "applied": -25}},
    "infected": {"notices": [{"code": "zombie_max_count_zero", "text": ZOMBIE_MAX_COUNT_ZERO_TEXT},
                             {"code": "zone_spawn_dist_zero", "text": ZONE_SPAWN_DIST_ZERO_TEXT}]},
    "loot": {"explanation": CAPACITY_EXPLANATION, "unavailable": [],
             "capacity": [{"class": "changed", "source": "Land_House", "group": {"baseline": 3, "realized": 5},
                           "bound_text": None}],
             "mechanisms": {"fixed_dispatch": {"groups": ["Land_House"], "text": MECHANISM_TEXTS["fixed_dispatch"]},
                            "shared_category_capacity": {"groups": [], "text": "unused"},
                            "missing_supply": {"categories": {}, "text": "unused"}, "uncontrolled_placements": []}},
    "affected_files": [{"path": "mapgroupproto.xml"}, {"path": "areaflags.map"}],
    "coordinated_changes": [],
    "other_profiles": [{"profile_id": "bravo", "display_name": "Bravo"}, {"profile_id": "gone", "display_name": None}],
    "capture": {"required": True, "text": CAPTURE_RISK_TEXT},
}

# Shared helpers of the preview harnesses: the opener, the open dialog and its parts
PREVIEW = r'''
const words=window.ServerManMapPreviewWords,frame=window.ServerManMapPreviewDialog,apply=window.ServerManMapPreviewApply;
const settle=()=>new Promise(resolve=>setTimeout(resolve,20));
const opener=document.body.appendChild(Object.assign(document.createElement("button"),{type:"button",textContent:"Opener"}));
const shown=()=>document.querySelector("dialog.map-preview");
const headings=()=>[...shown().querySelectorAll("h3")].map(heading=>heading.textContent);
const section=(name)=>[...shown().querySelectorAll("section")].find(part=>part.querySelector("h3").textContent===name);
const actions=()=>[...shown().querySelectorAll(".action-row button")];
const action=(label)=>actions().find(button=>button.textContent===label);
const radios=()=>[...shown().querySelectorAll('input[type="radio"]')],boxes=()=>[...shown().querySelectorAll('input[type="checkbox"]')];
const visible=(element)=>!element.closest("[hidden]")&&element.getBoundingClientRect().height>0;
const key=(target,name,shiftKey=false)=>{const event=new KeyboardEvent("keydown",{key:name,shiftKey,bubbles:true,cancelable:true});
  target.dispatchEvent(event);return event.defaultPrevented};
const texts=(root)=>[...root.querySelectorAll("p, li, legend, label")].map(element=>element.textContent);
'''

# The minimum window of the UI-UX design is an inner 936 x 508. The headless window size is the outer size: the
# outer 960 x 600 gives that inner size, and the harness measures it (QA-8.16-F2).
NARROW = "960,600"
# Put first in a body with NARROW. It checks the inner size and adds inview(). inview() is true when an element
# is drawn inside the viewport and inside the dialog box.
NARROW_VIEW = r'''
check(innerWidth===936&&innerHeight===508,`the inner window is 936 x 508, not ${innerWidth} x ${innerHeight}`);
const inview=(element)=>{const r=element.getBoundingClientRect(),d=shown().getBoundingClientRect();
  return r.height>0&&r.top>=0&&r.left>=0&&r.bottom<=innerHeight&&r.right<=innerWidth&&r.top>=d.top-1&&r.bottom<=d.bottom+1;};
'''


def harness(body: str, answer: dict | None = None, **options: object) -> str:
    """Run one Apply preview harness with the answer as `answer`."""
    script = PREVIEW + "const answer=" + json.dumps(APPLY if answer is None else answer) + ";\n" + body
    return run_editor_harness(script, budget=int(options.pop("budget", 6000)), **options)


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class ApplyPreviewEdgeTests(unittest.TestCase):
    """Fake answers, the real dialog modules and isolated headless profiles."""

    def test_sections_texts_and_choices_resolve_the_fingerprint_and_the_choices(self) -> None:
        """Section order, unchanged backend texts, no preselection, the gate, and the exact resolved payload."""
        self.assertEqual(harness(r'''
opener.focus();
const objects={e1:{provider:"native_infected",properties:{event_name:"InfectedFoo"}},w1:{name:"Written pack"}};
const pending=apply.open(answer,{opener,objectOf:(id)=>objects[id]??null});await settle();
check(document.activeElement===shown().querySelector("h2")&&shown().querySelector("h2").textContent==="Apply map plan","first focus on the title");
check(JSON.stringify(headings())===JSON.stringify(["Check before you apply","Files changed outside the map editor","Unavailable encounters",
  "Map plan changes","Encounters","Loot report","Mission files"]),`section order: ${headings()}`);
const all=texts(shown());
for(const text of [answer.objects.order_text,answer.external_changes[1].incorporate.message,answer.external_changes[2].incorporate.message,
  answer.capture.text,answer.infected.notices[0].text,answer.infected.notices[1].text,answer.loot.explanation,
  answer.loot.mechanisms.fixed_dispatch.text])check(all.includes(text),`backend text unchanged: ${text}`);
check(!all.includes("unused"),"a mechanism without groups is not shown");
check(all.filter(text=>text===words.replaceNote).length===1,"the Replace the file note shows once");
check(all.includes("Global container capacity: +25% (last Apply: -25%)"),"the global value with the last Apply value");
check(all.includes("Bravo")&&all.includes("gone (deleted profile)")&&all.includes(words.others.apply),"the other profiles, also a deleted one");
check(all.includes("Written pack")&&all.includes("Old camp"),"written and plan-excluded encounters by name");
check(all.includes("This mission does not offer the infected event InfectedFoo.")&&all.includes("Bandit text.")&&all.includes("Its linked loot is still applied."),"7.3 reasons and the linked-loot line");
check(all.includes("Exclude Village pack from this Apply")&&all.includes("Exclude Bandit camp from this Apply"),"checkbox labels");
check(radios().length===6&&radios().every(radio=>!radio.checked)&&boxes().every(box=>!box.checked),"no choice is preselected");
const sets=[...shown().querySelectorAll("fieldset")];
check(sets.map(set=>set.querySelector("legend").textContent).join()===answer.choices.external_changes.join(),"one fieldset per path");
const keep=sets[1].querySelectorAll("input")[0],refusal=sets[1].querySelector(".map-preview-refusal");
check(keep.disabled&&keep.value==="incorporate"&&refusal.textContent===answer.external_changes[1].incorporate.message,"refused Keep the change is unavailable with its text");
check(keep.closest("label").nextElementSibling===refusal&&sets[1].getAttribute("aria-describedby")===refusal.id,"the text is under it and describes the group");
const note=[...shown().querySelectorAll("p")].find(p=>p.textContent===words.replaceNote);
check(sets[0].getAttribute("aria-describedby")===note.id&&!sets[2].getAttribute("aria-describedby").includes(note.id),"the note describes only a present file without a refusal");
const confirm=action("Apply");
check(actions().map(button=>button.textContent).join()==="Cancel,Apply"&&confirm.getAttribute("aria-disabled")==="true","Cancel, then the gated confirm");
const remaining=document.getElementById(confirm.getAttribute("aria-describedby"));
check(remaining.textContent==="5 choices are still needed before you can apply.","the remaining-choices line");
confirm.click();await settle();
check(shown()&&document.activeElement===radios()[0],"activation moves the focus to the first open choice");
radios()[1].click();sets[1].querySelectorAll("input")[1].click();sets[2].querySelectorAll("input")[1].click();boxes()[0].click();
check(remaining.textContent==="1 choice is still needed before you can apply.","one choice left");
confirm.click();await settle();check(document.activeElement===boxes()[1],"the focus goes to the open checkbox");
boxes()[1].click();boxes()[1].click();check(confirm.getAttribute("aria-disabled")==="true","clearing a checkbox needs the choice again");
boxes()[1].click();check(!confirm.hasAttribute("aria-disabled")&&remaining.textContent==="","all choices made");
confirm.click();const value=await pending;
check(JSON.stringify(value)===JSON.stringify({fingerprint:answer.fingerprint,choices:{external_changes:{"mapgroupproto.xml":"replace",
  "mapgrouppos.xml":"replace","env/zombie_territories.xml":"replace"},unavailable_encounters:{e1:"exclude",e2:"exclude"}}}),`payload: ${JSON.stringify(value)}`);
check(!shown()&&document.activeElement===opener,"the dialog is gone and the focus is back on the opener");
'''), "PASS")

    def test_collapsed_lists_and_what_is_never_collapsed(self) -> None:
        """Seven rows collapse with a count; five stay open; loot rows always collapse; choices never do."""
        self.assertEqual(harness(r'''
apply.open(answer,{});await settle();
const changes=section("Map plan changes"),toggle=changes.querySelector(".link-button");
const list=document.getElementById(toggle.getAttribute("aria-controls"));
check(toggle.textContent==="7 entries"&&toggle.getAttribute("aria-expanded")==="false"&&list.hidden,"Added is collapsed with its count");
toggle.click();check(!list.hidden&&toggle.getAttribute("aria-expanded")==="true"&&toggle.textContent==="7 entries","it opens; the label stays");
check(changes.querySelectorAll(".link-button").length===1&&visible([...changes.querySelectorAll("li")].find(li=>li.textContent==="Gone")),"one row stays open");
const loot=[...section("Loot report").querySelectorAll(".link-button")];
check(loot.length===2&&loot.every(toggle=>toggle.textContent==="1 entry"&&document.getElementById(toggle.getAttribute("aria-controls")).hidden),"loot rows are always collapsed");
check(visible(section("Loot report").querySelector("p")),"the report texts stay visible");
check(radios().every(visible)&&boxes().every(visible),"required choices are never collapsed");
check([...shown().querySelectorAll(".notice")].every(visible),"notices are never collapsed");
check(section("Check before you apply").querySelectorAll(".link-button").length===0,"two profiles: no toggle");
'''), "PASS")

    def test_only_replace_the_file_is_left_and_it_is_not_preselected(self) -> None:
        """A refused Keep the change leaves Replace the file unchecked; activation focuses it; no note shows."""
        answer = copy.deepcopy(APPLY)
        answer["choices"] = {"external_changes": ["mapgrouppos.xml"], "unavailable_encounters": []}
        answer["external_changes"] = [APPLY["external_changes"][1]]
        self.assertEqual(harness(r'''
apply.open(answer,{});await settle();
const [keep,replace]=radios(),confirm=action("Apply");
check(keep.disabled&&!replace.disabled&&!keep.checked&&!replace.checked,"only Replace the file is left, unchecked");
check(!texts(shown()).includes(words.replaceNote),"no note beside a refused Keep the change");
check(document.getElementById(confirm.getAttribute("aria-describedby")).textContent==="1 choice is still needed before you can apply.","one choice");
confirm.click();await settle();check(document.activeElement===replace&&!replace.checked&&shown(),"the focus goes to Replace the file; nothing is checked");
replace.click();check(!confirm.hasAttribute("aria-disabled"),"chosen");
''', answer), "PASS")

    def test_no_note_for_a_deleted_file(self) -> None:
        """A deleted file gets no Replace the file note, also when Keep the change is available."""
        answer = copy.deepcopy(APPLY)
        answer["choices"] = {"external_changes": ["mapgroupdirt.xml"], "unavailable_encounters": []}
        answer["external_changes"] = [{"path": "mapgroupdirt.xml", "exists_now": False,
                                       "incorporate": {"available": True, "reason": None, "message": None}}]
        self.assertEqual(harness(r'''
apply.open(answer,{});await settle();
check(!texts(shown()).includes(words.replaceNote)&&!shown().querySelector("fieldset").hasAttribute("aria-describedby"),"no note for a deleted file");
check(texts(shown()).includes(words.externalIntro),"the section intro stays");
action("Cancel").click();await settle();
// A choice path without an external_changes entry refers to no note (UI-UX 17:59:02, change 7)
const mixed={...answer,choices:{external_changes:["mapgroupproto.xml","mapgroupdirt.xml"],unavailable_encounters:[]},
  external_changes:[{path:"mapgroupproto.xml",exists_now:true,incorporate:{available:true,reason:null,message:null}}]};
apply.open(mixed,{});await settle();const sets=[...shown().querySelectorAll("fieldset")];
const note=[...shown().querySelectorAll("p")].find(p=>p.textContent===words.replaceNote);
check(note&&sets[0].getAttribute("aria-describedby")===note.id&&!sets[1].hasAttribute("aria-describedby"),"the note describes only the file that exists now");
''', answer), "PASS")

    def test_a_refused_answer_offers_only_close(self) -> None:
        """Refusals first, the family text once, plain collapsed lists, no control, and Close resolves null."""
        answer = copy.deepcopy(APPLY)
        gate = family_gate("deerisle").to_dict()
        answer.update(refused=True, refusals=[{"reason": "family_unverified", "message": gate["text"]},
                                              {"reason": "records_stale", "message": "Records text."}])
        answer["capabilities"]["family"] = gate
        answer["capabilities"]["tier_writes"] = {"available": False, "reason": "bounds_not_confirmed"}
        self.assertEqual(harness(r'''
opener.focus();const pending=apply.open(answer,{opener,outline:true});await settle();
check(headings()[0]==="Apply is not possible"&&headings()[1]==="Check before you apply","refusals first, then the notices");
check(texts(shown()).filter(text=>text===answer.capabilities.family.text).length===1,"the family text shows once");
check(section("Apply is not possible").querySelectorAll(".notice-error").length===2,"each refusal unchanged as an error note");
check(texts(section("Check before you apply")).slice(0,2).join("|")===[words.bounds,words.outline].join("|"),"the bounds line, then the outline line");
check(radios().length===0&&boxes().length===0,"no choice control");
check(texts(section("Files changed outside the map editor")).includes("mapgrouppos.xml"),"external changes as a plain list");
const lists=[...shown().querySelectorAll(".map-preview-list ul")];
check(lists.length>=6&&lists.every(list=>list.hidden),"every list is collapsed");
check(actions().map(button=>button.textContent).join()==="Close","only Close");
check(!shown().querySelector(".map-preview-footer").textContent.includes("choice"),"no remaining-choices line");
action("Close").click();check(await pending===null&&document.activeElement===opener,"Close resolves null");
// Stale on a refused answer: Preview again follows Close; the focus does not move (UI-UX 17:59:02, change 3)
const again=apply.open(answer,{opener});await settle();action("Close").focus();frame.stale();
check(shown().querySelector('[role="status"]').textContent===words.stale.apply,"the stale line");
check(actions().map(button=>button.textContent).join()==="Close,Preview again"&&action("Preview again").classList.contains("button-primary"),"Close, then Preview again");
check(document.activeElement===action("Close"),"the focus stays on Close");
action("Preview again").click();check(JSON.stringify(await again)==='{"again":true}',"Preview again asks for a new answer");
''', answer), "PASS")


if __name__ == "__main__":
    unittest.main()
