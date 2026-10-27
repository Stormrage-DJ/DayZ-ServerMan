"""Verify the Encounter tool, its type select and the provider reasons in composed Edge (7.3)."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_select_msedge import SELECT
from tests.test_mission_map_editor_tools_msedge import TOOLS
from tests.ui_harness_support import EDGE, run_editor_harness


# Synthetic 7.2 snapshots: infected with two offered events, and AI_Bandits unavailable (customer decision A)
ENCOUNTERS = TOOLS + SELECT + r'''
const banditText="Bandit placement is not available on this terrain: no verified ground height source.";
const infected=(available=true,reason=null,event_names=["InfectedVillage","InfectedCity"])=>({detected:available,available,reason,event_names,evidence:{}});
const providers=(native=infected())=>({native_infected:native,ai_bandits:{detected:false,available:false,reason:banditText,event_names:[],evidence:{}}});
const loadWith=async(document,set=providers())=>{const current=value(document);current.capabilities.encounter_providers=set;await load(current)};
const encounter=(id,x,z,radius,extra={})=>({id,kind:"encounter",name:`Encounter ${id}`,visible:true,locked:false,centre:{x,z},radius,
  provider:"native_infected",properties:{event_name:"InfectedCity",smin:0,smax:0,dmin:3,dmax:5},excluded:false,
  linked_loot:{enabled:false,tier:null,abundance:"rich",radius:null},...extra});
const typeSelect=()=>document.getElementById("map-encounter-provider"),button=()=>document.getElementById("map-tool-encounter");
const chooseType=(provider)=>{typeSelect().value=provider;typeSelect().dispatchEvent(new Event("change",{bubbles:true}))};
const options=(id)=>[...document.getElementById(id).options].map(option=>`${option.value}=${option.textContent}${option.disabled?"!":""}`).join("|");
const mark=(id)=>document.querySelector(`[data-map-encounter="${id}"]`);
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorEncounterToolEdgeTests(unittest.TestCase):
    """Use synthetic plans and capability snapshots, fake save answers and isolated headless profiles."""

    def test_tool_places_infected_defaults_with_the_circle_radius(self) -> None:
        """E chooses the tool; one click places one encounter with the PO defaults; undo removes it."""
        self.assertEqual(run_editor_harness(ENCOUNTERS + r'''
await loadWith(plan());
const order=[...document.querySelectorAll("#map-tool-buttons button")].map(item=>item.id.replace("map-tool-","")).join();
check(order==="select,circle,polygon,encounter,inspect"&&button().textContent==="Encounter"&&button().getAttribute("aria-keyshortcuts")==="E"&&button().title==="Encounter (E)","Encounter between Polygon and Inspect");
check(text("map-encounter-provider-label")==="Encounter type"&&options("map-encounter-provider")==="native_infected=Infected|ai_bandits=AI_Bandits (not available)|native_wolf=Wolves (not available)|native_bear=Bears (not available)","type options; no option is disabled");
check(typeSelect().value==="native_infected"&&text("map-encounter-provider-reason")===""&&button().getAttribute("aria-disabled")==="false"&&!button().hasAttribute("aria-describedby"),"first available type chosen");
press(region,"e");
check(button().getAttribute("aria-pressed")==="true"&&text("map-tool-status")==="Encounter"&&document.getElementById("map-tool-status").title==="Encounter; Infected","E chooses the tool; footer and title");
check(getComputedStyle(button()).boxShadow.includes("inset")&&getComputedStyle(document.getElementById("map-tool-circle")).boxShadow==="none","the pressed tool shows an inset bar");
check(region.classList.contains("map-drawing"),"crosshair while placing");
const history=model.status().history.length;click(500,400);let items=objects();
const first=items[0],pixel=1/view.snapshot().scale;
check(items.length===1&&first.kind==="encounter"&&first.name==="Encounter 1"&&first.provider==="native_infected"&&first.radius===100,"one click, one encounter with the circle tool radius");
check(JSON.stringify(first.properties)===JSON.stringify({event_name:"InfectedCity",smin:0,smax:0,dmin:3,dmax:5}),"first offered event in sorted order, 0, 0, 3, 5");
check(first.excluded===false&&first.visible&&!first.locked&&JSON.stringify(first.linked_loot)===JSON.stringify({enabled:false,tier:null,abundance:"rich",radius:null}),"not excluded; linked loot stored as not enabled");
check(Math.abs(first.centre.x-500)<=pixel&&Math.abs(first.centre.z-400)<=pixel&&model.status().history.length===history+1,"centre at the click; one undo action");
check(mark(first.id).dataset.mapState==="available"&&mark(first.id).querySelector("[data-map-mark]").dataset.mapMark==="cross","drawn with a cross");
check(mark(first.id).querySelector(".map-encounter-ring").getAttribute("stroke-dasharray")==="10 4 2 4"&&mark(first.id).querySelector(".map-encounter-ring").getAttribute("stroke-width")==="3","dash-dot ring of 3 px");
check(getComputedStyle(document.documentElement).getPropertyValue("--map-encounter").trim().toUpperCase()==="#F59BFF","encounter token");
const row=document.querySelector(`[data-map-object="${first.id}"]`);
check(row.textContent.includes("Encounter (Infected)")&&!row.textContent.includes("native_infected")&&!row.querySelector(".map-layer-note"),"list row names the type; no tags");
const radius=document.getElementById("map-circle-radius");radius.value="250";radius.dispatchEvent(new Event("change"));
click(1500,600);check(objects()[0].radius===250&&objects()[0].name==="Encounter 2","the current circle tool radius");
click(-50,500);check(objects().length===2&&text("map-tool-message")==="This point is outside the terrain bounds. Click inside the bounds to place an encounter."&&text("map-tool-notice")===text("map-tool-message"),"outside click places nothing and says why");
await model.undo();check(objects().length===1&&objects()[0].name==="Encounter 1","one undo per encounter");
tool("encounter");check(button().getAttribute("aria-pressed")==="false"&&text("map-tool-status")==="No tool active","a second press of the button ends the tool");
'''), "PASS")

    def test_ai_bandits_type_is_unavailable_and_places_nothing(self) -> None:
        """The AI_Bandits type shows the decision A reason, chooses no tool and ends an active Encounter tool."""
        self.assertEqual(run_editor_harness(ENCOUNTERS + r'''
await loadWith(plan());
tool("encounter");check(button().getAttribute("aria-pressed")==="true","Infected tool active");
chooseType("ai_bandits");
check(button().getAttribute("aria-pressed")==="false"&&text("map-tool-status")==="No tool active"&&text("map-tool-message")===banditText,"a change to AI_Bandits ends the tool and says why");
check(text("map-encounter-provider-reason")===banditText&&button().getAttribute("aria-disabled")==="true"&&button().getAttribute("aria-describedby")==="map-encounter-provider-reason","reason line and aria-disabled");
check(!button().disabled&&[...typeSelect().options].every(option=>!option.disabled),"the button stays focusable; no disabled option");
document.getElementById("map-announcement").textContent="";document.getElementById("map-tool-message").textContent="";
const focused=document.getElementById("map-tool-select");focused.focus();button().click();
check(button().getAttribute("aria-pressed")==="false"&&text("map-tool-message")===banditText&&text("map-tool-notice")===banditText&&text("map-announcement")===banditText,"a click says why in the panel, on the map and in the live region");
check(document.activeElement===focused,"the focus does not move");
press(region,"e");click(500,400);check(objects().length===0&&button().getAttribute("aria-pressed")==="false","E chooses nothing; a map click places nothing");
chooseType("native_infected");check(text("map-encounter-provider-reason")===""&&button().getAttribute("aria-disabled")==="false","back to Infected");
tool("encounter");chooseType("native_infected");check(button().getAttribute("aria-pressed")==="true"&&text("map-tool-message")==="","an available type keeps the tool");
await loadWith(plan());check(typeSelect().value==="native_infected","the choice survives a load again");
check(button().getAttribute("aria-pressed")==="true","the tool stays active through a load");
await loadWith(plan(),providers(infected(false,"territory_not_registered",[])));
check(button().getAttribute("aria-pressed")==="false"&&text("map-tool-status")==="No tool active"&&button().getAttribute("aria-disabled")==="true","a load that makes the type unavailable ends the tool");
click(500,400);check(objects().length===0,"no placement after that load");
const available=providers();available.ai_bandits={detected:true,available:true,reason:null,event_names:["X"],evidence:{}};
await loadWith(plan(),available);chooseType("ai_bandits");
check(options("map-encounter-provider")==="native_infected=Infected|ai_bandits=AI_Bandits (not available)|native_wolf=Wolves (not available)|native_bear=Bears (not available)"&&text("map-encounter-provider-reason")===banditText,"AI_Bandits stays unavailable when a snapshot says available");
tool("encounter");click(500,400);check(objects().length===0&&button().getAttribute("aria-pressed")==="false","and places nothing");
chooseType("native_infected");
chooseType("ai_bandits");await loadWith(plan());check(typeSelect().value==="ai_bandits","the AI_Bandits choice also survives a load again");
'''), "PASS")

    def test_each_infected_reason_text_and_provider_absence(self) -> None:
        """Each 7.2 code has the Product Owner text; an unavailable Infected type places nothing."""
        self.assertEqual(run_editor_harness(ENCOUNTERS + r'''
const start="Infected areas are not available on this mission: ",files="cfgenvironment.xml, env/zombie_territories.xml or db/events.xml";
const cases={territory_not_registered:"cfgenvironment.xml does not register env/zombie_territories.xml.",
  no_matching_events:"no zone name in env/zombie_territories.xml is also an event name in db/events.xml.",
  provider_file_missing:`${files} is missing.`,provider_file_unreadable:`${files} cannot be read.`,
  provider_path_invalid:`${files} is a link or is outside the mission folder.`,
  provider_configuration_invalid:`${files} is not valid XML of the expected form.`,odd_code:"the check gave the unknown result odd_code."};
for(const [code,rest] of Object.entries(cases)){
  await loadWith(plan(),providers(infected(false,code,[])));
  check(options("map-encounter-provider")==="native_infected=Infected (not available)|ai_bandits=AI_Bandits (not available)|native_wolf=Wolves (not available)|native_bear=Bears (not available)",`${code}: option text`);
  check(text("map-encounter-provider-reason")===start+rest,`${code}: reason ${text("map-encounter-provider-reason")}`);
}
press(region,"e");click(500,400);
check(objects().length===0&&text("map-tool-message")===start+cases.odd_code&&text("map-tool-status")==="No tool active","an unavailable Infected type places nothing and says why");
await loadWith(plan(),providers(infected(true,null,[])));
check(text("map-encounter-provider-reason")===start+cases.no_matching_events,"no offered event is unavailable");
const current=value(plan());await load(current);
check(text("map-encounter-provider-reason").startsWith(start)&&objects().length===0,"a load without a provider snapshot offers nothing");
'''), "PASS")

    def test_footer_and_panel_fit_at_both_window_sizes(self) -> None:
        """With the Encounter tool active and an encounter selected, the map keeps its size and nothing scrolls sideways."""
        body = r'''
// A selection reveals the inspector, which in a compact window narrows the map; measure the map with it open
await loadWith(plan());window.ServerManMapWorkspace.open("inspector");await wait();
const size=()=>[region.clientWidth,region.clientHeight].join(),start=size();
press(region,"e");click(500,400);pick(objects()[0].id);await wait();
const footer=document.getElementById("map-tool-status"),panel=document.getElementById("map-inspector-body");
check(size()===start&&footer.scrollHeight<=footer.clientHeight+1&&text("map-tool-status")==="Encounter","the footer stays one line; the map keeps its size");
check(panel.scrollWidth<=panel.clientWidth&&document.documentElement.scrollWidth<=document.documentElement.clientWidth,"no horizontal scroll");
const pair=document.querySelector(".map-encounter-pair");check(getComputedStyle(pair).gridTemplateColumns.split(" ").length===2,"two population columns");
'''
        for window_size in ("936,508", "1256,708"):
            with self.subTest(window_size=window_size):
                self.assertEqual(run_editor_harness(ENCOUNTERS + body, window_size=window_size), "PASS")


if __name__ == "__main__":
    unittest.main()
