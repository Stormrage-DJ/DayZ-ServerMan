"""Verify the linked loot controls of Properties in composed Edge: the checkbox, its note, Detach and its notice (7.4)."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_linked_loot_msedge import LINKED
from tests.ui_harness_support import EDGE, run_editor_harness


# The Product Owner texts of 2026-10-14 12:28:40 and helpers for the controls of the UI-UX entry of 12:30:09
CONTROLS = LINKED + r'''
const box=()=>field("map-linked-loot-enable"),detachButton=()=>field("map-linked-loot-detach");
const note="Linked loot uses the centre and spawn radius of this encounter. It does not depend on kills or on what the encounter spawns.";
const toggle=()=>box().click();
const tabOrder=()=>[...document.querySelectorAll("#map-properties-fields input, #map-properties-fields select, #map-properties-fields button")]
  .filter(item=>item.offsetParent!==null&&!item.closest("#map-tier-actions")).map(item=>item.id.replace(/^map-(properties-|encounter-|linked-loot-)?/,"")).join();
const shown=(id)=>!field(id).hidden&&visible(id);
const notice=()=>[text("map-tool-message"),text("map-tool-notice"),text("map-announcement")];
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorLinkedLootControlsEdgeTests(unittest.TestCase):
    """Use synthetic plans, capability snapshots, fake save answers and isolated headless profiles."""

    def test_checkbox_label_note_rows_focus_and_tab_order(self) -> None:
        """A new encounter is clear; a check is one action, keeps the focus and shows the rows; a clear hides them."""
        self.assertEqual(run_editor_harness(CONTROLS + r'''
await loadWith(plan());tool("encounter");click(1000,500);const id=objects()[0].id;pick(id);
check(find(id).linked_loot.enabled===false&&!box().checked&&shown("map-linked-loot"),"a placement does not enable linked loot");
check(!shown("map-tier-row")&&!shown("map-zone-capacity-row")&&!shown("map-linked-loot-detach"),"a new encounter: no tier, no Zone capacity, no Detach");
check(text("map-linked-loot-enable-label")==="Link loot to this encounter"&&box().getAttribute("aria-describedby")==="map-linked-loot-note"&&text("map-linked-loot-note")===note&&visible("map-linked-loot-note"),"the label, the note and its link");
const ids=[...document.querySelectorAll("#map-properties-fields > *")].map(item=>item.id);
const place=(name)=>ids.indexOf(name);
// The inspector puts the shared loot settings before the geometry and provider fields; Detach stays with the actions at the end
check(place("map-properties-name-field")<place("map-linked-loot")&&place("map-linked-loot")<place("map-tier-row")&&place("map-tier-row")<place("map-zone-capacity-row")&&place("map-zone-capacity-row")<place("map-properties-radius-row")&&place("map-properties-radius-row")<place("map-encounter-fields")&&place("map-encounter-fields")<place("map-linked-loot-detach")&&place("map-linked-loot-detach")<place("map-properties-message")&&place("map-properties-message")<place("map-properties-delete"),"DOM order "+ids);
check(tabOrder()==="name-field,enable,centre-x,centre-z,radius,event,smin,smax,dmin,dmax,delete","Tab order with linked loot off: "+tabOrder());
check(box().offsetWidth<40&&getComputedStyle(field("map-linked-loot")).borderTopStyle==="solid","the checkbox keeps its size; a top line starts the group");
field("map-announcement").textContent="";const length=position();box().focus();toggle();
check(find(id).linked_loot.enabled&&find(id).linked_loot.abundance==="rich"&&find(id).linked_loot.tier===null&&position()===length+1,"a check is one action: Rich and Inherit");
check(document.activeElement===box()&&box().checked&&text("map-announcement")===""&&visible("map-linked-loot-note"),"the focus stays, nothing is announced, the note stays");
check(shown("map-tier-row")&&shown("map-zone-capacity-row")&&shown("map-linked-loot-detach")&&text("map-linked-loot-detach")==="Detach loot zone","the rows and Detach appear");
check(tabOrder()==="name-field,enable,tier-select,zone-capacity,view-capacity-report,centre-x,centre-z,radius,event,smin,smax,dmin,dmax,detach,delete","Tab order with linked loot on: "+tabOrder());
check(detachButton().classList.contains("button")&&!detachButton().classList.contains("button-danger")&&getComputedStyle(detachButton()).justifySelf==="start","Detach is a plain button");
toggle();check(!find(id).linked_loot.enabled&&field("map-tier-row").hidden&&field("map-zone-capacity-row").hidden&&detachButton().hidden&&!field("map-zone-capacity").disabled,"a clear hides the rows and Detach, without disabling them");
check(document.activeElement===box()&&position()===length+2,"one action; the focus stays");
await model.undo();check(box().checked&&shown("map-tier-row")&&document.activeElement===box(),"undo restores the check; the focus stays");
await model.undo();check(!box().checked&&!shown("map-tier-row")&&position()===length,"undo restores the clear state");
''', budget=20000), "PASS")

    def test_every_encounter_state_lock_read_only_and_no_radius(self) -> None:
        """The checkbox shows for unavailable, excluded, hidden and wolf encounters; locked and read-only disable it."""
        self.assertEqual(run_editor_harness(CONTROLS + r'''
const start=plan();start.objects=[encounter("U1",1000,500,100,{properties:{event_name:"InfectedGone",smin:0,smax:0,dmin:3,dmax:5}}),
  encounter("X1",600,500,100,{excluded:true}),encounter("H1",1400,500,100,{visible:false}),
  encounter("W1",1700,700,60,{provider:"native_wolf",properties:{pack:3}}),bandit("B1",300,300),
  encounter("B2",300,700,null,{provider:"ai_bandits",properties:{},linked_loot:{enabled:true,tier:null,abundance:"normal",radius:150}})];
await loadWith(start);
for(const id of ["U1","X1","H1","W1"]){pick(id);const length=position();
  check(shown("map-linked-loot")&&!box().checked&&!box().disabled,`${id}: the checkbox shows`);
  const excluded=find(id).excluded;toggle();
  check(find(id).linked_loot.enabled&&find(id).excluded===excluded&&position()===length+1&&shown("map-linked-loot-detach"),`${id}: a check does not change Exclude`);}
pick("X1");check(text("map-encounter-next")==="Excluded: Apply does not write this encounter to the mission. Its linked loot is still applied.","the excluded note follows the checkbox");
toggle();check(text("map-encounter-next")==="Excluded: Apply does not write this encounter to the mission.","a clear removes the addition");
pick("U1");field("map-encounter-exclude").click();check(find("U1").excluded&&box().checked&&find("U1").linked_loot.enabled,"Exclude does not change the checkbox");
pick("W1");check(tabOrder()==="exclude,name-field,enable,tier-select,zone-capacity,view-capacity-report,centre-x,centre-z,radius,detach,delete","a wolf encounter: "+tabOrder());
model.lock("W1");check(box().disabled&&box().checked&&detachButton().disabled,"a locked encounter: disabled, the check mark stays");
pick("B1");check(!shown("map-linked-loot")&&!shown("map-linked-loot-detach"),"no checkbox without a spawn radius");
pick("B2");check(!shown("map-linked-loot")&&shown("map-tier-row")&&shown("map-zone-capacity-row")&&shown("map-linked-loot-detach"),"stored enabled loot without a spawn radius: rows and Detach, no checkbox");
detachButton().focus();detachButton().click();
check(document.activeElement===region&&!find("B2").linked_loot.enabled&&notice().every(line=>line==="Loot circle 1 detached from Encounter B2. Undo links them again."),"no checkbox after Detach: the focus moves to the map");
check(find(objects()[objects().findIndex(item=>item.id==="B2")-1].id).radius===150,"the circle has the stored loot radius");
const readOnly=value(start);readOnly.capabilities.encounter_providers=providers();readOnly.read_only=true;readOnly.read_only_reason="invalid_saved_data";
readOnly.plan.objects[1].linked_loot.enabled=true;await load(readOnly);pick("X1");
check(box().disabled&&box().checked&&detachButton().disabled&&shown("map-linked-loot-detach"),"a read-only draft disables the checkbox and Detach");
'''), "PASS")

    def test_detach_focus_notice_undo_and_refusal(self) -> None:
        """Detach keeps the encounter selected, moves the focus to the clear checkbox, then gives the notice."""
        self.assertEqual(run_editor_harness(CONTROLS + r'''
const start=plan();start.objects=[circle("Z0",300,300,50),linkedLoot("E1",1000,500,200,{tier:"Tier2",abundance:"very_rich"})];
await load(loaded(start));pick("E1");const length=position();
let spoken=[];new MutationObserver(()=>spoken.push(text("map-announcement"))).observe(field("map-announcement"),{childList:true,characterData:true,subtree:true});
// Record the focus at the moment the map notice is written, so the order of focus and notice is visible
let focusAtNotice=null;const textContent=Object.getOwnPropertyDescriptor(Node.prototype,"textContent");
Object.defineProperty(field("map-tool-notice"),"textContent",{get(){return textContent.get.call(this)},
  set(value){if(value&&focusAtNotice===null)focusAtNotice=document.activeElement?.id;textContent.set.call(this,value)}});
detachButton().focus();detachButton().click();await wait();
const made=objects()[1];const expected=`${made.name} detached from Encounter E1. Undo links them again.`;
check(made.kind==="loot_circle"&&made.name==="Loot circle 1"&&objects()[2].id==="E1"&&position()===length+1,"one action; the circle is directly above the encounter");
check(selected()==="E1"&&document.activeElement===box()&&!box().checked,"the encounter stays selected; the focus is on the clear checkbox");
check(focusAtNotice==="map-linked-loot-enable","the focus moves before the notice");
check(notice().every(line=>line===expected)&&spoken.includes(expected),"the notice in the panel, the map and the live region: "+notice().join("|"));
const rows=[...document.querySelectorAll("#map-list li")].map(item=>item.dataset.mapObject);
check(rows.indexOf(made.id)===rows.indexOf("E1")-1,"the circle row is directly above the encounter row");
key(box(),"z",{ctrlKey:true});await wait();
check(box().checked&&find("E1").linked_loot.enabled&&!find(made.id)&&document.activeElement===box()&&position()===length,"Ctrl+Z on the checkbox undoes Detach; the focus stays");
model.lock("E1");detachButton().disabled=false;detachButton().focus();detachButton().click();
check(notice().every(line=>line==="Unlock the object before editing its properties or geometry.")&&document.activeElement===detachButton()&&objects().length===2,"a refusal is announced; the focus stays on Detach");
'''), "PASS")

    def test_bear_refused_check_and_loot_zone(self) -> None:
        """A bear links loot; a refused check shows its reason only in Properties; a loot zone renders no controls."""
        self.assertEqual(run_editor_harness(CONTROLS + r'''
const start=plan();start.objects=[encounter("B1",1700,700,60,{provider:"native_bear",properties:{pack:2}}),encounter("E1",1000,500,100),
  circle("Z1",400,400,80)];await loadWith(start);
pick("B1");let length=position();
check(shown("map-linked-loot")&&!box().checked&&!box().disabled,"a bear encounter shows the checkbox");
toggle();check(JSON.stringify(find("B1").linked_loot)===JSON.stringify({enabled:true,tier:null,abundance:"rich",radius:null})&&position()===length+1&&shown("map-linked-loot-detach"),"a bear check is one action: Rich and Inherit");
pick("E1");model.lock("E1");length=position();
field("map-announcement").textContent="";field("map-tool-notice").textContent="";field("map-properties-message").textContent="";
box().disabled=false;box().focus();box().click();
check(text("map-properties-message")==="Unlock the object before editing its properties or geometry."&&!find("E1").linked_loot.enabled&&position()===length,"a refused check shows the lock text in Properties and changes nothing");
check(!box().checked&&box().disabled,"the checkbox shows the stored state again");
check(text("map-announcement")===""&&text("map-tool-notice")==="","a refused check is not announced");
pick("Z1");
check(getComputedStyle(field("map-linked-loot")).display==="none"&&getComputedStyle(detachButton()).display==="none"&&!visible("map-linked-loot-enable"),"a loot zone renders no checkbox block and no Detach");
'''), "PASS")

    def test_controls_fit_at_both_window_sizes(self) -> None:
        """The label and the note wrap in the panel; there is no horizontal scroll."""
        body = CONTROLS + r'''
const start=plan();start.objects=[linkedLoot("E1",1000,500,200,{},{excluded:true,properties:{event_name:"InfectedGone",smin:0,smax:0,dmin:3,dmax:5}})];
await load(loaded(start));pick("E1");field("map-linked-loot-enable").scrollIntoView();
const panel=document.getElementById("map-properties-fields");
check(shown("map-linked-loot")&&shown("map-linked-loot-detach")&&box().offsetWidth<40,"the controls show; the checkbox is not stretched");
check(panel.scrollWidth<=panel.clientWidth&&document.documentElement.scrollWidth<=document.documentElement.clientWidth,"no horizontal scroll");
'''
        for window_size in ("936,508", "1256,708"):
            with self.subTest(window_size=window_size):
                self.assertEqual(run_editor_harness(body, window_size=window_size), "PASS")


if __name__ == "__main__":
    unittest.main()
