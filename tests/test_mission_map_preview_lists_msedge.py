"""Verify the lists, list names and locked-choice styles of the Apply preview in composed Edge (8.10 rework).

Sources in evidence 8.10:
- UI-UX Designer 2026-10-15 17:59:02, required changes 1, 2, 4, 5, 6 and 8;
- Product Owner 2026-10-15 18:04:08: the loot report labels, the kept rows and
  the lists after the mechanism texts;
- Architect 2026-10-15 18:04:57, ruling A-1: the Excluded list comes from
  `encounters.excluded` alone.
"""

from __future__ import annotations

import copy
import json
import unittest

from dayz_serverman.domain.mission_map_loot_report_texts import (
    BOUND_TEXTS, CAPACITY_EXPLANATION, KEPT_TEXTS, MECHANISM_TEXTS, UNAVAILABLE_TEXTS,
)
from tests.test_mission_map_preview_dialog_msedge import APPLY, harness, item
from tests.ui_harness_support import EDGE, FRONTEND

# A loot report with every list: three mechanisms, two unavailable objects, two changed groups and repeated kept pairs
LOOT = {
    "explanation": CAPACITY_EXPLANATION,
    "unavailable": [{"id": "z1", "name": "North zone", "reason": "bounds_not_confirmed",
                     "text": UNAVAILABLE_TEXTS["bounds_not_confirmed"]},
                    {"id": "z2", "name": None, "reason": "limits_missing", "text": UNAVAILABLE_TEXTS["limits_missing"]}],
    "capacity": [{"class": "changed", "source": "Land_House", "group": {"baseline": 3, "realized": 5},
                  "bound_text": BOUND_TEXTS["qualified"]},
                 {"class": "changed", "source": "Land_Shed", "group": {"baseline": 2, "realized": 4}, "bound_text": None},
                 {"class": "no_change", "source": "Land_Barn", "group": {"baseline": 1, "realized": 1},
                  "bound_text": None}],
    "kept": [{"name": "Land_A", "reason": "planner_content", "text": KEPT_TEXTS["planner_content"]},
             {"name": "Land_A", "reason": "cluster_placement", "text": KEPT_TEXTS["cluster_placement"]},
             {"name": "Land_A", "reason": "planner_content", "text": KEPT_TEXTS["planner_content"]},
             {"name": "Land_B", "reason": "planner_content", "text": KEPT_TEXTS["planner_content"]}],
    "mechanisms": {
        "fixed_dispatch": {"groups": ["Land_House", "Land_Shed"], "text": MECHANISM_TEXTS["fixed_dispatch"]},
        "shared_category_capacity": {"groups": [{"source": "Land_House", "categories": ["tools", "food"]}],
                                     "text": MECHANISM_TEXTS["shared_category_capacity"]},
        "missing_supply": {"categories": {"food": ["Land_House"], "tools": ["Land_House"]},
                           "text": MECHANISM_TEXTS["missing_supply"]},
        "uncontrolled_placements": [{"reason": "cluster_placement", "count": 1, "text": KEPT_TEXTS["cluster_placement"]},
                                    {"reason": "planner_content", "count": 3, "text": KEPT_TEXTS["planner_content"]}]},
}

# Helpers: the rows and the toggle name of a list box, and the list boxes of the loot report
LISTS = r'''
const rows=(box)=>[...box.querySelectorAll("li")].map(li=>li.textContent);
const named=(toggle)=>toggle.getAttribute("aria-labelledby").split(" ").map(id=>document.getElementById(id).textContent).join(" ");
const boxes_=(name)=>[...section(name).querySelectorAll(".map-preview-list")];
const labelled=(name,label)=>boxes_(name).find(box=>box.firstElementChild.tagName==="P"&&box.firstElementChild.textContent===label);
'''


def run(body: str, answer: dict | None = None, **options: object) -> str:
    """Run one harness with the list helpers."""
    return harness(LISTS + body, answer, **options)


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class PreviewListEdgeTests(unittest.TestCase):
    """Fake answers, the real dialog modules and isolated headless profiles."""

    def test_the_loot_report_lists_and_their_labels(self) -> None:
        """Mechanism texts with an unlabelled list each, then three labelled lists; kept pairs once."""
        answer = dict(copy.deepcopy(APPLY), loot=LOOT)
        self.assertEqual(run(r'''
apply.open(answer,{});await settle();
const loot=section("Loot report"),inspect=window.ServerManMapInspectWording,mechanisms=answer.loot.mechanisms;
const lines=[...loot.querySelectorAll(":scope > p")].map(p=>p.textContent);
check(JSON.stringify(lines)===JSON.stringify([answer.loot.explanation,mechanisms.fixed_dispatch.text,
  mechanisms.shared_category_capacity.text,mechanisms.missing_supply.text]),`report texts: ${lines.join("|")}`);
// Each mechanism text is followed by its unlabelled list, always collapsed and named by the text
const expected=[["Land_House","Land_Shed"],["Land_House: tools, food"],["food","tools"]];
[...loot.querySelectorAll(":scope > p")].slice(1).forEach((text,index)=>{
  const box=text.nextElementSibling,toggle=box.querySelector(".link-button");
  check(box.classList.contains("map-preview-list")&&box.firstElementChild===toggle,`an unlabelled list after ${text.textContent}`);
  check(JSON.stringify(rows(box))===JSON.stringify(expected[index]),`rows: ${rows(box)}`);
  check(box.querySelector("ul").hidden&&named(toggle)===`${text.textContent} ${toggle.textContent}`,`collapsed and named: ${named(toggle)}`);});
const unavailable=labelled("Loot report",words.lootUnavailable),capacity=labelled("Loot report",words.lootCapacity);
const kept=labelled("Loot report",words.lootKept);
const last=boxes_("Loot report").slice(3);
check(last.length===3&&last.every((box,index)=>box===[unavailable,capacity,kept][index]),"the three labelled lists in order, last");
check(JSON.stringify(rows(unavailable))===JSON.stringify(["North zone: "+answer.loot.unavailable[0].text,"z2: "+answer.loot.unavailable[1].text]),`unavailable rows: ${rows(unavailable)}`);
const house=answer.loot.capacity[0],shed=answer.loot.capacity[1];
check(JSON.stringify(rows(capacity))===JSON.stringify([`Land_House: ${inspect.groupCapacity(house.group)} ${house.bound_text}`,
  `Land_Shed: ${inspect.groupCapacity(shed.group)}`]),`capacity rows: ${rows(capacity)}`);
check(rows(capacity)[1]==="Land_Shed: Group capacity: 2 now, 4 with this plan.","the 4.6 group capacity sentence");
const keptTexts=answer.loot.kept.map(entry=>entry.text);
check(JSON.stringify(rows(kept))===JSON.stringify([`Land_A: ${keptTexts[0]}`,`Land_A: ${keptTexts[1]}`,`Land_B: ${keptTexts[3]}`]),`kept rows: ${rows(kept)}`);
check(![...loot.querySelectorAll("p")].some(p=>keptTexts.includes(p.textContent)),"no kept text as a separate line");
for(const [box,label,count] of [[unavailable,words.lootUnavailable,"2 entries"],[capacity,words.lootCapacity,"2 entries"],[kept,words.lootKept,"3 entries"]]){
  const toggle=box.querySelector(".link-button");
  check(box.querySelector("ul").hidden&&toggle.textContent===count&&named(toggle)===`${label} ${count}`,`${label}: ${named(toggle)}`);}
''', answer), "PASS")
        source = (FRONTEND / "map_editor_preview_apply.js").read_text(encoding="utf-8")
        self.assertNotIn("Group capacity", source, "the sentence comes from the shared Inspect function")

    def test_unknown_and_absent_supply_and_an_empty_report(self) -> None:
        """The unknown-supply text has no list; empty groups and categories show neither text nor list."""
        loot = copy.deepcopy(LOOT)
        loot["mechanisms"]["missing_supply"] = {"categories": None, "text": MECHANISM_TEXTS["missing_supply_unknown"]}
        loot["mechanisms"]["shared_category_capacity"]["groups"] = []
        loot.update(unavailable=[], kept=[])
        empty = copy.deepcopy(loot)
        empty["mechanisms"]["fixed_dispatch"]["groups"] = []
        empty["mechanisms"]["missing_supply"] = {"categories": {}, "text": MECHANISM_TEXTS["missing_supply"]}
        self.assertEqual(run(r'''
const empty=''' + json.dumps(empty) + r''';
apply.open(answer,{});await settle();
const loot=section("Loot report"),lines=[...loot.querySelectorAll(":scope > p")];
check(JSON.stringify(lines.map(p=>p.textContent))===JSON.stringify([answer.loot.explanation,answer.loot.mechanisms.fixed_dispatch.text,
  answer.loot.mechanisms.missing_supply.text]),`texts: ${lines.map(p=>p.textContent).join("|")}`);
check(lines[2].nextElementSibling.firstElementChild.textContent===words.lootCapacity,"no list after the unknown-supply text");
check(boxes_("Loot report").length===2&&!labelled("Loot report",words.lootUnavailable)&&!labelled("Loot report",words.lootKept),"empty lists are not drawn");
action("Cancel").click();await settle();
apply.open({...answer,loot:empty},{});await settle();
check([...section("Loot report").querySelectorAll(":scope > p")].map(p=>p.textContent).join("|")===answer.loot.explanation,"only the explanation and the capacity list");
''', dict(copy.deepcopy(APPLY), loot=loot)), "PASS")

    def test_the_excluded_list_comes_from_the_answer_alone(self) -> None:
        """Both plan-excluded encounters by their answer names; the encounter that needs a choice is not listed."""
        answer = copy.deepcopy(APPLY)
        quiet, camp = {**item("x0", "Quiet pack", "encounter"), "provider": "native_infected"}, APPLY["encounters"]["excluded"][0]
        answer["encounters"]["excluded"] = [quiet, camp]
        answer["encounters"]["unavailable"] = APPLY["encounters"]["unavailable"][1:]
        answer["choices"]["unavailable_encounters"] = ["e2"]
        self.assertEqual(run(r'''
apply.open(answer,{objectOf:()=>null});await settle();
const box=labelled("Encounters",words.excluded);
check(box&&JSON.stringify(rows(box))==='["Quiet pack","Old camp"]',`excluded rows: ${box&&rows(box)}`);
check(!rows(box).includes("Bandit camp")&&texts(shown()).includes("Exclude Bandit camp from this Apply"),"a choice is not a plan exclusion");
action("Cancel").click();await settle();
apply.open({...answer,encounters:{...answer.encounters,excluded:[]}},{objectOf:()=>null});await settle();
check(!texts(shown()).includes(words.excluded),"an empty list draws no Excluded list");
''', answer), "PASS")

    def test_each_toggle_is_named_by_its_label_or_its_section(self) -> None:
        """A labelled list by its label, an unlabelled one by its section heading, and then its count."""
        answer = copy.deepcopy(APPLY)
        answer["other_profiles"] = [{"profile_id": f"p{n}", "display_name": f"Profile {n}"} for n in range(6)]
        self.assertEqual(run(r'''
apply.open(answer,{});await settle();
check(named(section("Map plan changes").querySelector(".link-button"))==="Added 7 entries","Added, then the count");
check(named(section("Check before you apply").querySelector(".link-button"))==="Check before you apply 6 entries","the section heading, then the count");
check([...shown().querySelectorAll(".link-button")].every(toggle=>toggle.getAttribute("aria-labelledby").endsWith(toggle.id)),"each name ends with the count");
action("Cancel").click();await settle();
apply.open({...answer,refused:true,refusals:[{reason:"records_stale",message:"Records text."}]},{});await settle();
check(named(section("Files changed outside the map editor").querySelector(".link-button"))==="Files changed outside the map editor 3 entries","a refused plain list");
''', answer), "PASS")

    def test_the_title_the_body_outline_and_the_locked_choice_look(self) -> None:
        """No title outline; an inner body outline; a disabled choice and its label look locked, also in forced colours."""
        self.assertEqual(run(r'''
apply.open(answer,{});await settle();
const title=shown().querySelector("h2"),body=shown().querySelector(".map-preview-body");
check(document.activeElement===title&&getComputedStyle(title).outlineStyle==="none","the title shows no outline");
const rules=[...document.styleSheets].flatMap(sheet=>[...sheet.cssRules]);
const rule=rules.find(item=>item.selectorText===".map-preview-body:focus-visible");
check(rule&&rule.style.outlineOffset==="-3px","the body outline is drawn inside");
const forced=rules.filter(item=>item.conditionText==="(forced-colors: active)").flatMap(item=>[...item.cssRules])
  .find(item=>item.selectorText===".map-preview .check-row:has(input:disabled)");
check(forced&&forced.style.color==="graytext","GrayText in forced colours");
const muted=getComputedStyle(document.documentElement).getPropertyValue("--color-text-muted").trim();
const probe=document.body.appendChild(Object.assign(document.createElement("span"),{}));probe.style.color=muted;
const [open,locked]=[radios()[0],radios()[2]],refusal=locked.closest("fieldset").querySelector(".map-preview-refusal");
check(locked.disabled&&getComputedStyle(locked).opacity==="0.65"&&getComputedStyle(locked).cursor==="not-allowed","a disabled radio is dimmed");
check(getComputedStyle(locked.closest("label")).color===getComputedStyle(probe).color,"its label is muted");
check(getComputedStyle(open).opacity==="1"&&getComputedStyle(open.closest("label")).color!==getComputedStyle(probe).color,"an enabled radio is not");
check(getComputedStyle(refusal).opacity==="1"&&getComputedStyle(refusal).color===getComputedStyle(document.getElementById("map-family-reason")).color,"the refusal text keeps the warning colour");
frame.stale();check(boxes().every(box=>getComputedStyle(box).opacity==="0.65"&&getComputedStyle(box.closest("label")).color===getComputedStyle(probe).color),"stale checkboxes look locked");
'''), "PASS")


if __name__ == "__main__":
    unittest.main()
