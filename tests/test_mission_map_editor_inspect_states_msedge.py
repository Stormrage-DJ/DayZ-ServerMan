"""Verify Inspect states in composed Edge: an Apply or Restore outcome, and read-only drafts (4.6 rework)."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_inspect_msedge import POINT
from tests.test_mission_map_editor_select_msedge import SELECT
from tests.test_mission_map_editor_tier_read_msedge import INSPECT
from tests.test_mission_map_editor_tools_msedge import TOOLS
from tests.ui_harness_support import EDGE, run_editor_harness


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorInspectStatesEdgeTests(unittest.TestCase):
    """Use synthetic plans, fake Inspect answers and isolated headless profiles."""

    def test_outcome_clears_every_shown_answer(self) -> None:
        """After an Apply or Restore outcome no earlier answer shows, also for the read that waits (QA-4.6-F1)."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + INSPECT + POINT + r'''
const shed={source:"Land_Shed",adjustment:0,placements:1,kept:null,reason:null,capacity:{group:{baseline:2,realized:2},containers:[],
  achievable:null,qualified:false,bound_text:null,generated_group:null}};
await load(tierValue(points));pick("Far");await reply(done(objectResult("Far",["Tier2"],["Tier1"],[shed])));
tool("inspect");click(1000,500);await reply(done(pointResult()));
check(line()==="Effective tier: Tier2"&&!element("map-tier-capacity").hidden&&!element("map-inspect-result").hidden,"both answers show");
window.ServerManMapInspectRead.outcome();
check(line()==="Effective tier: Reading tiers"&&busy()==="true"&&element("map-tier-original").hidden&&element("map-tier-capacity").hidden,"the waiting tier read shows Reading tiers, not its earlier answer");
check(status()==="Reading tiers"&&element("map-inspect-result").hidden&&element("map-inspect-capacity").hidden,"the point panel shows Reading tiers, not its earlier answer");
check(open().length===1&&open()[0].args[1].point,"one read at a time; the newest wanted read, the point, starts first");
await reply(done(pointResult()));
check(open().length===1&&open()[0].args[1].object==="Far"&&line()==="Effective tier: Reading tiers","then the tier read starts");
await reply(done(objectResult("Far",["Tier3"],["Tier3"])));check(line()==="Effective tier: Tier3","the new answer shows");
// Inspect Start Order: older Starts that end last answer inspection_unknown, and the newest job stays live (QA-4.6-F8)
pick("Under");pick("Top");pick("Far");const starts=named("inspect").slice(-3);
await reply(lostJob(),starts[0]);await reply(lostJob(),starts[1]);
check(line()==="Effective tier: Reading tiers"&&named("inspect").length===starts.length+named("inspect").indexOf(starts[0]),"the lost answers of replaced reads start nothing");
await reply(done(objectResult("Far",["Tier2"],["Tier2"])),starts[2]);check(line()==="Effective tier: Tier2","the newest read shows its result, not The read stopped");
''', budget=12000), "PASS")

    def test_read_only_drafts_offer_inspect_only(self) -> None:
        """Inspect works read only with confirmed bounds; editing tools stay unavailable (Product Owner 00:33:43)."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + INSPECT + POINT + r'''
const editing=()=>["select","circle","polygon"].every(name=>element(`map-tool-${name}`).disabled);
await load({...tierValue(points),read_only:true,read_only_reason:"mission_path_changed"});
check(!element("map-tool-inspect").disabled&&editing(),"in the path-change state Inspect is available and the editing tools are not");
key(region,"c");check(text("map-tool-status")==="No tool active","C chooses no editing tool");
key(region,"i");check(text("map-tool-status")==="Inspect","I chooses Inspect");
const dialog=document.createElement("dialog");document.body.append(dialog);window.ServerManMapTools.refresh();
check(element("map-tool-inspect").disabled&&!window.ServerManMapTools.usable("inspect"),"no Inspect while a dialog is open");
click(1000,500);check(named("inspect").length===0,"a click with an open dialog reads nothing");
dialog.remove();window.ServerManMapTools.refresh();
click(1000,500);
check(named("inspect").length===1&&named("inspect")[0].args[0]===null&&objects().length===3&&entries()===0,"a click reads the point of the shown plan and places nothing");
await reply(done(pointResult()));check(rows()["Effective tier"]==="Tier2 from Loot circle Under","the point answer shows");
click(1000,500,true);check(selected()===null&&named("inspect").length===1,"Shift+click selects nothing in a read-only draft");
pick("Far");check(select.disabled&&named("inspect").length===2,"the tier select stays disabled; the tier line reads");
await reply({success:false,error:{code:"STORAGE_FAILURE",message:"A mission file could not be read.",retryable:true,details:{reason:"mission_file_unreadable"}}});
tool("inspect");check(text("map-tool-status")==="No tool active"&&!element("map-tier-point").hidden,"Inspect a point is offered while the tool can be chosen");
element("map-tier-point").click();
check(text("map-tool-status")==="Inspect"&&element("map-tool-inspect").getAttribute("aria-pressed")==="true"&&document.activeElement===region,"Inspect a point chooses the tool");
check(text("map-tool-notice")==="Click a point inside Loot circle Far to see its tier.","and names the zone");
// Load returns no plan for invalid saved data, so no bounds are confirmed (Product Owner 2026-10-14 01:26:03)
await load({...tierValue(null),state:"CORRUPT",read_only:true,read_only_reason:"invalid_saved_data",detail:"the saved plan is not valid JSON"});
check(element("map-tool-inspect").disabled&&editing()&&!window.ServerManMapTools.usable("inspect"),"with invalid saved data Inspect is unavailable");
check(text("map-status").startsWith(window.ServerManMapWording.readOnly("invalid_saved_data"))&&!element("map-set-aside").hidden,"the read-only reason and Set aside invalid plan show");
const unconfirmed=structuredClone(points);unconfirmed.terrain.bounds_confirmed=false;
await load(tierValue(unconfirmed,{...tierCaps,tier_writes:{available:false,reason:"bounds_not_confirmed"}}));
check(element("map-tool-inspect").disabled,"Inspect needs confirmed bounds");
pick("Far");await reply(refusal("tier_header_invalid","The tier map is not valid."));
check(!element("map-tier-actions").hidden&&element("map-tier-point").hidden&&!element("map-tier-again").hidden,"no Inspect a point while the tool cannot be chosen");
''', budget=12000), "PASS")

    def test_inspect_a_point_follows_a_bounds_confirmation(self) -> None:
        """Confirmed manual bounds offer Inspect a point at once, without a new selection (QA-4.6-F7)."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + INSPECT + POINT + r'''
const unconfirmed=structuredClone(points);unconfirmed.terrain.bounds_confirmed=false;
await load(tierValue(unconfirmed,{...tierCaps,header:null,holdable_tiers:[],tier_writes:{available:false,reason:"tier_header_invalid"},
  unsupported:[{operation:"tier_layer",reason:"areaflags_missing"}]}));
pick("Far");check(element("map-tier-point").hidden&&element("map-tool-inspect").disabled,"no Inspect a point before the bounds are confirmed");
fill(bounds());manual();for(let index=0;index<10;index++)await wait();
check(model.snapshot().terrain.bounds_confirmed&&selected()==="Far"&&!element("map-tool-inspect").disabled,"the bounds are confirmed; Far stays selected");
check(!element("map-tier-point").hidden,"Inspect a point shows at once");
''', budget=12000), "PASS")


if __name__ == "__main__":
    unittest.main()
