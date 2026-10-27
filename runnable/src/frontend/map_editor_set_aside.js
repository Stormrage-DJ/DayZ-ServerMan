// Offer "Set aside invalid plan" only for invalid saved data; confirm, then load the new empty plan (D2, leaf 5.3).
"use strict";

// value: the last load answer; enabled: false while the editor loads or closes; kept: a committed set aside
// whose load and report are still due ({name, from}); reloading: that load runs.
const mapSetAsideState = {model: null, value: null, running: false, enabled: true, kept: null, reloading: false};

// These refusals need a reload: the saved data or the association changed (Editor Bridge Methods).
const mapSetAsideReloadCodes = Object.freeze(["REVISION_CONFLICT", "PROFILE_CONTEXT_MISMATCH", "NOT_FOUND"]);

// The path-change states are read only too, but only adoption (task 10) ends them.
function mapSetAsideOffered(value) {
  return Boolean(value?.read_only && value.read_only_reason === "invalid_saved_data" && !value.path_change &&
    value.association?.mission_key);
}

// The action shows only for an invalid saved plan while the editor is neither loading nor closing (QA-5.3-F1).
function mapSetAsideShow() {
  const state = mapSetAsideState, button = document.getElementById("map-set-aside");
  button.hidden = !state.enabled || !mapSetAsideOffered(state.value);
  button.disabled = state.running;
}

// Load again after a committed set aside, then name the dated file also when that load fails (E07).
async function mapSetAsideReload() {
  const state = mapSetAsideState, kept = state.kept, words = window.ServerManMapWording;
  state.reloading = true;
  await window.ServerManMapEditor.load();
  state.reloading = false;
  // A load that a close skipped keeps the report for the release
  if (state.kept !== kept || state.value === kept.from) return;
  state.kept = null;
  const value = state.value, current = document.getElementById("map-status").textContent;
  const done = words.setAsideDone(kept.name);
  if (!value) window.ServerManMapLayout.status(`${done} ${current}`, {notice: "error", announce: true});
  else if (value.read_only) window.ServerManMapLayout.status(`${done} ${current}`, {notice: "info", announce: true});
  else {
    const next = value.plan ? words.setAsideOpen : words.boundsRequired;
    window.ServerManMapLayout.status(`${done} ${next}`, {notice: "success", announce: true});
    document.getElementById("map-viewport").focus();
  }
}

// A refusal shows its reason; a changed state offers Reload plan, a storage failure keeps the action.
function mapSetAsideFailed(error) {
  const state = mapSetAsideState, words = window.ServerManMapWording, details = error.result?.error?.details;
  const reload = mapSetAsideReloadCodes.includes(error.result?.error?.code);
  const text = details?.reason === "plan_not_invalid" ? words.setAsideNotInvalid :
    (reload ? words.setAsideReload(error.message) : words.notSetAside(error.message));
  if (reload) state.value = null;
  mapSetAsideShow();
  window.ServerManMapAutosave.failed(text, reload);
  document.getElementById(reload ? "map-save-reload" : "map-set-aside").focus();
}

// Confirm first. Answers bind to the load count: a load in between shows the actual state, and a close keeps a
// committed answer for its release, as the 5.2 close rules do (QA-5.3-F1).
async function mapSetAsideRun() {
  const state = mapSetAsideState, words = window.ServerManMapWording, value = state.value;
  if (state.running || !state.enabled || !mapSetAsideOffered(value)) return;
  const choice = await window.ServerManMapDom.dialog(words.setAsideTitle, words.setAsideMessage,
    [{label: words.setAsideCancel, value: "cancel"}, {label: words.setAsideConfirm, value: "set-aside", danger: true}]);
  if (choice !== "set-aside" || state.value !== value || !state.enabled) return;
  const loads = state.model.loads();
  state.running = true;
  mapSetAsideShow();
  window.ServerManMapLayout.status(words.settingAside, {announce: true});
  try {
    const name = (await window.ServerManMapBridge.setAside(value.association.mission_key))?.set_aside;
    state.running = false;
    if (loads !== state.model.loads()) return mapSetAsideShow();
    state.kept = {name: String(name), from: value};
    mapSetAsideShow();
    if (state.enabled) await mapSetAsideReload();
  } catch (error) {
    state.running = false;
    // A close or a newer load keeps its own status; nothing was committed
    if (loads !== state.model.loads() || !state.enabled) return mapSetAsideShow();
    mapSetAsideFailed(error);
  }
}

// Wire the action once; the editor reports each load answer and its enabled state.
window.ServerManMapSetAside = Object.freeze({
  init: (model) => {
    const button = document.getElementById("map-set-aside");
    mapSetAsideState.model = model;
    if (button.dataset.wired) return;
    button.dataset.wired = "true";
    button.textContent = window.ServerManMapWording.setAside;
    button.addEventListener("click", mapSetAsideRun);
  },
  load: (value) => { mapSetAsideState.value = value; mapSetAsideShow(); },
  // A cancelled close loads again when a set aside committed during it (E06)
  enabled: (enabled) => {
    const state = mapSetAsideState;
    state.enabled = enabled;
    mapSetAsideShow();
    if (enabled && state.kept && !state.running && !state.reloading) setTimeout(mapSetAsideReload, 0);
  },
});
