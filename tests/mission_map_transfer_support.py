"""Fake host and page helpers of the 9.1 Edge tests: Export plan and Import plan in the composed editor.

The fake host answers `export_plan`, `select_import_file`, `preview_import` and `import_plan` as the host module
does. A preview report has the D7 fields of `compatibility()`. A commit stores the imported plan, so the reload
after it reads the saved revision. The fake lane of the 8.18 tests stays behind it.
"""

from __future__ import annotations

import json

from tests.mission_map_publish_support import page

# Product Owner texts of 2026-10-17 17:14:17 in evidence 9.1 and the reused CLI texts, word for word (an
# independent copy)
WORDS = {
    "heading": "Plan file", "export": "Export plan…", "import": "Import plan…", "copy": "Copy from another profile…",
    "loading": "Import and copy are not available until the map plan is loaded.",
    "read_only": "Import and copy are not available because the map plan is read only.",
    "conflict": "Import and copy are not available because autosave stopped. Choose Export plan to keep your "
                "changes, then Reload plan.",
    "busy": "Import and copy are not available while a map operation runs.",
    "reading": "Wait until the current file or preview step ends.",
    "bounds": "Import and copy are not available until you confirm the terrain bounds.",
    "export_loading": "Export is not available until the map plan is loaded.",
    "exporting": "Exporting the map plan", "checking": "Checking the file", "importing": "Importing the map plan",
    "paused": "Editing and autosave are paused until the import ends. You can still move and zoom the map and "
              "select items.",
    "not_opened": "The import did not start because the map editor could not save the current draft.",
    "still_unsaved": "The plan in the window is still not saved. The file is the way to keep it.",
    "keep": "To keep your unsaved changes, choose Export plan first.",
    "stale": "Not imported: The saved map plan changed. Reload plan shows its current state. Nothing was changed.",
    "placement": "The terrain or the bounds differ. Check the placement of the selected objects.",
    "title": "Import map plan", "intro": "DayZ-ServerMan checked the file. Nothing is written to the mission files.",
    "legend": "How to add the objects", "placement_box": "I will check the placement of every object.",
    "confirm": "Import plan",
    "preview_stale": "The saved map plan can have changed after this preview. Preview again before you import.",
    "export_invalid": "Not exported: The map plan in the window is not valid. Nothing was written.",
    "unknown": "The map editor no longer has the chosen file. Nothing was changed. Choose Import plan to select "
               "the file again.",
    "no_valid_plan": "This profile has no valid saved map plan. Nothing was changed.",
    "written": "Map plan written to chosen.json.",
    "protected": "chosen.json is in a folder that DayZ-ServerMan uses: the DayZ server folder, the DayZ-ServerMan "
                 "folder or the backup folder. Name a file in another folder.",
    "not_written": "The file chosen.json could not be written correctly. DayZ-ServerMan removed it.",
    "placement_review": "The terrain or the bounds differ. Check the placement of every object before you continue.",
    "invalid_document": "The file is not a valid map plan.",
    "limit": "The result would hold 2001 objects and 0 polygon vertices. The limits are 2,000 objects and 50,000 "
             "vertices. Nothing was changed.",
}

# The fake host, the D7 report builder and the page helpers
HOST = r'''
const W=__WORDS__,selection="s".repeat(32);
const zone=(id,x=500,z=400)=>({id,name:`Zone ${id}`,kind:"loot_circle",visible:true,locked:false,centre:{x,z},radius:50,tier:null,abundance:"rich"});
const report=(mode,extra={})=>({mode,terrain_match:true,bounds_equal:true,bounds_difference:{x_min:0,z_min:0,width:0,height:0},
  imported_terrain_id:"synthetic",destination_terrain_id:"synthetic",object_count:1,insertion:mode==="replace"?"replace":"top",
  remapped:[],excluded:[],excluded_count:0,discarded:[],unmapped:[],global_abundance:{destination:0,imported:0,result:0},
  unavailable:[],result_object_count:mode==="replace"?1:2,result_vertex_count:0,refusal:null,placement_review:false,
  confirmation_required:false,destination_revision:4,...extra});
const host={calls:[],objects:[zone("imp")],export:{success:true,value:{file_name:"chosen.json"}},select:null,preview:null,
  commit:null,during:null};
const previews=(replace={},merge={},both={})=>({success:true,value:{selection_id:selection,file_name:"plan.json",
  previews:{replace:report("replace",{...both,...replace}),merge:report("merge",{...both,...merge})}}});
// A commit saves revision 5: the reload reads the imported objects at that revision
const committed=(mode)=>{const current=model.snapshot(),objects=mode==="replace"?host.objects:[...host.objects,...current.objects];
  editorAnswer={success:true,value:{...value({...current,objects}),revision:5}};
  return {success:true,value:{revision:5,report:report(mode)}}};
Object.assign(window.pywebview.api,{
  export_plan:async(document)=>{host.calls.push(["export",structuredClone(document)]);return host.export},
  select_import_file:async()=>{host.calls.push(["select"]);return host.select??previews()},
  preview_import:async(id)=>{host.calls.push(["preview",id]);return host.preview??host.select??previews()},
  import_plan:async(id,mode,revision,confirmed)=>{host.calls.push(["import",id,mode,revision,confirmed]);
    if(host.during)await host.during();return host.commit?host.commit(mode):committed(mode)},
});
const dialog=()=>document.querySelector("dialog.map-preview"),inside=(selector)=>[...(dialog()?.querySelectorAll(selector)??[])];
const button=(label)=>inside(".action-row button").find(item=>item.textContent===label);
const radio=(mode)=>dialog().querySelector(`input[type="radio"][value="${mode}"]`),boxes=()=>inside('input[type="checkbox"]');
const remaining=()=>dialog().querySelector(".map-preview-footer p[id]")?.textContent??"";
const confirmButton=()=>button(W.confirm);
const shownFor=async()=>{for(let index=0;index<60&&!dialog();index++)await wait();check(dialog(),"the import dialog opened")};
const imports=()=>host.calls.filter(call=>call[0]==="import");
const status=()=>text("map-status"),reasons=(id)=>text(`${id}-reason`);
// Open Import plan and wait for the dialog
const begin=async(answer=null)=>{host.select=answer;element("map-import").click();await shownFor()};
// Make autosave stop with the stale-revision refusal, with one unsaved object in the draft
const block=async()=>{window.pywebview.api.save_plan=async(document,revision)=>{saved.push({plan:structuredClone(document),revision});
    return {success:false,error:{code:"REVISION_CONFLICT",message:"stale",retryable:false,details:{reason:"stale_revision"}}}};
  model.add("Add circle",zone("unsaved",700,300));await sleep(1000);check(window.ServerManMapAutosave.blocked(),"autosave stopped")};
'''


def transfer_page(body: str, *, budget: int = 60000) -> str:
    """Run one editor harness with the fake lane and the fake transfer host before the body."""
    return page(HOST.replace("__WORDS__", json.dumps(WORDS)) + body, budget=budget)
