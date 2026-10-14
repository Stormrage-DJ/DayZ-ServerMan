// Settle the editor document for a window or application close, and release it when the close is cancelled (3.9).
// Unsaved changes, typed bounds and an unfinished outline get one dialog (map_editor_close_dialog.js; R15, 5.4).
"use strict";

// editor, display, load: handed over by the app at start. generation: counts releases, so a released settle stops.
// pending: the running settle, which a second request shares. answer: "close" once given, until a release.
// reason: "editor" or "application". discarded: what a discard dropped ({plan, bounds, outline}), for a release.
const mapCloseState = {editor: null, display: null, load: null, generation: 0, pending: null, answer: null,
  reason: "editor", discarded: null};

// Freeze edits and pending loads until native destruction or release of the close request.
function mapCloseFreeze() {
  const editor = mapCloseState.editor;
  editor.closing = true;
  editor.model?.enabled(false);
  window.ServerManMapViewportUi.enabled(false);
  window.ServerManMapBounds.enabled(false);
  mapCloseState.display(false);
  window.ServerManMapBridge.invalidateLoads();
  window.ServerManMapAutosave.pause();
  window.ServerManMapLayout.status(window.ServerManMapWording.closing, {announce: true});
}

// A settle answer binds to its close and to the model load count: a release or a load ends it.
function mapCloseToken() {
  return {generation: mapCloseState.generation, loads: mapCloseState.editor.model?.loads()};
}

// The close and the load of a token are still the current ones.
function mapCloseCurrent(token) {
  return token.generation === mapCloseState.generation && token.loads === mapCloseState.editor.model?.loads();
}

// The draft holds changes that no save accepted; a read-only or empty draft holds none.
function mapCloseUnsaved() {
  const status = mapCloseState.editor.model?.status();
  return Boolean(status?.dirty && status.plan);
}

// Wait for a running plan save, and for a bounds save while changes or typed bounds are unsaved; false: the close ended.
async function mapCloseIdle(token) {
  while (mapCloseCurrent(token) && (window.ServerManMapPlanSave.busy() || ((mapCloseUnsaved() ||
      window.ServerManMapBounds.typed().changed) && window.ServerManMapBounds.busy()))) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return mapCloseCurrent(token);
}

// Save the unsaved draft at once, without the 800 ms wait. The first pass does not repeat a failed save and never
// saves typed bounds; the dialog offers that. A bounds save also saves the draft. A refusal that needs a reload
// cannot save.
async function mapCloseFlush(token, retry) {
  if (!(await mapCloseIdle(token))) return false;
  if (retry && window.ServerManMapBounds.typed().savable && !window.ServerManMapAutosave.blocked()) {
    await window.ServerManMapBounds.saveTyped();
    window.ServerManMapLayout.status(window.ServerManMapWording.closing);
    return mapCloseIdle(token);
  }
  if (!mapCloseUnsaved() || window.ServerManMapAutosave.blocked() || (!retry && window.ServerManMapAutosave.reason())) return true;
  await window.ServerManMapPlanSave.run(true);
  return mapCloseIdle(token);
}

// Save a pending edit, then close, or ask. "Save changes" tries again and asks again after a failure; when its
// choice also discarded the outline or bounds that need the review, a successful save closes. A discard sends
// nothing, so the saved plan stays. A committed save answer is adopted also when the close is cancelled.
async function mapCloseDecide() {
  const state = mapCloseState;
  mapCloseFreeze();
  state.discarded = null;
  const token = mapCloseToken();
  let retry = false, outlineDiscarded = false, boundsDiscarded = false;
  for (;;) {
    if (!(await mapCloseFlush(token, retry))) return "cancel";
    const plan = mapCloseUnsaved(), typed = window.ServerManMapBounds.typed(), points = window.ServerManMapTools.draft().length;
    if (!plan && (!typed.changed || boundsDiscarded) && (!points || outlineDiscarded)) {
      if (points || typed.changed) state.discarded = {bounds: typed.changed, outline: points > 0};
      break;
    }
    const {cause, blocked} = window.ServerManMapCloseDialog.cause(typed.changed), review = typed.changed && typed.review;
    const choice = await window.ServerManMapCloseDialog.ask({reason: state.reason, plan, cause, blocked, bounds: typed.changed,
      review, points, save: !blocked && (!typed.changed || typed.savable || (review && plan))});
    if (!mapCloseCurrent(token)) return "cancel";
    if (choice === "close") {
      state.discarded = {plan, bounds: typed.changed, outline: points > 0};
      break;
    }
    if (choice !== "save") {
      // Keep editing: this document opens again and keeps a shown failure; the host releases the other editors
      mapCloseRelease(true);
      return "cancel";
    }
    retry = true;
    outlineDiscarded = points > 0;
    boundsDiscarded = review;
  }
  state.answer = "close";
  return "close";
}

// Always a promise, also for an immediate "close" (A51-F2). A second request shares the running settle; an
// application close that joins it shows the application form of an open dialog. join: the host's join notice,
// which only changes a running settle and never starts one (QA-5.4-F7).
function mapCloseSettle(reason = "editor", join = false) {
  const state = mapCloseState;
  if (state.pending) {
    if (reason === "application") {
      state.reason = reason;
      window.ServerManMapCloseDialog.upgrade();
    }
    return state.pending;
  }
  if (join) return Promise.resolve(null);
  if (state.answer) return Promise.resolve(state.answer);
  state.reason = reason;
  state.pending = mapCloseDecide().finally(() => { state.pending = null; });
  return state.pending;
}

// A discard of changes is final: a cancelled close drops the outline and shows the saved plan, announced, without
// a notice (UI-UX 2026-10-14 00:10:31).
async function mapCloseShowSaved() {
  const editor = mapCloseState.editor;
  window.ServerManMapTools.restore([]);
  await mapCloseState.load();
  if (editor.loaded && !editor.closing) {
    window.ServerManMapLayout.status(window.ServerManMapCloseDialog.words.discarded, {announce: true});
  }
}

// A discard without plan changes drops only the typed bounds and the outline; the draft and its undo history stay
// (QA-5.4-F1). Returns the text that names what was dropped.
function mapCloseDrop(discarded) {
  const texts = [];
  if (discarded.bounds) {
    window.ServerManMapBounds.reset();
    texts.push(window.ServerManMapCloseDialog.words.boundsDiscarded);
  }
  if (discarded.outline) {
    window.ServerManMapTools.restore([]);
    texts.push(window.ServerManMapWording.outlineDiscarded);
  }
  return texts.join(" ");
}

// Restore the loaded status and backend comparison after a cancelled close. A document that is not closing,
// for example after its own "Keep editing", stays as it is. kept: "Keep editing" in this editor's own dialog.
function mapCloseRelease(kept = false) {
  const state = mapCloseState, editor = state.editor, discarded = state.discarded;
  if (!editor.closing) return;
  state.generation += 1;
  state.answer = null;
  state.discarded = null;
  editor.closing = false;
  if (discarded?.plan) return void mapCloseShowSaved();
  const dropped = discarded ? mapCloseDrop(discarded) : "";
  if (editor.loaded) {
    editor.loading = false;
    editor.model.enabled(true);
    window.ServerManMapViewportUi.enabled(true);
    state.display(true);
    // Pending answers stay invalidated; the retained snapshot is no longer loading.
    const current = {...editor.loaded, ...editor.model.status()};
    window.ServerManMapLayout.draftChanges(current.draft_changes);
    document.getElementById("map-editor").setAttribute("aria-busy", "false");
    window.ServerManMapAutosave.restore(current, kept);
    // Last, so a bounds failure held during the close is the status that the release shows (QA-5.2-F9)
    window.ServerManMapBounds.enabled(true);
  } else state.load();
  // The tool notice and the live region name what the discard dropped, as the tool's own Discard outline does
  if (dropped) window.ServerManMapTools.message(dropped);
}

// Reload guard: F5, Ctrl+R and Ctrl+P do nothing, and a page unload asks while work is unsaved and no close runs.
function mapCloseGuard() {
  document.addEventListener("keydown", (event) => {
    if (event.key === "F5" || (event.ctrlKey && ["r", "p"].includes(event.key.toLowerCase()))) event.preventDefault();
  }, true);
  window.addEventListener("beforeunload", (event) => {
    const editor = mapCloseState.editor;
    if (!editor || editor.closing || (!mapCloseUnsaved() && !window.ServerManMapBounds.typed().changed &&
        !window.ServerManMapTools.draft().length)) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

// The editor's host-facing settleForClose and releaseClose call these.
window.ServerManMapClose = Object.freeze({
  init: (editor, display, load) => { Object.assign(mapCloseState, {editor, display, load}); mapCloseGuard(); },
  settle: (reason, join) => mapCloseSettle(reason, join), release: () => mapCloseRelease(),
});
