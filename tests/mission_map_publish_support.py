"""Fake lane, previews and page helpers of the 8.18 Edge tests: Apply and Restore run from the editor window.

The fake lane keeps records and state events as the operation lane does. A cursor older than the retained
events gives EVENT_CURSOR_EXPIRED. A cursor past the newest event gives INVALID_REQUEST. The previews are the
8.10 test answers, with no required choice in the Apply answer.
"""

from __future__ import annotations

import copy
import json

from dayz_serverman.application.mission_map_recovery import RECOVERY_UNRESOLVED
from dayz_serverman.domain.mission_map_preview_texts import PREVIEW_STALE_TEXT
from dayz_serverman.domain.mission_map_restore_texts import RESTORE_STALE_TEXT, TARGET_CHANGED_TEXT
from tests.test_mission_map_editor_tools_msedge import TOOLS
from tests.test_mission_map_preview_dialog_msedge import APPLY
from tests.test_mission_map_preview_restore_msedge import ORIGINAL, PREVIOUS
from tests.ui_harness_support import run_editor_harness

# An Apply answer that needs no choice, so its confirm button is available at once
READY = copy.deepcopy(APPLY)
READY.update(choices={"external_changes": [], "unavailable_encounters": []}, external_changes=[])
READY["encounters"].update(unavailable=[], excluded=[])
# The host text of a late file change: the Apply commit sentence and the 8.8 restore text
APPLY_LATE = ("Apply stopped because cfgeventspawns.xml changed after the preview. No change was kept, and that file "
              "keeps its current content. Review the preview again.")
RESTORE_LATE = TARGET_CHANGED_TEXT.format(path="cfgeventspawns.xml")

# Product Owner texts of 2026-10-15 15:57:10 and 16:03:40 in evidence 8.10, word for word (an independent copy)
TEXTS = {
    "apply": "Apply…", "restore": "Restore", "previous": "Restore previous state…", "original": "Restore original…",
    "term": "Map operation", "reading": "Preparing the preview", "check": "Check again",
    "paused_apply": "Editing and autosave are paused until Apply ends. You can still move and zoom the map and "
                    "select items. Closing the map editor does not stop Apply.",
    "paused_restore": "Editing and autosave are paused until the restore ends. You can still move and zoom the map "
                      "and select items. Closing the map editor does not stop the restore.",
    "cleared": "No unfinished mission map change is left. Preview again before you apply or restore.",
    "deferred": "DayZ-ServerMan cannot check the unfinished mission map change while another operation runs. Check "
                "again when that operation ends.",
    "not_opened": "The Apply preview did not open because the map editor could not save the current draft. Apply "
                  "uses only the saved map plan.",
    # Product Owner 2026-10-16 15:50:59 in evidence 8.18
    "reloaded": "The saved map plan changed, so the map editor loaded it again. Undo and Redo start again from this plan.",
    "late_apply": PREVIEW_STALE_TEXT, "late_restore": RESTORE_LATE, "stale_restore": RESTORE_STALE_TEXT,
    "apply_late_host": APPLY_LATE, "recovery_unresolved": RECOVERY_UNRESOLVED,
    # Product Owner 2026-10-17 14:50:44 in evidence 8.11: the catalogue sentence of the unresolved recovery block
    "unresolved_text": "A mission map change did not finish, and its files cannot be recovered safely. Check Manager "
                       "diagnostics. Then select Check again in the map editor, or restart DayZ-ServerMan; it checks "
                       "again when it starts.",
}

# The fake lane and the page helpers; TOOLS gives the loaded plan, clicks and the model
LANE = TOOLS + r'''
const T=__TEXTS__,sleep=(ms)=>new Promise(resolve=>setTimeout(resolve,ms));
const lane={events:[],records:new Map(),session:"s1",sequence:0,floor:1,calls:[],refusal:null,hold:null,
  recovery:{success:true,value:{blocked:false,diagnostics:[]}},
  previews:{apply:{success:true,value:__READY__},previous:{success:true,value:__PREVIOUS__},original:{success:true,value:__ORIGINAL__}}};
const fail=(code,message,details)=>({success:false,error:{code,message,retryable:false,...(details?{details}:{})}});
const record=(id,kind,state,extra={})=>({schema_version:1,revision:0,operation_id:id,kind,state,accepted_at:"2026-10-16T10:00:00Z",
  started_at:null,finished_at:null,cancellation_requested:false,progress_percent:0,progress_phase:state==="RUNNING"?"running":"accepted",
  result:null,terminal_error:null,progress_detail:null,target_profile_id:"alpha",last_working_phase:null,...extra});
// Store a record with the next revision and append its state event
const emit=(value)=>{lane.sequence+=1;const next={...value,revision:lane.sequence};lane.records.set(next.operation_id,next);
  lane.events.push({schema_version:1,session_id:lane.session,sequence:lane.sequence,operation_id:next.operation_id,
    occurred_at:"2026-10-16T10:00:00Z",kind:"state",payload:{state:next.state}});return next};
// A held call waits until release() is called
const held=async()=>{if(lane.hold){await new Promise(resolve=>{lane.release=resolve;});lane.hold=null;}};
Object.assign(window.pywebview.api,{
  operation_events:async(after)=>{lane.calls.push(["events",after]);const kept=lane.events.filter(event=>event.sequence>=lane.floor);
    if(!Number.isInteger(after)||after<0||after>lane.sequence)return fail("INVALID_REQUEST","event cursor exceeds the current sequence");
    if(kept.length?after<kept[0].sequence-1:lane.sequence>after)return fail("EVENT_CURSOR_EXPIRED","event cursor is older than retained history");
    const page=kept.filter(event=>event.sequence>after).slice(0,100);
    return {success:true,value:{session_id:lane.session,events:page,next_cursor:page.length?page.at(-1).sequence:Math.max(after,lane.sequence)}}},
  operation:async(id)=>{lane.calls.push(["operation",id]);const value=lane.records.get(id);
    return value?{success:true,value:structuredClone(value)}:fail("NOT_FOUND",`operation ${id} was not found`)},
  check_recovery:async()=>{lane.calls.push(["check"]);await held();return lane.recovery},
  preview_plan:async()=>{lane.calls.push(["preview","apply"]);await held();return lane.previews.apply},
  preview_restore:async(mode)=>{lane.calls.push(["preview",mode]);await held();return lane.previews[mode]},
  apply_plan:async(fingerprint,choices)=>{lane.calls.push(["apply",fingerprint,choices]);return submit("APPLY_MISSION_MAP")},
  restore_mission:async(mode,fingerprint)=>{lane.calls.push(["restore",mode,fingerprint]);
    return submit(mode==="previous"?"RESTORE_MISSION_MAP_PREVIOUS":"RESTORE_MISSION_MAP_ORIGINAL")},
});
const submit=(kind)=>{if(lane.refusal)return lane.refusal;const queued=emit(record(`own${lane.sequence}`,kind,"QUEUED"));
  return {success:true,value:{operation_id:queued.operation_id,state:queued.state}}};
// The follower reads every 1.5 s; one call of poll() lets one read pass
const poll=async()=>{await sleep(1600);await wait()};
const element=(id)=>document.getElementById(id),slot=()=>text("map-operation-status"),notice=()=>text("map-operation-notice");
const looks=()=>[...element("map-operation-status").classList].join(),noticeLook=()=>element("map-operation-notice").className;
const shown=()=>document.querySelector("dialog.map-preview");
const action=(label)=>[...(shown()?.querySelectorAll(".action-row button")??[])].find(button=>button.textContent===label);
const opened=async()=>{for(let index=0;index<50&&!shown();index++)await wait();check(shown(),"the preview opened")};
// Activate an opener: Apply, or Restore and then a mode button
const activate=(mode)=>{if(mode==="apply")element("map-apply").click();
  else{element("map-restore").click();element(`map-restore-${mode}`).click();}};
const kinds={apply:"APPLY_MISSION_MAP",previous:"RESTORE_MISSION_MAP_PREVIOUS",original:"RESTORE_MISSION_MAP_ORIGINAL"};
const own=()=>[...lane.records.values()].filter(item=>item.operation_id.startsWith("own")).at(-1);
const loads=()=>editorCalls.filter(call=>call==="load").length;
// Load the plan, then let the first read of the follower pass
const start=async(current=value(plan()))=>{await load(current);await poll();check(lane.calls.some(call=>call[0]==="events"),"the follower read")};
'''


def page(body: str, *, budget: int = 40000, **options: object) -> str:
    """Run one editor harness with the fake lane before the body."""
    script = (LANE.replace("__TEXTS__", json.dumps(TEXTS)).replace("__READY__", json.dumps(READY))
              .replace("__PREVIOUS__", json.dumps(PREVIOUS)).replace("__ORIGINAL__", json.dumps(ORIGINAL)) + body)
    return run_editor_harness(script, budget=budget, **options)
