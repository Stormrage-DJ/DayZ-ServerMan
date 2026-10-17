// Autosave the draft shortly after each completed edit and show the save state (R15, Save Status and Close Dialogs).
"use strict";

// The save waits 800 ms after the last edit; a save that runs 2 s shows the delayed-save text.
const mapAutosaveTimes = Object.freeze({wait: 800, delayed: 2000});

// held: a pointer is down on the map; blocked: a refusal needs a reload; late: the delayed text showed;
// paused: the editor is closing, so the state is kept and shown again when the close is cancelled;
// reason: the bare reason of a shown save failure, for the close dialog (5.4).
const mapAutosaveState = {model: null, loadedText: null, timer: null, delayed: null, held: false, waiting: false,
  saved: false, failed: false, late: false, blocked: false, paused: false, text: "", notice: null, reason: "",
  start: 0};

// Draft changes: the backend value while the draft equals the saved snapshot; "updating" while a save waits or runs.
function mapAutosaveDraft(value) {
  const state = mapAutosaveState;
  if (!value.dirty) return value.draft_changes;
  return !state.blocked && (state.timer || state.waiting || window.ServerManMapPlanSave.busy()) ? "updating" : null;
}

// Every plan state goes through the layout writer; a close keeps it, and Reload plan, for a cancelled close.
function mapAutosaveShow(text, notice = null, announce = false, reason = "") {
  const state = mapAutosaveState;
  Object.assign(state, {text, notice, reason});
  if (state.paused) return;
  window.ServerManMapLayout.status(text, {notice, announce});
  document.getElementById("map-save-reload").hidden = !state.blocked;
  // Export plan shows before Reload plan while autosave is stopped, and Import shows why it waits (9.1)
  document.getElementById("map-plan-export").hidden = !state.blocked;
  window.ServerManMapTransfer.render();
  window.ServerManMapLayout.draftChanges(mapAutosaveDraft(state.model.status()));
  // The effective capacity texts follow the save state: Updating, Not evaluated or the backend values (5.1)
  window.ServerManMapAbundance.render();
}

// The delayed text shows 2 s after the save started, also when a cancelled close interrupted the timer (QA-5.2-F6).
function mapAutosaveDelay() {
  const state = mapAutosaveState, words = window.ServerManMapWording;
  clearTimeout(state.delayed);
  state.delayed = setTimeout(() => {
    state.late = true;
    mapAutosaveShow(words.savingDelayed, "info", true);
  }, Math.max(0, state.start + mapAutosaveTimes.delayed - Date.now()));
}

// Stop the 800 ms wait, also a save that waits for the end of a drag.
function mapAutosaveStop() {
  clearTimeout(mapAutosaveState.timer);
  mapAutosaveState.timer = null;
  mapAutosaveState.waiting = false;
}

// The timer saves only while no pointer is held on the map; the end of the drag starts the wait again.
function mapAutosaveSchedule() {
  const state = mapAutosaveState;
  mapAutosaveStop();
  state.timer = setTimeout(() => {
    state.timer = null;
    if (state.held) state.waiting = true;
    else window.ServerManMapPlanSave.run();
  }, mapAutosaveTimes.wait);
}

// A pointer down on the map can start a drag; window capture sees it before the map handlers consume it.
function mapAutosaveHold(event) {
  if (event.target instanceof Element && event.target.closest("#map-viewport")) mapAutosaveState.held = true;
}

// The end of a drag starts the wait again for a save that came due during the drag.
function mapAutosaveRelease() {
  const state = mapAutosaveState;
  state.held = false;
  if (state.waiting) mapAutosaveSchedule();
}

// Each model change decides the wait and the status; the backend answer alone says Saved (R15).
function mapAutosaveChanged(value) {
  const state = mapAutosaveState, words = window.ServerManMapWording, busy = window.ServerManMapPlanSave.busy();
  // A refusal that needs a reload keeps its text and its Reload action until the next load
  if (state.blocked || state.paused) return;
  if (value.dirty && value.plan && value.writable) {
    mapAutosaveSchedule();
    // A running save keeps "Saving" or the delayed text until its answer
    if (!busy) mapAutosaveShow(words.unsaved);
    return;
  }
  mapAutosaveStop();
  if (busy) return;
  if (value.dirty) {
    mapAutosaveShow(value.plan ? words.localUnsaved : words.localNoPlan);
    return;
  }
  // The draft equals the saved revision again, so marks of an older refusal no longer apply (QA-4.4-F6)
  window.ServerManMapPlanSave.clear();
  if (state.failed) mapAutosaveSaved();
  else mapAutosaveShow(...(state.saved ? [words.saved] : state.loadedText(value)));
}

// A save starts: the wait ends, and after 2 s the status says why it can take longer.
function mapAutosaveStarted() {
  const state = mapAutosaveState, words = window.ServerManMapWording;
  mapAutosaveStop();
  state.start = Date.now();
  mapAutosaveShow(words.saving);
  mapAutosaveDelay();
}

// The answer came; Saved shows only when the draft still equals what the backend accepted. The end of a
// sequence after a failure or after the delayed text is announced once (UI-UX 2026-10-13 00:01:25, item 2).
function mapAutosaveSaved() {
  const state = mapAutosaveState, words = window.ServerManMapWording;
  clearTimeout(state.delayed);
  if (state.model.status().dirty) {
    mapAutosaveShow(words.unsaved);
    return;
  }
  const announce = state.failed || state.late;
  Object.assign(state, {saved: true, failed: false, late: false});
  mapAutosaveShow(words.saved, null, announce);
}

// A refusal or failure shows its reason; a conflict, a changed association or an unknown result stops autosave.
function mapAutosaveFailed(text, reload = false, reason = "") {
  const state = mapAutosaveState;
  clearTimeout(state.delayed);
  Object.assign(state, {failed: true, late: false, failedAt: Date.now()});
  if (reload) {
    state.blocked = true;
    mapAutosaveStop();
  }
  mapAutosaveShow(text, "error", true, reason);
}

// A load starts a new saved state: no wait, no failure and no Reload action.
function mapAutosaveReset() {
  const state = mapAutosaveState;
  mapAutosaveStop();
  clearTimeout(state.delayed);
  Object.assign(state, {delayed: null, saved: false, failed: false, late: false, blocked: false, paused: false,
    text: "", notice: null, reason: ""});
  document.getElementById("map-save-reload").hidden = true;
  document.getElementById("map-plan-export").hidden = true;
}

// A cancelled close shows the current save state again: a running save, a refusal that needs a reload,
// or else the current draft, which also covers an answer that came during the close and starts a due wait.
// kept: Keep editing in this editor's close dialog keeps a shown failure, and the next edit saves (5.4).
function mapAutosaveRestore(value, kept = false) {
  const state = mapAutosaveState, words = window.ServerManMapWording;
  state.paused = false;
  if (state.blocked || (kept && state.reason)) mapAutosaveShow(state.text, state.notice, false, state.reason);
  else if (window.ServerManMapPlanSave.busy()) {
    mapAutosaveShow(state.late ? words.savingDelayed : words.saving, state.late ? "info" : null);
    if (!state.late) mapAutosaveDelay();
  }
  else mapAutosaveChanged(value);
  window.ServerManMapLayout.announce();
}

// Wire the drag guard and the Reload action once.
function mapAutosaveInit(model, loadedText) {
  const state = mapAutosaveState;
  if (state.model) return;
  state.model = model;
  state.loadedText = loadedText;
  const reload = document.getElementById("map-save-reload");
  reload.textContent = window.ServerManMapWording.reloadPlan;
  reload.addEventListener("click", () => window.ServerManMapEditor.load());
  window.addEventListener("pointerdown", mapAutosaveHold, true);
  // A click also ends the hold, because the button was released before it
  for (const type of ["pointerup", "pointercancel", "click"]) window.addEventListener(type, mapAutosaveRelease, true);
  window.addEventListener("blur", mapAutosaveRelease);
}

// The plan save runs the saves; this module owns the wait, the drag guard and the save status.
window.ServerManMapAutosave = Object.freeze({
  init: mapAutosaveInit, changed: mapAutosaveChanged, reset: mapAutosaveReset, schedule: mapAutosaveSchedule,
  started: mapAutosaveStarted, saved: mapAutosaveSaved, failed: mapAutosaveFailed, restore: mapAutosaveRestore,
  draft: mapAutosaveDraft, blocked: () => mapAutosaveState.blocked, reason: () => mapAutosaveState.reason,
  failedAt: () => mapAutosaveState.failedAt ?? 0,
  dropped: () => clearTimeout(mapAutosaveState.delayed),
  // A returned outline is announced; it is not a save failure, so the next Saved stays quiet
  returned: (text) => { clearTimeout(mapAutosaveState.delayed); mapAutosaveShow(text, null, true); },
  // A close stops the wait and the delayed text, and keeps the save state for a cancelled close
  pause: () => { mapAutosaveStop(); clearTimeout(mapAutosaveState.delayed); mapAutosaveState.paused = true; },
  refresh: () => mapAutosaveChanged(mapAutosaveState.model.status()),
});
