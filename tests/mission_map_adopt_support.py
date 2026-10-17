"""Fake adoption answers and page helpers of the 10.16 Edge tests: adoption from the editor window.

The answers come from the real text builder of the review (leaf 10.13), so the dialog shows the Product Owner
texts of the cases and routes. The fake lane and the page helpers are those of the 8.18 tests.
"""

from __future__ import annotations

import copy
import json

from dayz_serverman.domain import mission_map_adoption_texts as texts
from dayz_serverman.domain.mission_map_adoption import CAPTURE, COPY, MOVE, PATH_CHANGE, RESTORE, RETIRED
from tests.mission_map_publish_support import READY, page as publish_page

OLD = "mpmissions\\dayzOffline.old"
NEW = "D:\\DayZServer\\mpmissions\\dayzOffline.new"
OTHERS = [{"profile_id": "bravo", "display_name": "Bravo"}, {"profile_id": "gone", "display_name": None}]


def review(case: str, route: str | None, *, retired: tuple[str, ...] = (), others: bool = False, **options: object) -> dict:
    """Return one adoption answer that is not refused, with the texts of the case and route."""
    values = {"old_path": OLD, "new_path": NEW, "other_path": OLD, "history_name": "dayzOffline.new.retired", "count": 3}
    return {"version": 1, "refused": False, "refusals": [], "fingerprint": "d" * 64, "case": case, "route": route,
            "records_only": False, **texts.review_texts(case, route, values, list(retired), others=others, **options),
            "other_profiles": OTHERS if others else []}


def refused() -> dict:
    """Return the answer of a refused review: ADOPT-25 for a changed file, with the texts of the case."""
    message = texts.FILES_DIFFER_TEXT[COPY].format(path="cfgeventspawns.xml", old_path=OLD)
    answer = review(COPY, PATH_CHANGE, retired=(OLD,))
    return {**answer, "refused": True, "refusals": [{"reason": "adoption_refused", "file": "cfgeventspawns.xml",
                                                     "message": message}]}


def apply_refused(adoption: dict | None) -> dict:
    """Return a refused Apply answer; `adoption` is the `capture.adoption` field."""
    answer = copy.deepcopy(READY)
    capture = {**answer["capture"], "result": "refused", "reason": "editor_output", "adoption": adoption}
    return {**answer, "refused": True, "capture": capture, "refusals": [{
        "reason": "editor_output", "message": "Original capture refused: editor output from operation x1."}]}


# Independent copies of the Product Owner texts (evidence 10.1, 14:26:38 and 14:35:24)
TEXTS = {
    "label": "Adopt mission…", "busy": "Wait until the adoption preview is ready.",
    "reading": "Preparing the adoption preview", "title": "Adopt mission", "confirm": "Adopt",
    "stale": "The mission files or the map editor records can have changed after this preview. Preview again before "
             "you adopt.",
    "adopt4": "Read only: the mission path changed. The map plan shown belongs to an earlier path. Adopt the mission "
              "to use its map editor records for the new path.",
    "adopt5": "Read only: this mission returned to an earlier path. Adopt the mission to use its map editor records "
              "again.",
    "adopt6": "Apply is not possible: a restore removed the last Apply record of this profile for this mission. Adopt "
              "the mission to continue.",
    "term": "Map operation", "not_opened": "The adoption preview did not open because the map editor could not save the "
                                           "current draft.",
    "paused": "Editing and autosave are paused until the adoption ends. You can still move and zoom the map and select "
              "items. Closing the map editor does not stop the adoption.",
    "mismatch": "Not adopted: This profile now uses a different mission from the one in this map editor. Reload plan "
                "shows its current state.",
    "apply_refusal": "A restore removed the last Apply record of this profile for this mission. Adopt the mission "
                     "before you apply.",
    "copy_editable": "Adoption is needed: this mission is a copy of another mission path. Adopt the mission to "
                     "continue.",
    "kind_failure": "The mission map records could not be adopted.",
    "stale_commit": texts.ADOPTION_STALE_TEXT, "committed": texts.ADOPTION_COMMITTED_TEXT,
}
ANSWERS = {
    "move": review(MOVE, PATH_CHANGE, retired=(OLD,)),
    "copy": review(COPY, PATH_CHANGE, retired=(OLD,), others=True, existing=True),
    "capture": review(COPY, CAPTURE, others=True),
    "retired": review(RETIRED, None, retired=(OLD, OLD + "2", OLD + "3"), original_step="active"),
    "restore": review(RESTORE, None),
    "refused": refused(),
    "apply_refused": apply_refused({"case": "copy"}), "apply_refused_plain": apply_refused(None),
}

# The adoption answers of the fake API, the load values for each case, and the page helpers
ADOPT = r'''
const X=__X__,A=__A__,adoption={answer:null};
Object.assign(window.pywebview.api,{
  preview_adoption:async()=>{lane.calls.push(["preview","adopt"]);await held();return adoption.answer},
  adopt_mission:async(fingerprint)=>{lane.calls.push(["adopt",fingerprint]);return submit("ADOPT_MISSION_MAP")},
});
const reply=(name)=>{adoption.answer={success:true,value:structuredClone(A[name])}};
reply("move");
const reasons={move:"mission_path_changed",copy:"mission_path_changed",retired:"returned_to_earlier_path",restore:"adoption_required"};
// The load answer of each case (Architect 2026-10-16 14:33:32, UX-2); a restore leaves the plan editable
const offered=(kind)=>({...value(plan()),read_only:kind!=="restore",read_only_reason:reasons[kind],adoption:{case:kind},
  path_change:kind==="restore"?null:{shown_mission_key:"b".repeat(32),folders:[]}});
const control=()=>element("map-adopt"),status=()=>text("map-status");
const key=(target,name,shift=false)=>{target.focus();const event=new KeyboardEvent("keydown",{key:name,shiftKey:shift,bubbles:true,cancelable:true});
  target.dispatchEvent(event);return event.defaultPrevented};
const headings=()=>[...shown().querySelectorAll("h3")].map(heading=>heading.textContent);
const section=(name)=>[...shown().querySelectorAll("section")].find(part=>part.querySelector("h3").textContent===name);
const lines=(root)=>[...root.querySelectorAll("p, li")].map(item=>item.textContent);
const buttons=()=>[...shown().querySelectorAll(".action-row button")].map(button=>button.textContent);
const footer=()=>shown().querySelector(".map-preview-footer");
// Run an adoption to its end: the reviewed payload goes out, the lane runs and ends in the given state
const finish=async(state,extra={})=>{const queued=own();emit(record(queued.operation_id,queued.kind,"RUNNING"));await poll();
  return emit(record(queued.operation_id,queued.kind,state,extra))};
'''


def page(body: str, **options: object) -> str:
    """Run one editor harness with the fake lane, the adoption API and the answers before the body."""
    script = ADOPT.replace("__X__", json.dumps(TEXTS)).replace("__A__", json.dumps(ANSWERS))
    return publish_page(script + body, **options)
