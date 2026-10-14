"""Verify the tier control and its read on demand in composed Edge with fake Inspect answers (4.6)."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_select_msedge import SELECT
from tests.test_mission_map_editor_tools_msedge import TOOLS
from tests.ui_harness_support import EDGE, run_editor_harness


# Fake Inspect calls: each start and continue waits until the test answers it, so the test sets the order of
# the answers. Cancel answers at once. The fakes replace the host API of the composed editor document.
INSPECT = r'''
const calls=[];
const fake=(name)=>(...args)=>new Promise(resolve=>calls.push({name,args,resolve,answered:false}));
window.pywebview.api.inspect=fake("inspect");window.pywebview.api.continue_inspection=fake("continue");
window.pywebview.api.cancel_inspection=async(job)=>{calls.push({name:"cancel",args:[job],answered:true});return {success:true,value:{cancelled:true}}};
const open=()=>calls.filter(call=>!call.answered),named=(name)=>calls.filter(call=>call.name===name);
const reply=async(answer,call=open()[0])=>{call.answered=true;call.resolve(answer);await wait();await wait()};
const ok=(value)=>({success:true,value}),done=(result)=>ok({status:"done",result}),stale=(id)=>ok({status:"stale",job:{id,ended:true}});
const running=(id,phase="reading",rows_done=null,rows_total=null)=>ok({status:"running",job:{id,phase,rows_done,rows_total}});
const refusal=(reason,message,extra={})=>({success:false,error:{code:"INVALID_REQUEST",message,retryable:false,details:{reason,...extra}}});
const lostJob=()=>refusal("inspection_unknown","The read stopped. Start Inspect again.");
const objectResult=(id,effective,original,capacity=[])=>({object_id:id,effective_tiers:effective,original_tiers:original,cell_count:12,
  cells:"d9",tier_writable:true,plan_source:"saved",capacity,capacity_reason:null});
const tierCaps={terrain_id:"synthetic",detected_bounds:null,header:{columns:2048,rows:1024,world_width:2000,world_height:1000,value_bits:4},
  holdable_tiers:["Tier1","Tier2","Tier3","Tier4"],tier_writes:{available:true,reason:null},unsupported:[]};
const tierValue=(document,caps=tierCaps)=>({...value(document),capabilities:structuredClone(caps)});
const element=(id)=>document.getElementById(id),line=()=>text("map-tier-effective"),select=element("map-tier-select");
const busy=()=>element("map-tier-read").getAttribute("aria-busy"),announced=()=>text("map-announcement");
const choose=(tier)=>{select.value=tier;select.dispatchEvent(new Event("change"))};
const start=plan();start.objects=[circle("C",1000,500,200),polygon("P",[[100,100],[400,100],[400,400]],{tier:"Tier3"}),
  circle("X",1600,500,100,{tier:"Tier9"}),circle("L",1600,800,100,{locked:true})];
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorTierReadEdgeTests(unittest.TestCase):
    """Use synthetic plans, fake Inspect answers and isolated headless profiles."""

    def test_select_reading_sampling_done_and_capacity(self) -> None:
        """Inherit first; Reading tiers from the start, a percent only from sampling rows after 1 s; the result."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + INSPECT + r'''
await load(tierValue(start));check(named("inspect").length===0&&element("map-tier-row").hidden,"no read and no tier control without a selection");
pick("C");
check([...select.options].map(option=>option.textContent).join("|")==="Inherit (original)|Tier1|Tier2|Tier3|Tier4"&&select.value===""&&!select.disabled,"Inherit first, then the tiers that the value layer can hold");
check(text("map-tier-label")==="Tier"&&select.getAttribute("aria-describedby")==="map-tier-effective map-tier-message","the select has its label and the read text");
check(line()==="Effective tier: Reading tiers"&&busy()==="true"&&element("map-tier-original").hidden&&element("map-tier-capacity").hidden,"Reading tiers from the start");
check(element("map-tier-effective").getAttribute("aria-live")==="off"&&element("map-tier-actions").hidden,"the percent text is not live; no actions while reading");
check(named("inspect")[0].args[0]===null&&JSON.stringify(named("inspect")[0].args[1])==='{"object":"C"}',"the saved plan and the object target");
await reply(running("j1"));check(named("continue")[0].args[0]==="j1","continue names the job");
await new Promise(resolve=>setTimeout(resolve,1100));check(line()==="Effective tier: Reading tiers","no percent without rows");
await reply(running("j1","reading",3,10));check(line()==="Effective tier: Reading tiers","only sampling rows give a percent");
await reply(running("j1","sampling",42,100));check(line()==="Effective tier: Reading tiers, 42%","after about 1 s the percent of the sampling rows");
await reply(running("j1","sampling",100,100));check(line()==="Effective tier: Reading tiers, 99%","99% at most until the result");
await reply(running("j1"));check(line()==="Effective tier: Reading tiers, 99%","the final proof keeps the last sampling percent");
const bound="Upper bound: the point counts are not proved usable, so fewer items may fit.",points="The container has fewer points than its capacity, so the points limit it.";
const capacity=[{source:"Land_Hospital",adjustment:75,placements:3,kept:null,reason:null,capacity:{group:{baseline:4,realized:7},
  containers:[{name:"lootFloor",baseline:3,realized:7,points:9,point_bound:7,point_limited:false,point_text:null},
    {name:"lootShelves",baseline:4,realized:9,points:2,point_bound:2,point_limited:true,point_text:points}],achievable:3,qualified:true,bound_text:bound,generated_group:"SMME_Hospital_7"}},
  {source:"PlannerRich_Shed",adjustment:50,placements:1,kept:"planner_content",reason:null,capacity:{group:{baseline:2,realized:2},containers:[],achievable:2,qualified:false,bound_text:null,generated_group:null}},
  {source:"Land_Unknown",adjustment:50,placements:2,kept:null,reason:"source_missing",capacity:null},
  {source:"Land_Shed",adjustment:0,placements:1,kept:null,reason:null,capacity:{group:{baseline:2,realized:2},containers:[],achievable:null,qualified:false,bound_text:null,generated_group:null}}];
await reply(done(objectResult("C",["Tier2","Tier3"],["Tier2"],capacity)));
check(line()==="Effective tier: Tier2, Tier3"&&text("map-tier-original")==="Original: Tier2"&&!element("map-tier-original").hidden&&busy()==="false","the result with the original set that differs");
check(announced()==="Effective tier: Tier2, Tier3","a read that showed a percent announces its result");
const items=[...document.querySelectorAll("#map-tier-capacity-list li")].map(item=>item.textContent);
check(!element("map-tier-capacity").hidden&&text("map-tier-capacity-title")==="Building capacity"&&items.length===4,"the building capacity list of the object answer");
check(items[0]===`Land_Hospital: 3 buildings at +75%. Group capacity: 4 now, 7 with this plan. Containers: lootFloor 3 to 7, lootShelves 4 to 9. The spawn points of lootShelves allow 2 at most. The spawn points allow 3 at most. ${bound} Apply writes them to group SMME_Hospital_7.`,items[0]);
check(!items[0].includes(points),"the generic backend point text does not show");
check(items[1]===`PlannerRich_Shed: 1 building at +50%. ${window.ServerManMapInspectWording.plannerContent}`,items[1]);
check(items[2]==="Land_Unknown: 2 buildings at +50%. Their group is not in mapgroupproto.xml, so their capacity is not known.",items[2]);
check(items[3]==="Land_Shed: 1 building at 0%. Group capacity: 2 now, 2 with this plan.",items[3]);
element("map-announcement").textContent="";pick("P");
check(select.value==="Tier3"&&line()==="Effective tier: Reading tiers"&&element("map-tier-capacity").hidden,"a new selection reads its object; no stale capacity shows");
await reply(running("jp","sampling",50,100));check(line()==="Effective tier: Reading tiers","no percent before about 1 s");
await reply(done(objectResult("P",["Tier3"],["Tier3"])));
check(line()==="Effective tier: Tier3"&&element("map-tier-original").hidden&&announced()==="","equal sets hide Original; a quick read is not announced");
check([...document.querySelectorAll("#map-tier-capacity-list li")].map(item=>item.textContent).join()==="This zone covers no buildings.","an object without buildings says so");
pick("X");const stored=[...select.options].at(-1);
check(stored.textContent==="Tier9 (not holdable)"&&stored.disabled&&select.value==="Tier9"&&text("map-tier-message")===window.ServerManMapInspectWording.tierNotHoldable,"a stored tier that the layer cannot hold stays as a disabled option with its reason");
pick("L");check(select.disabled,"a locked zone has no tier choice");
await load(tierValue(start,{...tierCaps,tier_writes:{available:false,reason:"bounds_not_confirmed"}}));pick("P");
check(select.options[select.selectedIndex].textContent==="Tier3 (not writable)","a tier override says not writable while tier writes are unavailable");
await load(value(start));pick("C");const before=named("inspect").length;
check(line()==="Effective tier: Not read. This mission has no usable tier map, so the effective tier cannot be read."&&element("map-tier-again").hidden&&!element("map-tier-point").hidden,"a mission without a tier map shows its reason without a read");
await load(tierValue(start,{...tierCaps,header:null,unsupported:[{operation:"tier_layer",reason:"areaflags_missing"}]}));pick("C");
check(line()==="Effective tier: Not read. This mission has no areaflags.map, so its original tiers cannot be shown."&&named("inspect").length===before,"the capability reason, still without a read");
''', budget=12000), "PASS")

    def test_edits_selection_stale_lost_outcome_and_failures(self) -> None:
        """A newer edit or selection replaces the read; stale, late and pre-Apply answers are dropped; T6 and T7."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + INSPECT + r'''
await load(tierValue(start));pick("C");const first=named("inspect")[0];
choose("Tier2");check(find("C").tier==="Tier2"&&entries()===1&&!select.disabled,"the select stays enabled during a read; a choice is one undo action");
check(named("inspect").length===2&&named("inspect")[1].args[0].objects[0].tier==="Tier2"&&line()==="Effective tier: Reading tiers","the edit starts a new read of the draft");
await reply(running("old","sampling",5,10),first);
check(named("cancel").some(call=>call.args[0]==="old")&&named("continue").length===0&&line()==="Effective tier: Reading tiers","a late answer of the replaced read is dropped and its job cancelled");
await reply(stale("j2"));
check(line()==="Effective tier: The mission files changed. Reading tiers again"&&named("inspect").length===3,"a stale answer starts a fresh read");
await reply(running("j3"));await reply(lostJob());
check(named("inspect").length===4&&line()==="Effective tier: Reading tiers","the first lost job starts once more with the plain text");
await reply(running("j4"));await reply(lostJob());
check(line()==="Effective tier: Not read. The read stopped."&&named("inspect").length===4&&busy()==="false","a second lost job is the failure, without a retry");
check(!element("map-tier-actions").hidden&&text("map-tier-again")==="Read again"&&text("map-tier-point")==="Inspect a point","the failure offers Read again and Inspect a point");
check(announced()==="Effective tier: Not read. The read stopped.","the failure is announced");
element("map-tier-again").focus();element("map-tier-again").click();await wait();
check(named("inspect").length===5&&line()==="Effective tier: Reading tiers"&&document.activeElement===select,"Read again starts a fresh read; the focus stays in the tier control");
pick("P");check(named("inspect").length===6&&named("inspect")[5].args[1].object==="P","a newer selection replaces the read");
await reply(done(objectResult("C",["Tier1"],["Tier1"])),named("inspect")[4]);
check(line()==="Effective tier: Reading tiers","the answer for the earlier selection is dropped");
await reply(running("j6"));const before=named("continue").at(-1);
window.ServerManMapInspectRead.outcome();
check(named("cancel").some(call=>call.args[0]==="j6")&&named("inspect").length===7&&line()==="Effective tier: Reading tiers","an Apply or Restore outcome starts the read again");
await reply(done(objectResult("P",["Tier4"],["Tier4"])),before);
check(line()==="Effective tier: Reading tiers","an answer of a job that started before the last Apply or Restore is dropped");
await reply(done(objectResult("P",["Tier3"],["Tier3"])));check(line()==="Effective tier: Tier3","the fresh read shows its result");
pick("C");
await reply(refusal("invalid_document","The plan is not valid.",{problems:[{object_id:"C",rule:"radius",message:"The radius must be from 1 to 100000 m."}]}));
check(line()==="Effective tier: Not read. The plan has an error. Correct it first. Loot circle C: The radius must be from 1 to 100000 m.","an invalid plan shows Save's errors");
check(element("map-tier-again").hidden&&!element("map-tier-point").hidden,"only Inspect a point helps an invalid plan");
element("map-tier-point").click();
check(text("map-tool-status")==="Inspect"&&element("map-tool-inspect").getAttribute("aria-pressed")==="true"&&document.activeElement===region,"Inspect a point chooses the Inspect tool and focuses the map");
check(text("map-tool-notice")==="Click a point inside Loot circle C to see its tier.","the tool status names the zone");
pick("P");
await reply({success:false,error:{code:"STORAGE_FAILURE",message:"A mission file could not be read.",retryable:true,details:{reason:"mission_file_unreadable"}}});
check(line()==="Effective tier: Not read. A mission file could not be read."&&!element("map-tier-again").hidden,"an unreadable file offers Read again");
pick("C");await reply(refusal("tier_header_invalid","The tier map is not valid."));
check(line()==="Effective tier: Not read. The tier map of this mission is not valid.","an invalid header has its reason");
pick("P");const reads=named("inspect").length;await load(tierValue(start));
check(named("inspect").length===reads&&element("map-tier-row").hidden,"a load ends the selection and its read");
''', budget=12000), "PASS")


if __name__ == "__main__":
    unittest.main()
