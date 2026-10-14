"""Verify the 4.6 texts and read rules in composed Edge: capacity entries, causes, percent restarts and keys."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_inspect_msedge import POINT
from tests.test_mission_map_editor_select_msedge import SELECT
from tests.test_mission_map_editor_tier_read_msedge import INSPECT
from tests.test_mission_map_editor_tools_msedge import TOOLS
from tests.ui_harness_support import EDGE, run_editor_harness


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorInspectRulesEdgeTests(unittest.TestCase):
    """Use synthetic plans, fake Inspect answers and isolated headless profiles."""

    def test_capacity_entries_and_not_writable_causes(self) -> None:
        """Capacity entries as UI-UX 00:15:02 and 00:33:42 say; the cause of "(not writable)"; holdable case."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + INSPECT + r'''
const container=(name,baseline,realized)=>({name,baseline,realized,points:null,point_bound:null,point_limited:false,point_text:null});
const capacity=(containers,achievable,realized=9)=>({group:{baseline:4,realized},containers,achievable,qualified:false,bound_text:null,generated_group:null});
const refused=(reason,extra={})=>({source:"Land_Barn",adjustment:50,placements:1,kept:null,reason,capacity:null,...extra});
const answerList=[{source:"Land_Many",adjustment:50,placements:2,kept:null,reason:null,
  capacity:capacity([container("c1",1,2),container("c2",3,3),container("c3",1,2),container("c4",1,2),container("c5",1,2)],9)},
  {source:"Land_Low",adjustment:50,placements:1,kept:null,reason:null,capacity:capacity([],5)},
  {source:"Land_Kept",adjustment:50,placements:1,kept:"cluster_placement",reason:null,capacity:null},
  refused("ceiling_unresolved"),refused("default_missing"),refused("default_duplicate"),refused("ceiling_lexical"),
  refused("container_ambiguous"),refused("container_ambiguous",{field:"lootShelves"}),refused("ceiling_unsupported"),
  refused("dirt_placement_not_movable")];
await load(tierValue(start));pick("C");await reply(done(objectResult("C",["Tier1"],["Tier1"],answerList)));
const items=[...document.querySelectorAll("#map-tier-capacity-list li")].map(item=>item.textContent),head="Land_Barn: 1 building at +50%. ";
check(items[0]==="Land_Many: 2 buildings at +50%. Group capacity: 4 now, 9 with this plan. Containers: c1 1 to 2, c2 3, c3 1 to 2, c4 1 to 2, and 1 more.",items[0]);
check(items[1]==="Land_Low: 1 building at +50%. Group capacity: 4 now, 9 with this plan. The spawn points allow 5 at most.",items[1]);
check(items[2]==="Land_Kept: 1 building at +50%. The capacity stays unchanged.",items[2]);
const reasons=["Their capacity is not set in mapgroupproto.xml, so it cannot be changed.",
  "mapgroupproto.xml has no default capacity for them, so their capacity cannot be changed.",
  "mapgroupproto.xml has more than one default capacity for them, so their capacity cannot be changed.",
  "A lootmax value of their group in mapgroupproto.xml is not a whole number, so their capacity cannot be changed.",
  "Their group has two containers with the same name, so their capacity cannot be changed.",
  "Their group has more than one container named lootShelves, so their capacity cannot be changed.",
  "This adjustment would raise their capacity above the supported limit. Choose a smaller adjustment.",
  "They are in mapgroupdirt.xml, so Apply cannot change their capacity."];
reasons.forEach((reason,index)=>check(items[index+3]===head+reason,items[index+3]));
const causes=[["bounds_not_confirmed","Tiers cannot be written until the terrain bounds are confirmed. The effective tier is the original tier."],
  ["bounds_differ_from_header","Tiers cannot be written, because the terrain bounds differ from the tier map. The effective tier is the original tier."],
  ["tier_header_invalid","Tiers cannot be written in this mission. The effective tier is the original tier."]];
for(const [reason,cause] of causes){
  await load(tierValue(start,{...tierCaps,tier_writes:{available:false,reason}}));pick("P");
  check(select.options[select.selectedIndex].textContent==="Tier3 (not writable)"&&text("map-tier-message")===cause,`the cause of ${reason}`);
  pick("C");check(text("map-tier-message")==="","an Inherit zone has no cause text");
}
const lower=plan();lower.objects=[circle("Low",500,500,100,{tier:"tier2"})];
await load(tierValue(lower));pick("Low");
check(select.value==="Tier2"&&![...select.options].some(option=>option.disabled)&&text("map-tier-message")==="","a holdable tier matches without case");
''', budget=12000), "PASS")

    def test_percent_restarts_stale_continue_and_point_rows(self) -> None:
        """T2, T3 and T5 percent rules over restarts; a stale continue starts afresh; point rows; Escape owners."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + INSPECT + POINT + r'''
const sleep=(ms)=>new Promise(resolve=>setTimeout(resolve,ms));
await load(tierValue(points));pick("Far");await reply(running("j1","sampling",5,10));
await sleep(600);check(line()==="Effective tier: Reading tiers","no percent at 600 ms");
await sleep(500);check(line()==="Effective tier: Reading tiers, 50%","the percent after about 1 s");
await reply(running("j1","sampling",2,3));check(line()==="Effective tier: Reading tiers, 66%","the percent is rounded down");
await reply(stale("j1"));
check(named("inspect").length===2&&named("continue").length===2&&line()==="Effective tier: The mission files changed. Reading tiers again","a stale answer to a continue starts afresh, without the old job");
await reply(running("j2"));await sleep(1100);
check(line()==="Effective tier: The mission files changed. Reading tiers again","a restart drops the earlier percent");
await reply(running("j2","sampling",1,10));check(line()==="Effective tier: The mission files changed. Reading tiers again, 10%","then the percent of the new rows");
await reply(stale("j2"));await reply(running("j3","sampling",2,10));
check(line()==="Effective tier: The mission files changed. Reading tiers again","each restart waits about 1 s again");
await reply(done(objectResult("Far",["Tier1"],["Tier1"])));
tool("inspect");click(1000,500);
await reply(done(pointResult({not_writable:{id:"Under",name:"Loot circle Under",tier:"Tier4"},capacity_reason:null})));
const texts=[...document.querySelectorAll("#map-inspect-result dd")].map(item=>item.textContent);
check(texts.includes("Loot circle Under sets Tier4, but tiers cannot be written in this mission."),"a tier override that cannot be written is named");
check(text("map-inspect-capacity")==="","no point sentence without the point reason");
const dialog=document.createElement("dialog");document.body.append(dialog);key(region,"Escape");
check(marker(),"Escape with an open dialog keeps the marker");dialog.remove();
document.addEventListener("keydown",(event)=>event.preventDefault(),{capture:true,once:true});key(region,"Escape");
check(marker(),"an Escape that a drag cancel or a calibration took keeps the marker");
key(region,"Escape");check(!marker(),"a free Escape clears it");
''', budget=14000), "PASS")


if __name__ == "__main__":
    unittest.main()
