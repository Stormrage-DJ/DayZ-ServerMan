"""Verify Inspect mode in composed Edge: point answers, the marker, cancellation and one job at a time (4.6)."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_select_msedge import SELECT
from tests.test_mission_map_editor_tier_read_msedge import INSPECT
from tests.test_mission_map_editor_tools_msedge import TOOLS
from tests.ui_harness_support import EDGE, run_editor_harness


# A point answer of the 4.2 shape: a hidden winner that changes capacity only, a tier from the zone below it
POINT = r'''
const pointResult=(extra={})=>({x:1000,z:500,grid:{h:5,v:5},header_cell:{i:1024,j:512},cell:{i:1024,j:512,x:1000.49,z:500.49},
  original_tier:["Tier1"],effective_tier:["Tier2"],tier_source:"zone",tier_object:{id:"Under",name:"Loot circle Under",kind:"loot_circle",hidden:false},
  not_writable:null,winner:{id:"Top",name:"Loot circle Top",kind:"loot_circle",hidden:true},abundance:{global:25,zone:50,zone_preset:"rich",effective:75},
  hidden_contributors:[{id:"Top",name:"Loot circle Top",kind:"loot_circle",hidden:true}],abundance_only:true,encounters:[],capacity:null,
  capacity_reason:"point_target",plan_source:"saved",plan_revision:4,...extra});
const rows=()=>Object.fromEntries([...document.querySelectorAll("#map-inspect-result > div")].map(row=>[row.children[0].textContent,row.children[1].textContent]));
const status=()=>text("map-inspect-status"),marker=()=>document.querySelector("#map-inspect-marker");
const points=plan();points.objects=[circle("Top",1000,500,150,{visible:false}),circle("Under",1000,500,300,{tier:"Tier2"}),circle("Far",1800,800,50)];
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorInspectEdgeTests(unittest.TestCase):
    """Use synthetic plans, fake Inspect answers and isolated headless profiles."""

    def test_point_answer_marker_and_deleted_winner(self) -> None:
        """A click reads its point; the answer names hidden and capacity-only zones; a deleted winner reads again."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + INSPECT + POINT + r'''
await load(tierValue(points));
check(text("map-inspect-title")==="Inspect"&&status()==="Choose Inspect (I) and click a point on the map."&&element("map-inspect-result").hidden,"the idle panel");
key(region,"i");check(text("map-tool-status")==="Inspect"&&element("map-tool-inspect").getAttribute("aria-pressed")==="true","I chooses the Inspect tool");
check(element("map-tool-inspect").getAttribute("aria-keyshortcuts")==="I"&&text("map-tool-inspect")==="Inspect","the tool button names its key");
click(1000,500);const call=named("inspect")[0];
check(call.args[0]===null&&Math.abs(call.args[1].point.x-1000)<3&&Math.abs(call.args[1].point.z-500)<3,"a click reads the point of the saved plan");
check(objects().length===3&&entries()===0&&selected()===null,"Inspect places and selects nothing");
check(status()==="Reading tiers"&&element("map-inspect").getAttribute("aria-busy")==="true"&&marker()&&!element("map-inspect-cancel").hidden,"Reading tiers, the marker and Cancel read");
await reply(running("p1","sampling",10,20));await new Promise(resolve=>setTimeout(resolve,1100));
check(status()==="Reading tiers","the Inspect panel shows no percent (T1)");
await reply(done(pointResult()));
const shown=rows();
check(status()===""&&element("map-inspect").getAttribute("aria-busy")==="false"&&element("map-inspect-cancel").hidden,"the answer replaces Reading tiers");
check(shown["Coordinates"].startsWith("Native X")&&shown["Tier map cell"]==="Column 1024, row 512"&&shown["Original tier"]==="Tier1",JSON.stringify(shown));
check(shown["Effective tier"]==="Tier2 from Loot circle Under","the effective tier names its zone");
check(shown["Winning zone"]==="Loot circle Top (hidden on the map). It changes only the capacity. Its tier is Inherit.","a hidden, capacity-only winner is identified");
check(shown["Hidden zones"]==="Loot circle Top. They are hidden on the map and still count.","hidden contributors are named");
check(shown["Capacity change"]==="Global +25%, zone Rich +50%: +75%"&&shown["Encounters"]==="None","the capacity breakdown and no encounters");
check(text("map-inspect-capacity")==="Building capacity: inspect a zone to see the buildings it covers."&&text("map-inspect-capacity")===window.ServerManMapInspectWording.pointCapacity,"a point answer shows the point capacity sentence");
check(announced()==="Inspect: effective tier Tier2 from Loot circle Under.","a read that a click started is announced politely");
element("map-announcement").textContent="";
pick("Top");element("map-properties-delete").click();const again=named("inspect").at(-1);
check(!find("Top")&&entries()===1&&again.args[1].point&&again.args[0].objects.length===2,"deleting the winner reads the point again with the draft");
check(marker()&&status()==="Reading tiers","the marker stays while the point is read again");
await reply(done(pointResult({winner:{id:"Under",name:"Loot circle Under",kind:"loot_circle",hidden:false},abundance_only:false,hidden_contributors:[],tier_source:"zone"})),again);
check(rows()["Winning zone"]==="Loot circle Under"&&!rows()["Hidden zones"],"the next zone wins");
pick("Under");element("map-properties-delete").click();
await reply(done(pointResult({winner:null,abundance_only:false,hidden_contributors:[],tier_source:"original",tier_object:null,effective_tier:["Tier1"],
  abundance:{global:25,zone:0,zone_preset:null,effective:25}})),named("inspect").at(-1));
const original=rows();
check(original["Winning zone"]==="None. The original settings apply."&&original["Effective tier"]==="Tier1 (original)"&&original["Capacity change"]==="Global +25%, no zone: +25%",JSON.stringify(original));
check(!announced().startsWith("Inspect:"),"the read again after a draft change is not announced");
await load(tierValue(points));check(!marker()&&status()==="Choose Inspect (I) and click a point on the map.","a load ends the point of the earlier plan");
''', budget=12000), "PASS")

    def test_cancel_escape_failures_and_one_job_at_a_time(self) -> None:
        """Escape and Cancel read end the point read; stale and failed reads; the tier read waits for a point read."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + INSPECT + POINT + r'''
await load(tierValue(points));tool("inspect");click(1000,500);await reply(running("p1"));
key(region,"Escape");
check(!marker()&&status()==="Choose Inspect (I) and click a point on the map."&&named("cancel").some(call=>call.args[0]==="p1"),"Escape clears the marker and cancels the job");
await reply(done(pointResult()));check(element("map-inspect-result").hidden,"a late answer after Escape is dropped");
click(1000,500);await reply(running("p2"));element("map-inspect-cancel").click();
check(!marker()&&named("cancel").some(call=>call.args[0]==="p2")&&document.activeElement===region,"Cancel read ends the read and focuses the map");
await reply(done(pointResult()));
click(1000,500);await reply(stale("p3"));
check(status()==="The mission files changed. Reading tiers again"&&open().length===1&&open()[0].name==="inspect","a stale answer reads the point again");
await reply({success:false,error:{code:"STORAGE_FAILURE",message:"A mission file could not be read.",retryable:true,details:{reason:"mission_file_unreadable"}}});
check(status()==="Not read. A mission file could not be read."&&!element("map-inspect-again").hidden&&announced()==="Not read. A mission file could not be read.","a failure shows its reason and Read again");
element("map-inspect-again").click();await wait();check(open().length===1&&status()==="Reading tiers"&&document.activeElement===region,"Read again reads the point again");
await reply(running("p4"));
check(named("inspect").length===5,"one point read at a time");
pick("Far");
check(named("inspect").length===5&&line()==="Effective tier: Reading tiers"&&status()==="Reading tiers","the tier read waits while the point read runs");
await reply(done(pointResult()));
check(named("inspect").length===6&&named("inspect")[5].args[1].object==="Far"&&rows()["Original tier"]==="Tier1","the tier read starts when the point read is done");
await reply(done(objectResult("Far",["Tier1"],["Tier1"])));check(line()==="Effective tier: Tier1","the tier read shows its result");
key(document.getElementById("map-properties-name-field"),"Escape");
check(marker()&&selected()==="Far","Escape in a text field keeps the marker");
key(region,"Escape");check(!marker()&&selected()===null,"Escape on the map clears the selection and the marker");
''', budget=12000), "PASS")


if __name__ == "__main__":
    unittest.main()
