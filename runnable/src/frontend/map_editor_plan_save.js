// Save the draft for autosave and at once after an outline Finish; a refusal of the new outline can return it.
"use strict";

// One save runs at a time and a request during it queues one more; outlines wait here until a save accepts them.
const mapPlanSaveState = {model: null, loads: null, running: false, again: false, outlines: new Map(), problems: new Set()};

// These refusals need a reload: the revision, the saved data or the association changed (Editor Bridge Methods).
const mapPlanSaveReload = Object.freeze(["REVISION_CONFLICT", "PROFILE_CONTEXT_MISMATCH", "NOT_FOUND"]);

// Adopt only actual returned metadata for the same association; the plan is not echoed.
function mapPlanSaveValid(result, plan) {
  return Number.isSafeInteger(result?.revision) && result.revision >= 0 && typeof result.draft_changes === "boolean" &&
    ["profile_id", "mission_root", "mission_key", "runtime_profile_key"].every((name) =>
      result.association?.[name] === plan.association[name]);
}

// The texts of the refusals that offer Reload plan name the loss of the draft (UI-UX 2026-10-13 00:01:25, item 4).
function mapPlanSaveReloadText(error, reason) {
  const words = window.ServerManMapWording;
  if (reason === "stale_revision") return words.reloadStale;
  return reason === "invalid_saved_data" ? words.reloadInvalid : words.reloadReason(error.message);
}

// The bare reason of such a refusal; the close dialog names it without the footer action (5.4).
function mapPlanSaveReloadReason(error, reason) {
  const words = window.ServerManMapWording;
  if (reason === "stale_revision") return words.savedPlanChanged;
  return reason === "invalid_saved_data" ? words.savedPlanInvalid : error.message;
}

// Return the status report of a refusal; it runs after the save has ended.
function mapPlanSaveRefused(error, submitted) {
  const state = mapPlanSaveState, words = window.ServerManMapWording, autosave = window.ServerManMapAutosave;
  const details = error.result?.error?.details ?? {};
  const problems = (details.problems ?? []).filter((problem) => problem?.object_id);
  const newest = [...state.outlines.values()].at(-1);
  const named = newest && problems.find((problem) => problem.object_id === newest.id);
  // Outline Finish: the newest outline returns only when it has no later edit and no newer outline exists
  if (named && !window.ServerManMapTools.draft().length && state.model.withdraw(newest)) {
    // The Finish step has left the history, so redo cannot bring the refused outline back
    state.outlines.delete(newest.id);
    window.ServerManMapTools.restore(newest.vertices);
    const text = words.outlineReturned(named.message);
    window.ServerManMapTools.message(text);
    state.again = true;
    return () => autosave.returned(text);
  }
  // An answer that cannot be confirmed may have saved, so it needs a reload like a conflict (QA-5.2-F4)
  if (error.unconfirmed || mapPlanSaveReload.includes(error.result?.error?.code)) {
    return () => autosave.failed(mapPlanSaveReloadText(error, details.reason), true, mapPlanSaveReloadReason(error, details.reason));
  }
  // A refusal names objects of the submitted draft; it applies only to the objects still in the draft (QA-4.4-F6)
  const current = state.model.snapshot()?.objects ?? [];
  const present = problems.filter((problem) => current.some((item) => item.id === problem.object_id));
  if (problems.length && !present.length) return autosave.refresh;
  // Mark each named object and show the reason with its current name; the next edit saves again
  state.problems = new Set(present.map((problem) => problem.object_id));
  const first = present[0], item = first && current.find((object) => object.id === first.object_id);
  const reason = item ? `${item.name}: ${first.message}` : error.message;
  return () => autosave.failed(words.notSaved(reason), false, reason);
}

// Submit a copy of the current draft with its revision. Only a load drops the late answer: after a close, also a
// cancelled one, the backend has committed it, so the window adopts it (QA-5.2-F1). A close saves at once while
// the edits stop (5.4); a read-only plan never saves.
async function mapPlanSaveRun(close = false) {
  const state = mapPlanSaveState, words = window.ServerManMapWording, model = state.model;
  const autosave = window.ServerManMapAutosave;
  if (state.running) {
    state.again = true;
    return;
  }
  const status = model.status(), loads = model.loads();
  const writable = status.writable || (close && !status.read_only && Boolean(status.association?.mission_key));
  if (!writable || !status.plan || !status.dirty || autosave.blocked()) return;
  // A running bounds save changes the revision, so this save waits for it
  if (window.ServerManMapBounds.busy()) {
    autosave.schedule();
    return;
  }
  const submitted = status.plan;
  state.running = true;
  autosave.started();
  let report = autosave.dropped;
  try {
    // A chosen image goes with its selection token, as in the bounds save
    const selection = window.ServerManMapBackground?.selection(submitted) ?? null;
    const result = await window.ServerManMapBridge.save(submitted, status.revision ?? null, selection);
    if (loads !== model.loads()) return;
    if (!mapPlanSaveValid(result, submitted)) throw Object.assign(new Error(words.saveUnconfirmed), {unconfirmed: true});
    state.problems.clear();
    for (const item of submitted.objects) state.outlines.delete(item.id);
    model.adopt(submitted, result);
    report = autosave.saved;
  } catch (error) {
    if (loads === model.loads()) report = mapPlanSaveRefused(error, submitted);
  } finally {
    // The status follows the end of the save, so a refresh sees no running save. A save queued during it runs
    // also after a load or close: run checks the then current draft (QA-5.2-F1, F2)
    state.running = false;
    const again = state.again;
    state.again = false;
    report();
    window.ServerManMapTools.refresh();
    if (again) mapPlanSaveRun();
  }
}

// Autosave calls run after its wait; Finish saves at once and keeps the outline for the refusal rule.
window.ServerManMapPlanSave = Object.freeze({
  init: (model) => { mapPlanSaveState.model = model; },
  run: mapPlanSaveRun,
  finish: (outline) => {
    const state = mapPlanSaveState;
    // A load since the last Finish ends the old outline and refusal marks
    if (state.loads !== state.model.loads()) {
      state.loads = state.model.loads();
      state.outlines.clear();
      state.problems.clear();
    }
    state.outlines.set(outline.id, structuredClone(outline));
    mapPlanSaveRun();
  },
  problems: () => mapPlanSaveState.problems,
  // The draft equals the saved revision again, so no refusal mark applies
  clear: () => { mapPlanSaveState.problems.clear(); },
  busy: () => mapPlanSaveState.running,
  // The bare reason of a refusal, also for the bounds save (UI-UX 2026-10-14 02:21:57)
  reason: (error) => mapPlanSaveReloadReason(error, error.result?.error?.details?.reason),
});
