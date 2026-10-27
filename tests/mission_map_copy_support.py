"""Fake host and page helpers of the 9.2 Edge tests: Copy from another profile in the composed editor.

It adds `copy_sources`, `preview_copy` and `copy_plan` to the fake host of `mission_map_transfer_support`. A copy
preview has the D7 fields of `report()`. A commit stores the copied plan as the import support does, so the reload
reads the saved revision. The page names only the source profile; the fake host never sees a destination.
"""

from __future__ import annotations

import json

from tests.mission_map_transfer_support import WORDS, transfer_page

# Product Owner texts of 2026-10-17 17:14:17 in evidence 9.1, word for word (an independent copy)
COPY_WORDS = {
    "label": "Copy from another profile…", "checking": "Checking the source profile", "copying": "Copying the map plan",
    "paused": "Editing and autosave are paused until the copy ends. You can still move and zoom the map and select "
              "items.",
    "not_opened": "The copy did not start because the map editor could not save the current draft.",
    "stale": "Not copied: The saved map plan changed. Reload plan shows its current state. Nothing was changed.",
    "picker_title": "Choose a source profile", "legend": "Source profile", "continue": "Continue",
    "picker_intro": "DayZ-ServerMan copies the map plan of the profile that you choose. The source plan does not "
                    "change.",
    "picker_remaining": "Choose a source profile to continue.", "empty": "No other profile exists.",
    "picker_stale": "The profiles or the map plans can have changed after this list. Preview again before you "
                    "continue.",
    "self": "This is the profile that you edit now.", "no_mission": "This profile has no active mission link.",
    "title": "Copy map plan from Alpha PvE", "source": "Source profile: Alpha PvE. Mission: dayzOffline.enoch.",
    "intro": "DayZ-ServerMan checked the source plan. The source plan does not change. Nothing is written to the "
             "mission files.",
    "confirm": "Copy plan",
    "preview_stale": "The saved map plan or the source plan can have changed after this preview. Preview again before "
                     "you copy.",
    "no_plan": "The source profile has no valid saved map plan to copy. Nothing was changed.",
}

# The copy part of the fake host; `host.sources`, `host.copy_preview` and `host.copy_commit` replace an answer
COPY_HOST = r'''
const C=__COPY_WORDS__;
const profile=(id,name,mission,disabled=null)=>({profile_id:id,display_name:name,mission_root:mission,disabled});
const listing=(extra=[])=>({success:true,value:{profiles:[profile("self","This one","dayzOffline.chernarusplus","self"),
  profile("alpha","Alpha PvE","dayzOffline.enoch"),profile("nolink","No link",null,"no_mission"),...extra]}});
const copyPreviews=(replace={},merge={},both={})=>({success:true,value:{source_profile_id:"alpha",
  previews:{replace:report("replace",{...both,...replace}),merge:report("merge",{...both,...merge})}}});
Object.assign(host,{sources:null,copy_preview:null,copy_commit:null});
Object.assign(window.pywebview.api,{
  copy_sources:async()=>{host.calls.push(["sources"]);return host.sources??listing()},
  preview_copy:async(id)=>{host.calls.push(["preview_copy",id]);return host.copy_preview?host.copy_preview(id):copyPreviews()},
  copy_plan:async(id,mode,revision,confirmed)=>{host.calls.push(["copy",id,mode,revision,confirmed]);
    if(host.during)await host.during();return host.copy_commit?host.copy_commit(mode):committed(mode)},
});
const copies=()=>host.calls.filter(call=>call[0]==="copy");
const copyButton=()=>element("map-copy");
const openPicker=async()=>{copyButton().click();await shownFor()};
// Choose a source in the picker and continue; the compatibility dialog replaces the picker
const pick=async(id="alpha")=>{radio(id).click();button(C.continue).click();
  for(let index=0;index<60&&!button(C.confirm);index++)await wait();await wait()};
const copyFlow=async(mode)=>{radio(mode).click();boxes().forEach(box=>box.click());button(C.confirm).click();
  for(let index=0;index<60&&(copies().length===0||text("map-operation-status")===C.copying);index++)await wait();await wait()};
const refusal=(reason,message,code="INVALID_REQUEST")=>({success:false,error:{code,message,retryable:false,details:{reason}}});
'''


def copy_page(body: str) -> str:
    """Run one editor harness with the fake lane, the fake transfer host and the fake copy host before the body."""
    return transfer_page(COPY_HOST.replace("__COPY_WORDS__", json.dumps(COPY_WORDS)) + body)


__all__ = ["COPY_WORDS", "WORDS", "copy_page"]
