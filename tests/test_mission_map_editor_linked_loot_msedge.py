"""Verify encounter linked loot in composed Edge: start values, presets, sync, Detach, Delete and the excluded note (7.4)."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_encounter_edit_msedge import EDIT
from tests.test_mission_map_editor_inspect_msedge import POINT
from tests.test_mission_map_editor_select_msedge import SELECT
from tests.test_mission_map_editor_tier_read_msedge import INSPECT
from tests.test_mission_map_editor_tools_msedge import TOOLS
from tests.ui_harness_support import EDGE, run_editor_harness


# A fake backend evaluation that counts enabled linked loot as a loot object (D9); tiers that the layer can hold
LINKED = EDIT + r'''
const L=window.ServerManMapLinkedLoot,percent={sparse:-25,normal:0,rich:50,very_rich:100};
const lootOf=(item)=>item.kind==="encounter"?(item.linked_loot.enabled?item.linked_loot:null):item;
const evaluate=(document,revision)=>({revision,global:document.global_abundance,objects:document.objects.map(item=>{const loot=lootOf(item),zone=loot?percent[loot.abundance]:0;
  return {id:item.id,available:true,reason:null,zone_adjustment:zone,effective_adjustment:document.global_abundance+zone,
    tier:loot?loot.tier:null,tier_writable:true,abundance_only:Boolean(loot)&&loot.tier===null}})});
window.pywebview.api.save_plan=async(document,revision,selection)=>{saved.push({plan:structuredClone(document),revision,selection});
  const answer=success(revision===null?0:revision+1);answer.value.evaluation=evaluate(document,answer.value.revision);return answer};
const loaded=(document,extra={})=>{const current=value(document);Object.assign(current.capabilities,{encounter_providers:providers(),
  holdable_tiers:["Tier1","Tier2","Tier3"],tier_writes:{available:true,reason:null}});return {...current,evaluation:evaluate(document,4),...extra}};
const linkedLoot=(id,x,z,radius,loot={},extra={})=>encounter(id,x,z,radius,{linked_loot:{enabled:true,tier:null,abundance:"rich",radius:null,...loot},...extra});
const ring=(id)=>document.querySelector(`[data-map-linked="${id}"]`),zoneLine=(id)=>document.querySelector(`[data-map-zone="${id}"]`);
const choose=(id,choice)=>{field(id).value=choice;field(id).dispatchEvent(new Event("change",{bubbles:true}))};
const sleep=(ms)=>new Promise(resolve=>setTimeout(resolve,ms));
const refusal=(run)=>{try{run();return null}catch(error){return error.message}};
const screen=(x,z)=>view.toScreen({x,z});
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorLinkedLootEdgeTests(unittest.TestCase):
    """Use synthetic plans, capability snapshots, fake save answers and isolated headless profiles."""

    def test_enable_starts_rich_inherit_with_presets_and_tier(self) -> None:
        """Enabling is one action and starts as Rich and Inherit; the Zone capacity and tier rows edit the linked loot."""
        self.assertEqual(run_editor_harness(LINKED + r'''
const start=plan();start.objects=[encounter("E1",1000,500,100,{linked_loot:{enabled:false,tier:"Tier2",abundance:"sparse",radius:null}}),
  bandit("B1",300,300)];await load(loaded(start));pick("E1");
check(!visible("map-tier-row")&&!visible("map-zone-capacity-row")&&!ring("E1"),"disabled linked loot: no tier row, no Zone capacity and no loot line");
const length=entries();
check(L.set(model,"E1",true)===true&&JSON.stringify(find("E1").linked_loot)===JSON.stringify({enabled:true,tier:null,abundance:"rich",radius:null})&&entries()===length+1,"enabling starts as Rich and Inherit; one action");
check(visible("map-tier-row")&&visible("map-zone-capacity-row")&&field("map-zone-capacity").value==="rich"&&field("map-tier-select").value===""&&!field("map-zone-capacity").disabled&&!field("map-tier-select").disabled,"tier and Zone capacity rows show the linked loot");
const order=[...document.querySelectorAll("#map-properties-fields input, #map-properties-fields select, #map-properties-fields button")].filter(item=>item.offsetParent!==null).map(item=>item.id);
check(order.indexOf("map-linked-loot-enable")<order.indexOf("map-tier-select")&&order.indexOf("map-tier-select")<order.indexOf("map-zone-capacity")&&order.indexOf("map-zone-capacity")<order.indexOf("map-properties-centre-x")&&order.indexOf("map-encounter-dmax")<order.indexOf("map-properties-delete"),"linked loot settings precede geometry and retain all provider fields "+order);
check(text("map-list").includes("Encounter (Infected); Tier: Inherit; Zone capacity: Rich +50%")&&ring("E1").classList.contains("map-zone-inherit"),"the row and the map show the linked loot");
choose("map-zone-capacity","very_rich");check(find("E1").linked_loot.abundance==="very_rich"&&find("E1").abundance===undefined&&entries()===length+2,"a preset choice is one action on the linked loot");
check(text("map-zone-capacity-effective")==="Capacity change: Updating","the effective text waits for the backend");
await sleep(900);check(saved.at(-1).plan.objects[0].linked_loot.abundance==="very_rich"&&text("map-zone-capacity-effective")==="Capacity change: Very Rich +100%; with global 0%: +100%","the save answer gives the effective text");
choose("map-tier-select","Tier2");check(find("E1").linked_loot.tier==="Tier2"&&find("E1").tier===undefined&&entries()===length+3,"a tier choice is one action on the linked loot");
check(ring("E1").classList.contains("map-zone-tier")&&ring("E1").hasAttribute("stroke")&&text("map-list").includes("Tier: Tier2; Zone capacity: Very Rich +100%"),"the tier colour and the row follow");
check(L.set(model,"E1",false)===true&&JSON.stringify(find("E1").linked_loot)===JSON.stringify({enabled:false,tier:"Tier2",abundance:"very_rich",radius:null}),"disabling keeps the stored fields");
check(!visible("map-tier-row")&&!visible("map-zone-capacity-row")&&!ring("E1")&&!text("map-list").includes("Zone capacity"),"disabled: rows, line and row summary go");
L.set(model,"E1",true);check(find("E1").linked_loot.tier===null&&find("E1").linked_loot.abundance==="rich","enabling again starts as Rich and Inherit");
await model.undo();await model.undo();check(JSON.stringify(find("E1").linked_loot)===JSON.stringify({enabled:true,tier:"Tier2",abundance:"very_rich",radius:null}),"undo returns each step");
await model.undo();await model.undo();await model.undo();check(JSON.stringify(find("E1"))===JSON.stringify(start.objects[0])&&position()===0,"five undos reach the start");
check(refusal(()=>L.set(model,"B1",true))==="This draft action is not valid."&&!find("B1").linked_loot.enabled,"no linked loot for a provider without a spawn radius (D6 not built)");
model.lock("E1");check(refusal(()=>L.set(model,"E1",true))==="Unlock the object before editing its properties or geometry."&&!find("E1").linked_loot.enabled,"a locked encounter refuses");
const readOnly=loaded(start,{read_only:true,read_only_reason:"mission_path_changed"});await load(readOnly);
check(L.set(model,"E1",true)===false&&!find("E1").linked_loot.enabled,"a read-only draft refuses");
'''), "PASS")

    def test_move_resize_and_fields_keep_linked_loot_on_the_encounter(self) -> None:
        """The loot line is the spawn circle under the ring; drags and fields move both; the hit test uses list position."""
        self.assertEqual(run_editor_harness(LINKED + r'''
const start=plan();start.objects=[linkedLoot("E1",1000,500,300),circle("Z1",1000,500,250)];await load(loaded(start));
const scale=view.snapshot().scale,off=(pixels)=>pixels/scale,hit=(x,z)=>window.ServerManMapTools.hit({x,z});
const same=(id)=>{const item=find(id),centre=screen(item.centre.x,item.centre.z),line=ring(id);
  return Math.abs(Number(line.getAttribute("cx"))-centre.x)<0.01&&Math.abs(Number(line.getAttribute("cy"))-centre.y)<0.01&&Math.abs(Number(line.getAttribute("r"))-item.radius*scale)<0.01};
check(300*scale>60&&same("E1"),"the loot line is the spawn circle");
const layer=()=>[...document.querySelectorAll("#map-zones *")];
check(layer().indexOf(zoneLine("Z1"))<layer().indexOf(ring("E1"))&&layer().indexOf(ring("E1"))<layer().indexOf(mark("E1")),"above Z1 by list position, under the encounter ring");
check(hit(1000+off(40),500)==="E1","inside the ring the linked loot is reached at its list position");
model.move("E1",1);check(objects()[0].id==="Z1"&&hit(1000+off(40),500)==="Z1","a zone above it wins");
await model.undo();L.set(model,"E1",false);check(hit(1000+off(40),500)==="Z1","disabled loot takes no part in the zone step");
await model.undo();check(find("E1").linked_loot.enabled&&hit(1000+off(40),500)==="E1","undo enables it again");
model.visible("E1");check(!ring("E1")&&!mark("E1")&&hit(1000+off(40),500)==="Z1","a hidden encounter draws no loot line and is not hit");
await model.undo();check(ring("E1")&&hit(1000+off(40),500)==="E1","shown again");
pick("E1");check(!ring("E1").classList.contains("map-zone-selected")&&mark("E1").classList.contains("map-encounter-selected"),"the selection cue is the encounter ring");
const length=position();const end=drag([1000+off(40),500],[1100+off(40),450],{hold:true});
const preview=screen(1100,450);check(Math.abs(Number(ring("E1").getAttribute("cx"))-preview.x)<=2&&Math.abs(Number(ring("E1").getAttribute("cy"))-preview.y)<=2,"the loot line follows the drag preview");
release(end,7);check(near(find("E1").centre.x,1100)&&near(find("E1").centre.z,450)&&find("E1").linked_loot.radius===null&&same("E1")&&position()===length+1,"a move is one action; the loot keeps the encounter centre");
drag([1100+300-off(5),450],[1100+360,450],{ctrl:true});check(find("E1").radius>340&&same("E1")&&position()===length+2,"a resize is one action; the loot keeps the spawn radius");
type("map-properties-centre-x","800");type("map-properties-radius","120");check(find("E1").centre.x===800&&find("E1").radius===120&&same("E1")&&position()===length+4,"the centre and spawn radius fields move and resize both");
for(let index=0;index<4;index++)await model.undo();
check(JSON.stringify(find("E1"))===JSON.stringify(start.objects[0])&&same("E1"),"undo returns both together");
'''), "PASS")

    def test_detach_inserts_a_circle_directly_above_the_encounter(self) -> None:
        """Detach is one action: an independent circle above the encounter with its centre, radius, tier and preset."""
        self.assertEqual(run_editor_harness(LINKED + r'''
const start=plan();start.objects=[circle("Z0",300,300,50),linkedLoot("E1",1000,500,200,{tier:"Tier2",abundance:"very_rich"}),
  circle("Z2",1000,500,400),encounter("E3",1500,500,100)];await load(loaded(start));pick("E1");
const scale=view.snapshot().scale,off=(pixels)=>pixels/scale,before=JSON.stringify(objects()),length=entries();
const id=L.detach(model,"E1"),made=find(id);
check(objects().map(item=>item.id).join()===`Z0,${id},E1,Z2,E3`&&entries()===length+1,"the circle enters directly above the encounter; one action");
check(made.kind==="loot_circle"&&made.name==="Loot circle 1"&&made.visible&&!made.locked&&made.centre.x===1000&&made.centre.z===500&&made.radius===200,"an independent circle on the spawn circle");
check(made.tier==="Tier2"&&made.abundance==="very_rich","it keeps the tier and the capacity preset");
check(JSON.stringify(find("E1").linked_loot)===JSON.stringify({enabled:false,tier:"Tier2",abundance:"very_rich",radius:null})&&!ring("E1")&&zoneLine(id),"the encounter's linked loot is no longer enabled; the circle is drawn");
pick("E1");drag([1000+off(40),500],[1200+off(40),500]);
check(near(find("E1").centre.x,1200)&&find(id).centre.x===1000&&entries()===length+2,"a later move of the encounter does not move the circle");
await model.undo();await model.undo();check(JSON.stringify(objects())===before,"undo removes the circle and enables the linked loot again");
check(refusal(()=>L.detach(model,"E3"))==="This draft action is not valid."&&entries()===length+2,"no Detach without enabled linked loot");
model.lock("E1");const locked=entries();
check(refusal(()=>L.detach(model,"E1"))==="Unlock the object before editing its properties or geometry."&&entries()===locked&&objects().length===4,"a locked encounter refuses Detach");
'''), "PASS")

    def test_delete_and_excluded_encounter_keep_one_unit(self) -> None:
        """An excluded encounter keeps its linked loot and says so; Delete removes both in one action."""
        self.assertEqual(run_editor_harness(LINKED + r'''
const start=plan();start.objects=[linkedLoot("E1",1000,500,200,{},{excluded:true}),circle("Z2",1000,500,400),
  linkedLoot("E2",400,400,80,{},{properties:{event_name:"InfectedGone",smin:0,smax:0,dmin:3,dmax:5}})];
await load(loaded(start));pick("E1");
const note="Excluded: Apply does not write this encounter to the mission.";
check(text("map-encounter-next")===`${note} Its linked loot is still applied.`,"the excluded note adds that the linked loot is still applied");
check(ring("E1")&&mark("E1").dataset.mapState==="excluded"&&visible("map-zone-capacity-row")&&visible("map-tier-row"),"an excluded encounter keeps its linked loot");
L.set(model,"E1",false);check(text("map-encounter-next")===note,"without linked loot the note has no addition");
await model.undo();check(text("map-encounter-next")===`${note} Its linked loot is still applied.`,"undo brings the addition back");
pick("E2");check(text("map-encounter-next")==="Before Apply, make this encounter available, change it, or exclude it."&&ring("E2"),"an unavailable encounter keeps its next step and its linked loot");
field("map-encounter-exclude").click();check(text("map-encounter-next")===`${note} Its linked loot is still applied.`&&find("E2").linked_loot.enabled,"Exclude keeps the linked loot");
await model.undo();pick("E1");const length=position();field("map-properties-delete").click();
check(!find("E1")&&!ring("E1")&&!mark("E1")&&position()===length+1&&text("map-tool-notice")==="Encounter E1 deleted. Undo brings it back.","Delete removes the encounter and its linked loot as one action");
await model.undo();check(JSON.stringify(find("E1"))===JSON.stringify(start.objects[0])&&ring("E1")&&mark("E1")&&objects()[0].id==="E1","one undo brings both back at the same list position");
'''), "PASS")


    def test_inspect_names_linked_loot_winner_and_covering_encounters(self) -> None:
        """A point answer shows linked loot as the winning zone and lists the covering encounters by name."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + INSPECT + POINT + r'''
await load(tierValue(points));key(region,"i");click(1000,500);
const encounters=[{id:"E1",name:"Encounter E1",provider:"native_infected",hidden:false,excluded:false},
  {id:"E2",name:"Encounter E2",provider:"native_infected",hidden:true,excluded:true}];
await reply(done(pointResult({winner:{id:"E1",name:"Encounter E1",kind:"encounter",hidden:false},hidden_contributors:[],
  abundance:{global:0,zone:100,zone_preset:"very_rich",effective:100},encounters})));
const shown=rows();
check(shown["Winning zone"]==="Encounter E1. It changes only the capacity. Its tier is Inherit."&&shown["Capacity change"]==="Global 0%, zone Very Rich +100%: +100%","linked loot wins as its encounter "+JSON.stringify(shown));
check(shown["Encounters"]==="Encounter E1, Encounter E2","the covering encounters by name, in plan order");
'''), "PASS")


if __name__ == "__main__":
    unittest.main()
