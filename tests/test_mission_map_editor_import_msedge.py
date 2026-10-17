"""Verify the compatibility dialog of Import plan for each preview case in composed Edge, with a fake host (9.1).

Rules:
- UI-UX Designer 2026-10-17 17:08:16 in evidence 9.1, section 5: the sections, the choices and the focus order.
- Architect 17:10:42, sections 4 and 5: both previews, `expected_revision` and `confirmed`.
- Product Owner 17:14:17: the texts; the CLI review texts without their CLI-only lines (PO-91-2).
"""

from __future__ import annotations

import unittest

from tests.mission_map_transfer_support import transfer_page
from tests.ui_harness_support import EDGE

# Choose a mode and submit; the dialog closes and the import call carries the choices
SUBMIT = r'''
const choose=async(mode)=>{radio(mode).click();confirmButton().click();for(let index=0;index<40&&!imports().length;index++)await wait();
  await wait();await wait();return imports().at(-1)};
const headings=()=>inside("h3").map(item=>item.textContent),lines=()=>dialog().querySelector(".map-preview-body").textContent;
'''


def run(body: str) -> str:
    """Run the dialog helpers with the fake host."""
    return transfer_page(SUBMIT + body)


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class ImportPreviewEdgeTests(unittest.TestCase):
    """Fake previews, the real editor modules and isolated headless profiles."""

    def test_same_terrain_needs_only_the_mode(self) -> None:
        """No box; no radio checked; the gate, the focus and the payload with the preview revision."""
        self.assertEqual(run(r'''
await start();await begin();
check(dialog().querySelector("h2").textContent===W.title&&document.activeElement===dialog().querySelector("h2"),"title with the focus");
check(lines().includes(W.intro)&&lines().includes("Terrain: same as this mission")&&lines().includes("Bounds: same"),"intro and compatibility");
check(lines().includes("Objects in the file: 1")&&dialog().querySelector("legend").textContent===W.legend,"count and legend");
check(boxes().length===0&&!radio("replace").checked&&!radio("merge").checked,"no box and no preselected mode");
check(lines().includes("Replace: the objects of the file replace the objects of this plan.")&&lines().includes(
  "Merge: the objects of the file go above all objects of this plan, in their order."),"both effects show together");
check(lines().includes("Global container capacity: 0%.")&&!lines().includes("stays."),"the Merge capacity line only when the values differ");
check(lines().includes("The plan will have 1 object and 0 polygon vertices.")&&lines().includes("The plan will have 2 objects and 0 polygon vertices."),"results");
check(remaining()==="1 choice is still needed before you can import."&&confirmButton().getAttribute("aria-disabled")==="true","the gate");
check(!dialog().querySelector(".map-preview-footer").textContent.includes("Stop the DayZ server"),"no stop sentence");
confirmButton().click();check(document.activeElement===radio("replace")&&!imports().length,"a missing choice takes the focus");
check(radio("replace").getAttribute("aria-describedby")&&element(radio("replace").getAttribute("aria-describedby")).textContent.includes("Replace:"),"the effect describes its radio");
const call=await choose("replace");
check(JSON.stringify(call)===JSON.stringify(["import",selection,"replace",4,false]),`payload: ${JSON.stringify(call)}`);
'''), "PASS")

    def test_another_terrain_needs_the_placement_box(self) -> None:
        """The warning and its box first; confirmed only with every box; the box names the warning."""
        self.assertEqual(run(r'''
await start();
await begin(previews({},{global_abundance:{destination:0,imported:25,result:0}},{terrain_match:false,bounds_equal:false,
  imported_terrain_id:"pripyat",bounds_difference:{x_min:-0.5,z_min:0,width:19480,height:1.25},placement_review:true,confirmation_required:true}));
check(lines().includes("Terrain: pripyat in the file, synthetic in this mission"),"both terrains");
check(lines().includes("Bounds: the file differs by X -0.5, Z 0, width 19480 and height 1.25 metres"),"the bounds difference");
check(lines().includes("Global container capacity: 0% stays. The file has +25%."),"the Merge capacity line");
const [box]=boxes(),warning=dialog().querySelector(".notice-warning");
check(warning.textContent===W.placement_review&&box.parentElement.textContent===W.placement_box,"warning and box texts");
check(warning.nextElementSibling===box.closest("label")&&box.getAttribute("aria-describedby")===warning.id,"the box directly after its warning");
check(remaining()==="2 choices are still needed before you can import."&&!box.checked,"two choices");
confirmButton().click();check(document.activeElement===box,"the placement box comes first in the focus order");
box.click();radio("merge").click();check(remaining()===""&&!confirmButton().hasAttribute("aria-disabled"),"the gate opens");
box.click();check(confirmButton().getAttribute("aria-disabled")==="true","unchecking closes the gate again");
box.click();const call=await choose("merge");
check(JSON.stringify(call)===JSON.stringify(["import",selection,"merge",4,true]),`confirmed: ${JSON.stringify(call)}`);
'''), "PASS")

    def test_left_out_and_unmapped_lists_have_their_boxes(self) -> None:
        """Left out with its linked loot; R21 unmapped items; each box after its list; the new-ID list of Merge."""
        self.assertEqual(run(r'''
await start();
await begin(previews({},{remapped:[{from:"imp"}]},{excluded:[{id:"e9",name:"Camp",linked_loot:{enabled:true,radius:20}}],excluded_count:1,
  unmapped:[{id:"P101",reason:"A bandit point has no ground height."},{id:null,reason:"Weapon policy: Test weapon rule"}],
  discarded:["notes","author"],confirmation_required:true}));
check(headings().includes("Objects left out because they do not fit the bounds")&&headings().includes("Items that are not imported"),`headings ${headings()}`);
check(lines().includes("Camp (with its linked loot)")&&lines().includes("P101: A bandit point has no ground height.")&&lines().includes("Weapon policy: Test weapon rule"),"rows");
const labels=boxes().map(item=>item.parentElement.textContent);
check(JSON.stringify(labels)===JSON.stringify(["Import without the listed object","Import without the 2 listed items"]),`box labels ${labels}`);
check(boxes().every(item=>!item.checked&&item.closest("section").querySelector("ul")),"each box in the section of its list");
check(lines().includes("Fields of the file that are not kept: 2")&&lines().includes("Objects that get a new ID: 1"),"discarded fields and new IDs");
check(remaining()==="3 choices are still needed before you can import.","three choices");
radio("replace").click();confirmButton().click();check(document.activeElement===boxes()[0],"then the left-out box");
boxes()[0].click();confirmButton().click();check(document.activeElement===boxes()[1],"then the unmapped box");
boxes()[1].click();const call=await choose("replace");check(call[4]===true,"confirmed with every box");
'''), "PASS")

    def test_plan_limits_and_the_unavailable_list(self) -> None:
        """A limit disables one radio with XFER-31; both limits refuse the answer; the unavailable reasons."""
        self.assertEqual(run(r'''
await start();
await begin(previews({},{refusal:"over",refusal_text:W.limit},{unavailable:[{id:"t1",name:"Tier zone",reason:"tier_not_holdable",kind:"loot_circle"},
  {id:"b1",name:"Bandits",reason:"Bandit placement is not available on this terrain: no verified ground height source.",kind:"encounter",provider:"ai_bandits"},
  {id:"i1",name:"City",reason:"event_not_offered",kind:"encounter",provider:"native_infected",event_name:"InfectedCity"}]}));
check(radio("merge").disabled&&!radio("replace").disabled,"Merge cannot be chosen");
check(lines().includes(`This mode is not possible. ${W.limit}`),"XFER-31 with the bridge text");
check(lines().includes("Tier zone: The tier map of this mission cannot hold this tier. Choose another tier or Inherit."),"tier reason");
check(lines().includes("Bandits: Bandit placement is not available on this terrain: no verified ground height source."),"provider reason");
check(lines().includes("City: This mission does not offer the infected event InfectedCity."),"event reason");
check(boxes().length===0,"the unavailable list has no box");
button("Cancel").click();await wait();check(!dialog()&&!imports().length,"Cancel ends without a call");
await begin(previews({refusal:"over",refusal_text:W.limit},{refusal:"over",refusal_text:W.limit}));
check(!confirmButton()&&button("Close")&&radio("replace").disabled&&radio("merge").disabled,"both refused: Close only");
button("Close").click();await wait();check(!dialog(),"closed");
'''), "PASS")

    def test_an_invalid_document_lists_each_problem(self) -> None:
        """The bridge text and the collapsed list of XFER-32; Close only; nothing is imported."""
        self.assertEqual(run(r'''
await start();
await begin({success:false,error:{code:"INVALID_REQUEST",message:W.invalid_document,retryable:false,details:{reason:"invalid_document",
  selection_id:selection,file_name:"plan.json",problems:[{object_id:"c1",rule:"invalid_document",message:"radius must be positive"},
  {object_id:null,rule:"invalid_document",message:"objects must be a list"}]}}});
check(dialog().querySelector(".notice-error").textContent===W.invalid_document,"the bridge text");
const toggle=dialog().querySelector(".map-preview-list .link-button");
check(lines().includes("Objects with problems: 2")&&toggle.getAttribute("aria-expanded")==="false","the collapsed list with its count");
toggle.click();check(lines().includes("c1: radius must be positive")&&lines().includes("objects must be a list"),"rows");
check(!confirmButton()&&button("Close")&&!radio("replace"),"Close only");
button("Close").click();await wait();check(!dialog()&&!imports().length&&document.activeElement===element("map-import"),"focus on the opener");
'''), "PASS")


if __name__ == "__main__":
    unittest.main()
