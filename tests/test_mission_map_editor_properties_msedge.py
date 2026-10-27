"""Verify the Properties panel in composed Edge: rename, Delete and the circle centre fields (4.5)."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_select_msedge import SELECT
from tests.test_mission_map_editor_tools_msedge import TOOLS
from tests.ui_harness_support import EDGE, run_editor_harness


# Field helpers: a change event is the edit moment (Save Status and Close Dialogs)
FIELDS = r'''
// The field keeps the focus, as after Enter, so a render does not refill it
const input=(id)=>document.getElementById(id),change=(id,text)=>{input(id).focus();input(id).value=text;input(id).dispatchEvent(new Event("change"))};
const message=()=>text("map-properties-message"),invalid=(id)=>input(id).getAttribute("aria-invalid")==="true";
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorPropertiesEdgeTests(unittest.TestCase):
    """Use synthetic plans, fake save answers and isolated headless profiles."""

    def test_rename_and_delete_are_one_action_each(self) -> None:
        """A valid name and a delete are one undo action; a refused name, a text field and a locked item change nothing."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + FIELDS + r'''
const start=plan();start.objects=[circle("C",1000,500,200),polygon("P",[[100,100],[400,100],[400,400]]),circle("L",1600,500,100,{locked:true})];
await load(value(start));pick("C");
check(input("map-properties-name-field").value==="Loot circle C"&&!input("map-properties-name-field").disabled,"the name field shows the stored name");
change("map-properties-name-field","  Spawn east  ");
check(find("C").name==="Spawn east"&&input("map-properties-name-field").value==="Spawn east"&&entries()===1&&!invalid("map-properties-name-field")&&message()==="","a valid name is one action, trimmed by the D1 rule");
change("map-properties-name-field","   ");
check(find("C").name==="Spawn east"&&entries()===1&&invalid("map-properties-name-field")&&message()===window.ServerManMapWording.invalidName,"a blank name is refused with its reason");
change("map-properties-name-field","x".repeat(81));check(find("C").name==="Spawn east"&&entries()===1,"a name over 80 characters is refused");
// The Undo button takes the focus from the field, so the field shows the restored name
input("map-properties-name-field").blur();await model.undo();check(find("C").name==="Loot circle C"&&input("map-properties-name-field").value==="Loot circle C","undo restores the name");
key(input("map-properties-name-field"),"Delete");check(objects().length===3,"the Delete key in a text field deletes nothing");
const button=input("map-properties-delete");button.focus();button.click();
check(!find("C")&&objects().length===2&&entries()===1&&position()===1&&selected()===null,"the Delete button deletes at once as one action");
check(document.activeElement===region&&text("map-tool-notice")==="Loot circle C deleted. Undo brings it back.","the focus goes to the map; the notice says what happened");
await model.undo();check(geometry("C")===JSON.stringify(start.objects[0])&&objects()[0].id==="C","undo brings the item back in its place");
pick("P");key(region,"Delete");check(!find("P")&&entries()===1&&position()===1&&document.activeElement===region,"the Delete key deletes the selected item from the map");
await model.undo();check(find("P")&&objects()[1].id==="P","undo restores it");
pick("P");key(document.querySelector('[data-map-control="C:visible"]'),"Delete");check(find("P")&&find("C")&&position()===0,"the Delete key on a list row of another item deletes nothing");
key(document.querySelector('[data-map-control="P:visible"]'),"Delete");check(!find("P")&&position()===1&&document.activeElement===region,"the Delete key acts from the selected item's own list row");
await model.undo();pick("C");key(input("map-properties-delete"),"Delete");check(!find("C")&&position()===1&&document.activeElement===region,"and from a Properties button");
await model.undo();pick("L");
check(input("map-properties-name-field").disabled&&input("map-properties-delete").disabled&&input("map-properties-centre-x").disabled&&input("map-properties-centre-z").disabled,"a locked item has no name, centre or Delete control");
key(region,"Delete");check(find("L")&&position()===0&&text("map-tool-notice")===window.ServerManMapWording.objectLocked,"the Delete key refuses a locked item and says why");
let refused=false;try{model.remove("Delete object","L")}catch(error){refused=error.message===window.ServerManMapWording.objectLocked}
check(refused&&find("L"),"the model refuses to delete a locked item");
window.ServerManMapLayers.clear();key(region,"Delete");check(objects().length===3&&text("map-tool-notice")==="Select an item first","Delete without a selection says so");
'''), "PASS")

    def test_circle_centre_fields(self) -> None:
        """A valid centre is one undo action; a blank, non-numeric or outside value changes nothing; read only disables."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + FIELDS + r'''
const start=plan();start.objects=[circle("C",1000,500,200),polygon("P",[[100,100],[400,100],[400,400]])];
await load(value(start));pick("C");
check(input("map-properties-centre-x").value==="1000"&&input("map-properties-centre-z").value==="500","the fields show the stored native values");
check(input("map-properties-centre-x").type==="text"&&input("map-properties-centre-x").inputMode==="decimal"&&input("map-properties-centre-z").getAttribute("aria-describedby")==="map-properties-message","text fields with a decimal keyboard and the message");
check(text("map-properties-centre-x-label")==="Centre X (m)"&&text("map-properties-centre-z-label")==="Centre Z (m)","labels");
change("map-properties-centre-x"," 1500.250 ");check(find("C").centre.x===1500.25&&input("map-properties-centre-x").value==="1500.25"&&find("C").centre.z===500&&entries()===1,"a valid X is one action");
change("map-properties-centre-z","0");check(find("C").centre.z===0&&entries()===2,"a centre on the bounds line is inside");
await model.undo();await model.undo();check(geometry("C")===JSON.stringify(start.objects[0])&&input("map-properties-centre-x").value==="1000","undo restores the centre and the field");
const refusal="Enter a centre inside the terrain bounds: X from 0 to 2000 m, Z from 0 to 1000 m. The circle stays where it is.";
for(const [id,value] of [["map-properties-centre-x","2000.5"],["map-properties-centre-z","-1"],["map-properties-centre-x",""],["map-properties-centre-z","abc"]]){
  change(id,value);check(geometry("C")===JSON.stringify(start.objects[0])&&entries()===2&&invalid(id)&&message()===refusal,`${id} "${value}" is refused with the bounds`);}
pick("P");check(input("map-properties-radius-row").hidden,"an outline has no centre or radius fields");
await load({...value(start),read_only:true,read_only_reason:"invalid_saved_data"});pick("C");
check(input("map-properties-centre-x").disabled&&input("map-properties-centre-z").disabled&&input("map-properties-radius").disabled&&input("map-properties-name-field").disabled&&input("map-properties-delete").disabled,"a read-only draft disables every property control");
'''), "PASS")


    def test_inspector_composition_survives_early_load(self) -> None:
        """Early host loading retains real report access beside capacity without placeholder text."""
        self.assertEqual(run_editor_harness(TOOLS + SELECT + r'''
const start=plan();start.objects=[polygon("P",[[100,100],[500,100],[500,500]])];
await load(value(start));pick("P");
const propertyFields=document.getElementById("map-properties-fields"),row=document.getElementById("map-zone-capacity-row");
check(!propertyFields.textContent.includes("null"),"early creation never inserts a missing node as text");
check(row.contains(document.getElementById("map-view-capacity-report")),"report access belongs to capacity");
check(document.getElementById("map-properties-points").textContent==="Points: 3","actual polygon point count");
const explanation=document.getElementById("map-zone-capacity-explanation"),details=explanation.closest("details");
check(details&&!details.open&&explanation.textContent.includes("not item supply"),"guidance is preserved in a disclosure");
details.querySelector("summary").click();check(details.open,"guidance remains reachable");
'''), "PASS")

if __name__ == "__main__":
    unittest.main()
