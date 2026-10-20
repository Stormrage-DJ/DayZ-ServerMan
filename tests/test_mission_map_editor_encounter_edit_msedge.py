"""Verify encounter Properties, the R11 exclusion, the hit test and the drags in composed Edge (7.3)."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_encounters_msedge import ENCOUNTERS
from tests.ui_harness_support import EDGE, run_editor_harness


# Field helpers of the encounter Properties; a change event commits one value, as a real field does
EDIT = ENCOUNTERS + r'''
const field=(id)=>document.getElementById(id);
const type=(id,typed)=>{field(id).focus();field(id).value=typed;field(id).dispatchEvent(new Event("change",{bubbles:true}))};
const message=()=>text("map-properties-message");
const visible=(id)=>{const element=field(id);return Boolean(element)&&element.offsetParent!==null};
const bandit=(id,x,z)=>encounter(id,x,z,null,{provider:"ai_bandits",properties:{}});
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorEncounterEditEdgeTests(unittest.TestCase):
    """Use synthetic plans and capability snapshots, fake save answers and isolated headless profiles."""

    def test_population_event_labels_refusals_and_undo(self) -> None:
        """The labels and the sentence are exact; refusals keep the stored value; each valid edit is one action."""
        self.assertEqual(run_editor_harness(EDIT + r'''
const start=plan();start.objects=[encounter("E1",1000,500,100)];await loadWith(start);pick("E1");
check(text("map-properties-name")==="Encounter E1 (Encounter)"&&text("map-encounter-provider-text")==="Encounter type: Infected","name and type");
check(text("map-properties-radius-label")==="Spawn radius (m)"&&text("map-encounter-event-label")==="Infected event"&&text("map-encounter-population-legend")==="Infected population","labels");
check(["smin","smax","dmin","dmax"].map(key=>text(`map-encounter-${key}-label`)).join("|")==="Static minimum (smin)|Static maximum (smax)|Dynamic minimum (dmin)|Dynamic maximum (dmax)","population labels");
check(text("map-encounter-population-note")==="DayZ uses these values, the event limits and ZombieMaxCount to spawn infected in this area. DayZ-ServerMan does not promise a number of infected in the area at any time.","the sentence promises no number");
check(options("map-encounter-event")==="InfectedCity=InfectedCity|InfectedVillage=InfectedVillage"&&field("map-encounter-event").value==="InfectedCity","offered events, exact and sorted");
check(field("map-encounter-smin").inputMode==="numeric"&&field("map-encounter-smin").type==="text"&&!visible("map-encounter-availability")&&!visible("map-tier-row"),"text fields; no R11 block and no tier row for an available encounter");
const order=[...document.querySelectorAll("#map-properties-fields input, #map-properties-fields select, #map-properties-fields button")].filter(item=>item.offsetParent!==null).map(item=>item.id).join();
check(order==="map-properties-name-field,map-linked-loot-enable,map-properties-centre-x,map-properties-centre-z,map-properties-radius,map-encounter-event,map-encounter-smin,map-encounter-smax,map-encounter-dmin,map-encounter-dmax,map-properties-delete","focus order; linked loot controls precede geometry without losing provider fields "+order);
const length=entries();
for(const typed of ["101","-1","2.5",""," ","+5"]){type("map-encounter-smax",typed);
  check(message()==="Static maximum (smax) must be a whole number from 0 to 100."&&field("map-encounter-smax").getAttribute("aria-invalid")==="true",`refused ${typed}`);}
type("map-encounter-smin","1");check(message()==="Static minimum (smin) must not be more than static maximum (smax)."&&find("E1").properties.smin===0,"static order refused");
type("map-encounter-dmin","6");check(message()==="Dynamic minimum (dmin) must not be more than dynamic maximum (dmax)."&&find("E1").properties.dmin===3,"dynamic order refused");
type("map-encounter-dmax","2");check(message()==="Dynamic minimum (dmin) must not be more than dynamic maximum (dmax)."&&find("E1").properties.dmax===5,"a maximum below its minimum is refused");
check(entries()===length,"refusals add no action");
type("map-encounter-smax"," 007 ");check(find("E1").properties.smax===7&&field("map-encounter-smax").value==="7"&&message()===""&&field("map-encounter-smax").getAttribute("aria-invalid")==="false"&&entries()===length+1,"a valid value with spaces is one action and shows as stored");
type("map-encounter-smin","7");check(find("E1").properties.smin===7&&message()===""&&entries()===length+2,"an equal minimum and maximum are allowed");
type("map-encounter-dmax","100");check(find("E1").properties.dmax===100&&entries()===length+3,"100 is allowed");
field("map-encounter-event").value="InfectedVillage";field("map-encounter-event").dispatchEvent(new Event("change",{bubbles:true}));
check(find("E1").properties.event_name==="InfectedVillage"&&entries()===length+4,"an event choice is one action");
await model.undo();check(find("E1").properties.event_name==="InfectedCity"&&field("map-encounter-event").value==="InfectedCity","undo restores the event");
await model.undo();await model.undo();await model.undo();check(JSON.stringify(find("E1").properties)===JSON.stringify(start.objects[0].properties),"undo restores the population");
type("map-properties-radius","0");check(message()==="Enter a radius from 1 to 2000 m. The encounter keeps its spawn radius."&&find("E1").radius===100,"encounter radius refusal");
type("map-properties-centre-x","-5");check(message()==="Enter a centre inside the terrain bounds: X from 0 to 2000 m, Z from 0 to 1000 m. The encounter stays where it is."&&find("E1").centre.x===1000,"encounter centre refusal");
type("map-properties-centre-x","1200");type("map-properties-radius","150");check(find("E1").centre.x===1200&&find("E1").radius===150&&message()==="","centre and spawn radius fields");
await model.undo();await model.undo();check(find("E1").centre.x===1000&&find("E1").radius===100,"one undo each");
field("map-properties-delete").click();check(!find("E1")&&text("map-tool-notice")==="Encounter E1 deleted. Undo brings it back.","Delete removes the encounter as one action");
await model.undo();pick("E1");check(Boolean(find("E1")),"undo brings it back");
model.lock("E1");check(field("map-encounter-smin").disabled&&field("map-encounter-event").disabled&&field("map-properties-radius").disabled,"a locked encounter disables its fields");
'''), "PASS")

    def test_not_offered_event_unavailable_state_and_exclusion(self) -> None:
        """A not-offered event stays selected; the R11 block, tags and marks follow; Exclude is one undo action."""
        self.assertEqual(run_editor_harness(EDIT + r'''
const start=plan();start.objects=[encounter("E1",1000,500,100,{properties:{event_name:"InfectedGone",smin:0,smax:0,dmin:3,dmax:5}}),
  encounter("E2",400,400,80,{excluded:true})];await loadWith(start);pick("E1");
check(options("map-encounter-event")==="InfectedGone=InfectedGone (not offered)!|InfectedCity=InfectedCity|InfectedVillage=InfectedVillage"&&field("map-encounter-event").value==="InfectedGone","the stored event stays selected and disabled");
const reason="This mission does not offer the infected event InfectedGone.";
check(visible("map-encounter-availability")&&text("map-encounter-reason")===reason&&text("map-encounter-next")==="Before Apply, make this encounter available, change it, or exclude it.","reason and next step");
check(text("map-encounter-exclude-label")==="Exclude this encounter from Apply"&&!field("map-encounter-exclude").checked&&field("map-encounter-exclude").getAttribute("aria-describedby")==="map-encounter-reason map-encounter-next","Exclude checkbox");
const order=[...document.querySelectorAll("#map-properties-fields input, #map-properties-fields select")].filter(item=>item.offsetParent!==null).map(item=>item.id);
check(order[0]==="map-encounter-exclude"&&order[1]==="map-properties-name-field","Exclude comes before the fields");
const row=(id)=>document.querySelector(`[data-map-object="${id}"]`);
check(row("E1").querySelector(".map-layer-note").textContent===`Unavailable ${reason}`&&row("E1").querySelector('[data-map-control="E1:select"]').getAttribute("aria-describedby").split(/\s+/).includes("map-layer-note-E1"),"Unavailable tag and reason in the row");
check(mark("E1").dataset.mapState==="unavailable"&&mark("E1").querySelector("[data-map-mark]").dataset.mapMark==="triangle","warning triangle");
check(row("E2").querySelector(".map-layer-note").textContent==="Excluded "&&mark("E2").dataset.mapState==="excluded"&&mark("E2").querySelector(".map-encounter-ring").getAttribute("stroke-dasharray")==="2 6"&&mark("E2").querySelector("[data-map-mark]").dataset.mapMark==="not","an excluded encounter: tag, dotted ring and not sign");
check(getComputedStyle(row("E2").querySelector(".map-layer-tag")).borderTopStyle==="solid","the tag is a bordered box");
type("map-encounter-dmax","6");check(find("E1").properties.dmax===6&&find("E1").properties.event_name==="InfectedGone"&&field("map-encounter-event").value==="InfectedGone","a later edit never replaces a not-offered event");
await model.undo();const length=position();field("map-encounter-exclude").click();
check(find("E1").excluded===true&&position()===length+1&&text("map-encounter-next")==="Excluded: Apply does not write this encounter to the mission."&&text("map-encounter-reason")===reason,"Exclude is one action; the note follows");
check(row("E1").querySelector(".map-layer-note").textContent===`Unavailable Excluded ${reason}`&&mark("E1").dataset.mapState==="excluded","both tags; excluded drawing");
await model.undo();check(find("E1").excluded===false&&!field("map-encounter-exclude").checked&&mark("E1").dataset.mapState==="unavailable","undo includes it again");
field("map-encounter-event").value="InfectedCity";field("map-encounter-event").dispatchEvent(new Event("change",{bubbles:true}));
check(find("E1").properties.event_name==="InfectedCity"&&!visible("map-encounter-availability")&&!row("E1").querySelector(".map-layer-note")&&mark("E1").dataset.mapState==="available","an explicit edit makes it available");
pick("E2");const ring=mark("E2").querySelector(".map-zone-selection");
check(getComputedStyle(ring).strokeWidth==="3px"&&!ring.hasAttribute("stroke-dasharray"),"a selected excluded ring is solid and 3 px");
check(visible("map-encounter-availability")&&field("map-encounter-exclude").checked&&text("map-encounter-reason")===""&&text("map-encounter-next")==="Excluded: Apply does not write this encounter to the mission.","an available excluded encounter keeps the checkbox");
field("map-encounter-exclude").click();check(find("E2").excluded===false&&!visible("map-encounter-availability"),"a clear check includes it; the block goes away");
await loadWith(start,providers(infected(false,"territory_not_registered",[])));pick("E2");
check(text("map-encounter-reason")==="Infected areas are not available on this mission: cfgenvironment.xml does not register env/zombie_territories.xml.","provider reason on an encounter");
model.lock("E2");check(field("map-encounter-exclude").disabled,"a locked encounter disables Exclude");
'''), "PASS")

    def test_hit_test_drags_and_an_encounter_without_radius(self) -> None:
        """A click selects an encounter on its line or mark; drags move and resize it as one action each."""
        self.assertEqual(run_editor_harness(EDIT + r'''
const start=plan();start.objects=[circle("Z1",1000,500,380),encounter("E1",1000,500,400),bandit("B1",300,300),
  encounter("H1",1600,300,100,{visible:false})];await loadWith(start);
const scale=view.snapshot().scale,off=(pixels)=>pixels/scale,hit=(x,z)=>window.ServerManMapTools.hit({x,z});
check(400*scale>60,"the ring is large enough for the test");
check(hit(1000+400-off(11),500)==="E1"&&hit(1000+400-off(13),500)==="Z1","within 12 px of the ring line the encounter wins; 13 px reaches the zone");
check(hit(1000+400-off(30),500)==="Z1"&&hit(1000+off(11),500)==="E1"&&hit(1000+off(13),500)==="Z1","inside the ring the zone above it is reached; the centre mark wins");
check(hit(300+off(11),300)==="B1"&&hit(300+off(13),300)===null,"an encounter without a radius: 12 px around its mark");
check(hit(1600,300)===null&&!mark("H1"),"a hidden encounter is never hit or drawn");
check(window.ServerManMapEncounters.hit([encounter("T1",600,700,50),encounter("T2",600,700,50)],{x:650,z:700},view).id==="T1","at equal distance the higher item wins");
check(!mark("B1").querySelector(".map-encounter-ring")&&mark("B1").querySelector("[data-map-mark]").dataset.mapMark==="triangle","no ring without a radius; the mark shows the state");
pick("B1");check(text("map-properties-name")==="Encounter B1 (Encounter)"&&text("map-encounter-provider-text")==="Encounter type: AI_Bandits"&&!visible("map-properties-radius")&&visible("map-properties-centre-x")&&!visible("map-encounter-fields"),"AI_Bandits: centre fields only");
check(text("map-encounter-reason")==="Bandit placement is not available on this terrain: no verified ground height source."&&handles()===0,"decision A reason; no resize handle");
let length=position();drag([300+off(13),300],[350,320]);check(find("B1").centre.x===300&&position()===length,"a press 13 px from its centre does not move it");
drag([300+off(11),300],[350+off(11),320]);check(near(find("B1").centre.x,350)&&near(find("B1").centre.z,320)&&find("B1").radius===null&&position()===length+1,"a press 11 px from its centre moves it");
pick("E1");holdControl(true);check(handles()===1&&mark("E1").classList.contains("map-encounter-selected")&&!mark("E1").querySelector(".map-zone-selection").hasAttribute("stroke-dasharray"),"one east handle; a solid selected ring");
length=position();drag([1100,500],[1150,450]);check(near(find("E1").centre.x,1050)&&near(find("E1").centre.z,450)&&find("E1").radius===400&&position()===length+1,"a press inside the ring moves it; one action");
await model.undo();check(find("E1").centre.x===1000&&find("E1").centre.z===500,"undo returns the move");
length=position();drag([1400-off(11),500],[1450,500],{ctrl:true});check(find("E1").radius>400&&find("E1").centre.x===1000&&position()===length+1,"a press 11 px from the handle resizes; one action");
await model.undo();length=position();drag([1400-off(13),500],[1400-off(13)+50,500]);
check(find("E1").radius===400&&near(find("E1").centre.x,1050)&&position()===length+1,"13 px from the handle moves");
await model.undo();drag([1100,500],[1200,500],{hold:true});key(region,"Escape");gesture("pointerup",at(1200,500));
check(find("E1").centre.x===1000&&position()===length,"Escape cancels the drag without an action");
model.lock("E1");check(handles()===0,"a locked encounter shows no handle");
'''), "PASS")

    def test_read_only_draft_disables_encounter_properties_and_exclude(self) -> None:
        """In a read-only draft every encounter control is disabled, also Exclude (PO decision 3, QA-7.3-F3)."""
        self.assertEqual(run_editor_harness(EDIT + r'''
const start=plan();start.objects=[encounter("E1",1000,500,100,{properties:{event_name:"InfectedGone",smin:0,smax:0,dmin:3,dmax:5}})];
const current=value(start);current.capabilities.encounter_providers=providers();current.read_only=true;current.read_only_reason="invalid_saved_data";
await load(current);pick("E1");
check(visible("map-encounter-availability")&&visible("map-encounter-fields"),"the R11 block and the fields show read only");
check(["map-encounter-exclude","map-encounter-event","map-encounter-smin","map-encounter-smax","map-encounter-dmin","map-encounter-dmax",
  "map-properties-radius","map-properties-centre-x","map-properties-delete"].every(id=>field(id).disabled),"every control is disabled");
check(handles()===0&&button().disabled&&typeSelect().disabled,"no handle, no Encounter tool and no type choice");
'''), "PASS")


    def test_provider_contours_and_linked_interiors_keep_their_identity(self) -> None:
        """Provider colours remain honest while linked interiors follow their loot tier."""
        self.assertEqual(run_editor_harness(EDIT + r'''
const start=plan();start.objects=[encounter("Purple",500,500,100),bandit("Orange",1600,500),
  encounter("Linked",1000,500,150,{linked_loot:{enabled:true,tier:"Tier3",abundance:"rich",radius:null}})];
const current=value(start);current.capabilities.encounter_providers=providers();
current.capabilities.holdable_tiers=["Tier1","Tier2","Tier3","Tier4"];await load(current);
check(getComputedStyle(mark("Purple").querySelector(".map-encounter-ring")).stroke==="rgb(163, 53, 238)","infected contour is purple");
check(getComputedStyle(mark("Orange").querySelector(".map-encounter-mark-line")).stroke==="rgb(255, 128, 0)","recognized unavailable bandit contour is orange");
check(mark("Orange").dataset.mapState==="unavailable"&&mark("Orange").querySelector('[data-map-mark="triangle"]'),"orange does not enable its provider");
check(mark("Purple").querySelector(".map-zone-wash")&&!mark("Linked").querySelector(".map-zone-wash"),"only unlinked encounter adds a provider interior");
const linked=document.querySelector('[data-map-linked="Linked"]');
check(getComputedStyle(linked).stroke==="rgb(30, 255, 0)"&&getComputedStyle(mark("Linked").querySelector(".map-encounter-ring")).stroke==="rgb(163, 53, 238)","linked tier and provider contour stay distinct");
check(saved.length===0&&entries()===0,"palette and rendering change no plan data");
'''), "PASS")

if __name__ == "__main__":
    unittest.main()
