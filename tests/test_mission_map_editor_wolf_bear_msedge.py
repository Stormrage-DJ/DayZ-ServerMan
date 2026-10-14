"""Verify that the Encounter tool and Properties show wolves and bears as unavailable in composed Edge (7.9)."""

from __future__ import annotations

import json
import unittest

from dayz_serverman.domain.mission_map_providers import unverified_territory_capability
from tests.test_mission_map_editor_encounter_edit_msedge import EDIT
from tests.ui_harness_support import EDGE, run_editor_harness


# The backend entry of 7.9, the exact Product Owner text, and wolf and bear encounters as a plan stores them
WOLF_BEAR = EDIT + r'''
const animalText="Wolf and bear territories are not supported in this version; the pack territory mapping is not verified.";
const backend=''' + json.dumps(unverified_territory_capability().to_dict()) + r''';
const withAnimals=(set=providers())=>Object.assign(set,{native_wolf:{...backend},native_bear:{...backend}});
const animal=(id,provider,x,z,extra={})=>encounter(id,x,z,60,{provider,properties:{pack:3},...extra});
const row=(id)=>document.querySelector(`[data-map-object="${id}"]`);
const allTypes="native_infected=Infected|ai_bandits=AI_Bandits (not available)|native_wolf=Wolves (not available)|native_bear=Bears (not available)";
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorWolfBearEdgeTests(unittest.TestCase):
    """Use synthetic plans, the backend wolf and bear entries, fake save answers and isolated headless profiles."""

    def test_type_select_shows_wolves_and_bears_unavailable(self) -> None:
        """Wolves and Bears are two more enabled options; each shows the 7.9 text and places nothing."""
        self.assertEqual(run_editor_harness(WOLF_BEAR + r'''
check(backend.available===false&&backend.reason===animalText,"the backend entry holds the exact text");
await loadWith(plan(),withAnimals());
check(options("map-encounter-provider")===allTypes&&typeSelect().value==="native_infected","two more options after AI_Bandits; none is disabled");
tool("encounter");check(button().getAttribute("aria-pressed")==="true","Infected tool active");
for(const provider of ["native_wolf","native_bear"]){
  chooseType(provider);
  check(button().getAttribute("aria-pressed")==="false"&&text("map-tool-status")==="No tool active"&&text("map-tool-message")===animalText,`${provider}: a change ends the tool and says why`);
  check(text("map-encounter-provider-reason")===animalText&&button().getAttribute("aria-disabled")==="true"&&button().getAttribute("aria-describedby")==="map-encounter-provider-reason",`${provider}: reason line and aria-disabled`);
  document.getElementById("map-announcement").textContent="";document.getElementById("map-tool-message").textContent="";
  const focused=document.getElementById("map-fit");focused.focus();button().click();
  check(text("map-tool-message")===animalText&&text("map-tool-notice")===animalText&&text("map-announcement")===animalText&&document.activeElement===focused,`${provider}: a click says why; the focus stays`);
  press(region,"e");click(500,400);
  check(objects().length===0&&button().getAttribute("aria-pressed")==="false",`${provider}: E chooses nothing; a click places nothing`);
  chooseType("native_infected");tool("encounter");
}
const claims=withAnimals();claims.native_wolf={detected:true,available:true,reason:null,event_names:["X"],evidence:{}};delete claims.native_bear;
await loadWith(plan(),claims);
check(options("map-encounter-provider")===allTypes,"a snapshot that says available, or no entry, keeps both unavailable");
chooseType("native_wolf");check(text("map-encounter-provider-reason")===animalText&&objects().length===0,"Wolves: the 7.9 text without a snapshot reason");
chooseType("native_bear");check(text("map-encounter-provider-reason")===animalText,"Bears: the 7.9 text without a snapshot entry");
tool("encounter");click(500,400);check(objects().length===0,"and nothing is placed");
await loadWith(plan(),withAnimals(providers(infected(false,"territory_not_registered",[]))));
check(typeSelect().value==="native_bear","the choice survives a load again");
'''), "PASS")

    def test_wolf_and_bear_encounters_in_a_plan_show_the_reason(self) -> None:
        """A stored wolf or bear encounter is unavailable with the 7.9 text in the row and Properties; Exclude works."""
        self.assertEqual(run_editor_harness(WOLF_BEAR + r'''
const start=plan();start.objects=[animal("W1","native_wolf",1000,500),animal("B1","native_bear",400,400,{excluded:true})];
await loadWith(start,withAnimals());pick("W1");
check(row("W1").textContent.includes("Encounter (Wolves)")&&row("B1").textContent.includes("Encounter (Bears)")&&!row("W1").textContent.includes("native_wolf"),"rows name the types");
check(row("W1").querySelector(".map-layer-note").textContent===`Unavailable ${animalText}`&&row("W1").querySelector('[data-map-control="W1:select"]').getAttribute("aria-describedby")==="map-layer-note-W1","Unavailable tag and the text in the row");
check(row("B1").querySelector(".map-layer-note").textContent===`Unavailable Excluded ${animalText}`,"an excluded bear shows both tags and the text");
check(mark("W1").dataset.mapState==="unavailable"&&mark("W1").querySelector("[data-map-mark]").dataset.mapMark==="triangle"&&mark("B1").dataset.mapState==="excluded","warning triangle; excluded drawing");
check(text("map-encounter-provider-text")==="Encounter type: Wolves"&&visible("map-encounter-availability")&&text("map-encounter-reason")===animalText,"Properties: the type and the 7.9 text");
check(text("map-encounter-next")==="Before Apply, make this encounter available, change it, or exclude it."&&!field("map-encounter-exclude").checked&&!field("map-encounter-exclude").disabled,"next step and Exclude");
check(visible("map-properties-radius")&&text("map-properties-radius-label")==="Spawn radius (m)"&&!visible("map-encounter-fields"),"spawn radius row; no infected fields");
const length=position();field("map-encounter-exclude").click();
check(find("W1").excluded===true&&position()===length+1&&text("map-encounter-next")==="Excluded: Apply does not write this encounter to the mission."&&text("map-encounter-reason")===animalText,"Exclude is one action; the text stays");
check(row("W1").querySelector(".map-layer-note").textContent===`Unavailable Excluded ${animalText}`&&mark("W1").dataset.mapState==="excluded","both tags; excluded drawing");
await model.undo();check(find("W1").excluded===false&&mark("W1").dataset.mapState==="unavailable","undo includes it again");
type("map-properties-radius","90");check(find("W1").radius===90&&JSON.stringify(find("W1").properties)==='{"pack":3}'&&find("W1").provider==="native_wolf","an edit keeps the provider and its properties");
pick("B1");check(text("map-encounter-provider-text")==="Encounter type: Bears"&&text("map-encounter-reason")===animalText&&field("map-encounter-exclude").checked,"an excluded bear keeps the text and the checked box");
await loadWith(start);pick("W1");check(text("map-encounter-reason")===animalText,"a snapshot without wolf and bear entries gives the same text");
'''), "PASS")


if __name__ == "__main__":
    unittest.main()
