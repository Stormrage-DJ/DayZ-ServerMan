"""Verify Zone capacity, the Global container capacity slider and the effective texts in composed Edge (5.1)."""

from __future__ import annotations

import unittest

from tests.test_mission_map_editor_select_msedge import SELECT
from tests.test_mission_map_editor_tools_msedge import TOOLS
from tests.ui_harness_support import EDGE, run_editor_harness


# A fake backend evaluation (D9) for load and save answers; the window must only show its numbers.
ABUNDANCE = TOOLS + SELECT + r'''
const percent={sparse:-25,normal:0,rich:50,very_rich:100},labels={sparse:"Sparse -25%",normal:"Normal 0%",rich:"Rich +50%",very_rich:"Very Rich +100%"};
const steps=[-50,-25,0,25,50,75,100],signed=(n)=>`${n>0?"+":""}${n}%`;
const evaluate=(document,revision)=>({revision,global:document.global_abundance,objects:document.objects.map(item=>({id:item.id,
  available:true,reason:null,zone_adjustment:percent[item.abundance],effective_adjustment:document.global_abundance+percent[item.abundance],
  tier:item.tier,tier_writable:true,abundance_only:item.tier===null}))});
const evaluated=(document)=>({...value(document),evaluation:evaluate(document,4)});
window.pywebview.api.save_plan=async(document,revision,selection)=>{saved.push({plan:structuredClone(document),revision,selection});
  const answer=success(revision===null?0:revision+1);answer.value.evaluation=evaluate(document,answer.value.revision);return answer};
const sleep=(ms)=>new Promise(resolve=>setTimeout(resolve,ms));
const slider=()=>document.getElementById("map-global-capacity"),select=()=>document.getElementById("map-zone-capacity");
const effective=()=>text("map-zone-capacity-effective"),pending=()=>document.getElementById("map-zone-capacity-effective").classList.contains("map-effective-pending");
const slide=(number,release=true)=>{slider().value=String(number);slider().dispatchEvent(new Event("input"));if(release)slider().dispatchEvent(new Event("change"))};
const preset=(name)=>{select().value=name;select().dispatchEvent(new Event("change"))};
const explanation="Percentages change native container capacity, not item supply. More capacity may leave the live loot amount unchanged. Fixed dispatch loot is unchanged. Preview shows the supported capacity changes and native limits.";
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorAbundanceEdgeTests(unittest.TestCase):
    """Use synthetic plans, fake load and save answers and isolated headless profiles."""

    # Show the whole browser failure text, so a CI annotation holds every diagnostic value
    maxDiff = None

    def test_controls_presets_steps_and_every_combination_from_the_backend(self) -> None:
        """Labels, explanation, four presets and seven steps; every combination shows the backend values."""
        self.assertEqual(run_editor_harness(ABUNDANCE + r'''
const start=plan();start.objects=[circle("A",500,500,100)];await load(evaluated(start));
check(text("map-global-capacity-title")==="Global container capacity"&&text("map-zone-capacity-label")==="Zone capacity","the control labels");
check(text("map-global-capacity-explanation")===explanation&&text("map-zone-capacity-explanation")===explanation,"the exact explanation at both controls");
check(slider().min==="-50"&&slider().max==="100"&&slider().step==="25"&&slider().value==="0"&&!slider().disabled,"seven steps from -50% to +100%, at 0%");
check(text("map-global-capacity-value")==="0%"&&slider().getAttribute("aria-valuetext")==="0%","the slider value as text");
check(document.getElementById("map-zone-capacity-row").hidden,"no Zone capacity without a selected zone");
pick("A");check(!document.getElementById("map-zone-capacity-row").hidden&&select().value==="rich"&&!select().disabled,"the selected zone shows its preset");
check(JSON.stringify([...select().options].map(o=>[o.value,o.textContent]))===JSON.stringify(Object.entries(labels)),"four presets in order with their labels");
check(effective()==="Capacity change: Rich +50%; with global 0%: +50%"&&!pending(),"the effective text of the saved revision");
for(const global of steps)for(const name of Object.keys(percent)){const combination=plan();combination.global_abundance=global;
  combination.objects=[circle("A",500,500,100,{abundance:name})];await load(evaluated(combination));pick("A");
  const expected=`Capacity change: ${labels[name]}; with global ${signed(global)}: ${signed(global+percent[name])}`;
  check(effective()===expected&&!pending()&&slider().value===String(global)&&text("map-global-capacity-value")===signed(global),`${global} ${name}: ${effective()}`)}
for(const [global,name,expected] of [[0,"rich","Capacity change: Rich +50%; with global 0%: +50%"],[100,"very_rich","Capacity change: Very Rich +100%; with global +100%: +200%"],
  [-50,"rich","Capacity change: Rich +50%; with global -50%: 0%"],[-50,"sparse","Capacity change: Sparse -25%; with global -50%: -75%"],[100,"rich","Capacity change: Rich +50%; with global +100%: +150%"]]){
  const row=plan();row.global_abundance=global;row.objects=[circle("A",500,500,100,{abundance:name})];
  await load(evaluated(row));pick("A");check(effective()===expected,`R08 row ${expected}: ${effective()}`)}
const odd=evaluated(start);odd.evaluation.global=7;odd.evaluation.objects[0].effective_adjustment=999;await load(odd);pick("A");
check(effective()==="Capacity change: Rich +50%; with global +7%: +999%","the window shows the backend numbers and computes nothing");
await load(value(start));pick("A");check(effective()==="Capacity change: Not evaluated"&&pending(),"no evaluation: Not evaluated, muted");
''', budget=20000), "PASS")

    def test_preset_and_global_edits_are_undo_actions_and_wait_for_the_backend(self) -> None:
        """Each preset choice and each slider release is one undo action; Updating until the save answer."""
        self.assertEqual(run_editor_harness(ABUNDANCE + r'''
const start=plan();start.objects=[circle("A",500,500,100)];await load(evaluated(start));pick("A");
preset("very_rich");check(find("A").abundance==="very_rich"&&entries()===1,"a preset choice is one action");
check(effective()==="Capacity change: Updating"&&pending()&&text("map-list").includes("Zone capacity: Very Rich +100%"),"Updating until the save; the list shows the stored preset");
await sleep(900);check(saved.length===1&&saved[0].plan.objects[0].abundance==="very_rich","autosave sends the preset");
check(effective()==="Capacity change: Very Rich +100%; with global 0%: +100%"&&!pending(),"the save answer gives the effective text");
slide(100,false);check(text("map-global-capacity-value")==="+100%"&&model.snapshot().global_abundance===0&&entries()===1,"moving the slider is not yet an edit");
slider().dispatchEvent(new Event("change"));check(model.snapshot().global_abundance===100&&entries()===2&&effective()==="Capacity change: Updating","the release is one action");
await sleep(900);check(saved.length===2&&saved[1].plan.global_abundance===100&&effective()==="Capacity change: Very Rich +100%; with global +100%: +200%","saved global and its effective text");
key(slider(),"z",{ctrlKey:true});await wait();
check(model.snapshot().global_abundance===0&&slider().value==="0"&&text("map-global-capacity-value")==="0%"&&position()===1,"Ctrl+Z on the slider undoes the global edit");
check(effective()==="Capacity change: Updating","the undo waits for the backend");await sleep(900);
check(saved.length===3&&saved[2].plan.global_abundance===0&&effective()==="Capacity change: Very Rich +100%; with global 0%: +100%","undo saved");
await model.redo();check(model.snapshot().global_abundance===100&&slider().value==="100"&&text("map-global-capacity-value")==="+100%","redo restores it");
await model.undo();await model.undo();check(find("A").abundance==="rich"&&select().value==="rich"&&position()===0,"undo of the preset");
for(const step of steps){slide(step);check(model.snapshot().global_abundance===step&&text("map-global-capacity-value")===signed(step),`step ${step}`)}
check(entries()===7&&position()===7,"each of the seven steps is one action");slide(100);check(entries()===7,"an unchanged release adds no action");
for(let index=0;index<7;index++)await model.undo();
check(model.snapshot().global_abundance===0&&slider().value==="0"&&!model.status().history.undo,"seven undos reach the start");
await sleep(900);check(effective()==="Capacity change: Rich +50%; with global 0%: +50%","the plan equals the saved revision again");
''', budget=20000), "PASS")

    def test_default_refusal_locked_and_read_only(self) -> None:
        """A new zone is Rich; a refused save says Not evaluated; a locked zone and a read-only draft cannot change."""
        self.assertEqual(run_editor_harness(ABUNDANCE + r'''
const start=plan();start.objects=[circle("L",1600,500,100,{locked:true}),polygon("P",[[100,100],[400,100],[400,400]],{abundance:"sparse"}),
  {id:"E",kind:"encounter",name:"Encounter 1",visible:true,locked:false,centre:{x:900,z:500},radius:40,provider:"native_infected",
  properties:{event_name:"InfectedVillage",smin:1,smax:2,dmin:3,dmax:4},excluded:false,linked_loot:{enabled:false,tier:null,abundance:"rich",radius:null}}];
await load(evaluated(start));pick("P");check(select().value==="sparse"&&effective()==="Capacity change: Sparse -25%; with global 0%: -25%","a loot polygon has Zone capacity");
pick("E");check(document.getElementById("map-zone-capacity-row").hidden,"an encounter has no Zone capacity select here (linked loot is task 7)");
tool("circle");click(500,400);const id=objects()[0].id;pick(id);
check(find(id).abundance==="rich"&&select().value==="rich"&&effective()==="Capacity change: Updating","a new zone is Rich and waits for the backend");
window.pywebview.api.save_plan=async()=>refuse(id,"the circle radius is too large");await sleep(900);
check(effective()==="Capacity change: Not evaluated. Loot circle 1: the circle radius is too large"&&pending(),"a refused save: Not evaluated with the reason");
pick("L");check(select().disabled&&select().value==="rich"&&!slider().disabled,"a locked zone has no preset choice; the global slider stays");
const readOnly=evaluated(start);Object.assign(readOnly,{read_only:true,read_only_reason:"mission_path_changed"});await load(readOnly);pick("L");
check(slider().disabled&&select().disabled,"a read-only draft disables both controls");
check(model.setGlobal("Change global container capacity",50)===false&&model.snapshot().global_abundance===0,"the model refuses a read-only global edit");
await load(value(null));check(slider().disabled&&slider().value==="0","no plan: the slider is disabled at 0%");
''', budget=20000), "PASS")

    def test_held_slider_refused_release_backend_values_and_pending_style(self) -> None:
        """A held slider keeps its place; a refused release returns; only integer values show; Updating is muted."""
        self.assertEqual(run_editor_harness(ABUNDANCE + r'''
const start=plan();start.objects=[circle("A",500,500,100)];await load(evaluated(start));pick("A");
check(slider().getAttribute("aria-describedby")==="map-global-capacity-explanation","the slider names the explanation");
check(select().getAttribute("aria-describedby")==="map-zone-capacity-effective map-zone-capacity-explanation","the select names its texts");
// A held slider matches :active; the save answer of an earlier edit renders during the hold
preset("normal");const s=slider();let held=true;
s.matches=(query)=>query===":active"?held:Element.prototype.matches.call(s,query);
s.value="75";s.dispatchEvent(new Event("input"));await sleep(1000);
check(saved.length===1&&saved[0].plan.objects[0].abundance==="normal"&&saved[0].plan.global_abundance===0,"the preset saved during the hold");
check(effective()==="Capacity change: Normal 0%; with global 0%: 0%","the save answer rendered during the hold");
check(s.value==="75"&&text("map-global-capacity-value")==="+75%","the held slider keeps its position");
// The next save waits for its answer, so the text shows the state while the save runs
const waiting=[];window.pywebview.api.save_plan=(document,revision)=>{saved.push({plan:structuredClone(document),revision});
  return new Promise(resolve=>waiting.push(()=>{const answer=success(revision+1);answer.value.evaluation=evaluate(document,answer.value.revision);resolve(answer)}))};
held=false;s.dispatchEvent(new Event("change"));check(model.snapshot().global_abundance===75&&entries()===2,"the release is the edit");
const line=document.getElementById("map-zone-capacity-effective"),subtle=document.createElement("span");
subtle.style.color="var(--color-text-subtle)";document.body.append(subtle);
const muted=()=>getComputedStyle(line).color===getComputedStyle(subtle).color;
check(effective()==="Capacity change: Updating"&&muted()&&!line.closest("[aria-live]"),`Updating in the wait: muted and not a live region: ${effective()} | ${getComputedStyle(line).color} vs ${getComputedStyle(subtle).color} | ${line.closest("[aria-live]")?.id} | class:${line.className} | matches:${line.matches("#map-zone-capacity-effective.map-effective-pending")} | rules:${[...document.styleSheets].flatMap(sheet=>[...sheet.cssRules]).filter(rule=>rule.selectorText?.includes("map-zone-capacity-effective")).map(rule=>rule.cssText).join(" ")} | panel:${document.getElementById("map-panel").open} | visible:${line.checkVisibility()} | after a forced restyle:${(line.style.display="none",line.offsetHeight,line.style.display="",getComputedStyle(line).color)}`);
await sleep(900);check(waiting.length===1&&saved.at(-1).plan.global_abundance===75&&effective()==="Capacity change: Updating"&&muted(),"Updating while the save runs");
waiting.shift()();await sleep(50);check(effective()==="Capacity change: Normal 0%; with global +75%: +75%"&&!muted(),"the answer shows in the normal colour");
// A release while a dialog is open is refused, and the slider shows the draft value again
const dialog=document.createElement("dialog");document.body.append(dialog);dialog.show();
s.value="25";s.dispatchEvent(new Event("input"));s.dispatchEvent(new Event("change"));
check(entries()===2&&model.snapshot().global_abundance===75,"a release while a dialog is open is refused");
check(s.value==="75"&&text("map-global-capacity-value")==="+75%","the slider shows the draft value again");
dialog.remove();
// Only integer backend values show
const text_global=evaluated(start);text_global.evaluation.global="50";await load(text_global);pick("A");
check(effective()==="Capacity change: Not evaluated","a global that is not an integer is not shown");
const fraction=evaluated(start);fraction.evaluation.objects[0].effective_adjustment=50.5;await load(fraction);pick("A");
check(effective()==="Capacity change: Not evaluated","an effective value that is not an integer is not shown");
''', budget=20000), "PASS")


if __name__ == "__main__":
    unittest.main()
